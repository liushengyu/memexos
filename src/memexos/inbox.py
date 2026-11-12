"""The inbox: split the reports agents leave in inbox/ into single items, triage them, and act on them.

Writers put everything that needs judgment into the inbox, often hundreds of items a day; only a few need the
owner. If the owner has to read all of them, nobody reads any. So:

Items come from
  - inbox/*.md reports: every ## / ### section is an item (pure batch headings are not)
  - inbox/candidates/*/README.md: intake candidates the admission agent was unsure about (keep / drop)
  - pending categories from the classifier (approve / merge / reject)
  - inbox/*-blocked.md: compiles that were held back (records of reviews, fixes and inbox runs are not items)

Triage (a model, once per item) sorts them into
  decide  only the owner can settle it → a question with options and a recommendation, answered in the console
  agent   an agent can settle it → queued; needs_web: collect public sources first; needs_local: establish facts on this machine first
  system  a pipeline or tool problem for whoever maintains the system
  fyi     information only        done    already resolved

`memex inbox apply` (nightly, or from the console) then: collects public sources, runs local fact checks (written to
raw/feedback/ and applied by `memex correct`), carries out candidate and category decisions directly, and hands
the remaining decided and agent items to a writer in a staging worktree, through gate 1 and gate 2 like any compile.

State: <state>/inbox/state.jsonl (append-only: triaged, decided, undone, queued, applied, skipped, failed, resolved, …).
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from memexos import backends, events, i18n, judge, lock, rawfile, rawref
from memexos.capture.redact import known_secrets, scan
from memexos.util import append_jsonl, read_jsonl, self_cmd, split_frontmatter

KINDS = ("decide", "agent", "system", "fyi", "done")
RECORD = re.compile(r"-(review|fixes|inbox-log|blocked)\.md$")
RESULTS_FILE = "inbox/inbox-results.json"


def sdir(cfg) -> Path:
    return cfg.paths.state / "inbox"


def sid(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- items
def parse_report(path: Path) -> list[dict]:
    out, cur, parent, batch = [], None, "", ""
    for ln in path.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^(#{1,3}) +(.*)", ln)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                cur, parent = None, ""
                continue
            if level == 2:
                parent = title
            cur = {"level": level, "title": title, "parent": parent if level == 3 else "", "batch": batch, "body": []}
            out.append(cur)
            continue
        if cur is not None:
            cur["body"].append(ln)
    items = []
    for it in out:
        body = "\n".join(it["body"]).strip().strip("-").strip()
        if not body:
            continue
        title = f"{it['parent']} › {it['title']}" if it["parent"] else it["title"]
        items.append({"id": sid(path.name, title), "src": "report", "file": path.name, "date": path.name[:10], "title": title, "body": body,
                      "batch": it["batch"]})
    return items


def parse_candidates(cfg) -> list[dict]:
    items = []
    for readme in sorted((cfg.vault / "inbox" / "candidates").glob("*/README.md")):
        for ln in readme.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^- `([^`]+)`: (.*)", ln.strip())
            if not m:
                continue
            key, rest = m.group(1), m.group(2)
            url = (re.search(r"\((https?://[^)\s]+)\)\s*$", rest) or [None, ""])[1]
            reason = re.sub(r"\s*→.*$|\s*\(https?://[^)]+\)\s*$", "", rest).strip()
            items.append({"id": "cand:" + key, "src": "candidate", "file": readme.relative_to(cfg.vault).as_posix(), "date": readme.parent.name,
                          "title": i18n.t("inbox.cand_title", key=key), "body": reason, "key": key, "url": url, "batch": "",
                          "triage": {"kind": "decide", "why": i18n.t("inbox.cand_why"), "question": i18n.t("inbox.cand_question"),
                                     "context": i18n.t("inbox.cand_context", reason=reason, url=url or "-"),
                                     "options": [{"label": i18n.t("inbox.keep"), "effect": i18n.t("inbox.keep_effect")},
                                                 {"label": i18n.t("inbox.drop"), "effect": i18n.t("inbox.drop_effect")}],
                                     "recommend": -1, "recommend_why": "", "pages": []}})
    return items


def parse_blocked(cfg) -> list[dict]:
    items = []
    branches = set(subprocess.run(["git", "-C", str(cfg.vault), "branch", "--list", "staging/*", "--format=%(refname:short)"],
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split())
    for path in sorted((cfg.vault / "inbox").glob("*-blocked.md")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^## ([\d:]+) · `([^`]+)`\n(.*?)(?=^## |\Z)", text, re.M | re.S):
            branch, body = m.group(2), m.group(3).strip()
            alive = branch in branches
            items.append({"id": "blocked:" + branch, "src": "blocked", "file": path.name, "date": path.name[:10], "branch": branch,
                          "title": i18n.t("inbox.blocked_title", branch=branch), "body": body[:1500], "batch": "",
                          "triage": {"kind": "decide" if alive else "done",
                                     "why": i18n.t("inbox.blocked_why" if alive else "inbox.blocked_gone"),
                                     "question": i18n.t("inbox.blocked_question", branch=branch),
                                     "context": (re.search(r"^- (?:reason|原因)[:：]\s*(.*)$", body, re.M) or [None, ""])[1],
                                     "options": [{"label": i18n.t("inbox.discard"), "effect": i18n.t("inbox.discard_effect")},
                                                 {"label": i18n.t("inbox.leave"), "effect": i18n.t("inbox.leave_effect")}],
                                     "recommend": 0, "recommend_why": i18n.t("inbox.discard_why"), "pages": []}})
    return items


def all_items(cfg) -> list[dict]:
    items = []
    for path in sorted((cfg.vault / "inbox").glob("*.md")):
        if not RECORD.search(path.name):
            items += parse_report(path)
    items += parse_candidates(cfg) + parse_blocked(cfg)
    try:
        from memexos.catalog.classify import inbox_items
        items += inbox_items(cfg)
    except Exception:  # noqa: BLE001 — the inbox must still work when the catalog cannot be read
        pass
    seen, out = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return out


# ---------------------------------------------------------------- state
def append(cfg, ev: dict) -> dict:
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **ev}
    append_jsonl(sdir(cfg) / "state.jsonl", [ev])
    return ev


def fold(cfg) -> dict:
    st: dict = collections.defaultdict(dict)
    for e in read_jsonl(sdir(cfg) / "state.jsonl"):
        s, t = st[e["id"]], e["event"]
        if t == "triaged":
            s["triage"] = e["triage"]
        elif t == "decided":
            s.update(status="dismissed" if e.get("dismiss") else "decided", decision={k: e.get(k) for k in ("choice", "label", "text", "dismiss", "ts")})
            s.pop("result", None)
        elif t == "undone":
            s.update(status="open")
            s.pop("decision", None)
        elif t == "queued":
            s.update(status="queued", queued_ts=e["ts"])
        elif t == "dup":
            s["dup_of"] = e.get("of") or None
        elif t == "ranked":
            s["impact"] = {"level": e.get("level"), "score": e.get("score")}
        elif t == "researched":
            s["research"] = {"raws": e.get("raws", []), "note": e.get("note", ""), "ts": e["ts"]}
        elif t == "human_feedback":
            s["human_fb"] = e.get("path")
        elif t in ("applied", "skipped", "failed", "resolved"):
            s.update(status=t, result={"note": e.get("note", ""), "ts": e["ts"], "commit": e.get("commit")})
    return st


def merged(cfg) -> list[dict]:
    st = fold(cfg)
    out = []
    for it in all_items(cfg):
        s = st.get(it["id"], {})
        tri = s.get("triage") or it.get("triage")
        kind = tri["kind"] if tri else None
        out.append({**it, "triage": tri, "kind": kind, "status": s.get("status") or ("closed" if kind in ("fyi", "done") else "open"),
                    "decision": s.get("decision"), "result": s.get("result"), "human_fb": s.get("human_fb"), "research": s.get("research"),
                    "queued_ts": s.get("queued_ts"), "impact": s.get("impact"), "dup_of": s.get("dup_of")})
    return out


# ---------------------------------------------------------------- triage
TRIAGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False,
    "required": ["id", "kind", "why", "question", "context", "options", "recommend", "recommend_why", "agent_task", "needs_web", "needs_local", "pages"],
    "properties": {"id": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)}, "why": {"type": "string"},
                   "question": {"type": "string"}, "context": {"type": "string"},
                   "options": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["label", "effect"],
                                                          "properties": {"label": {"type": "string"}, "effect": {"type": "string"}}}},
                   "recommend": {"type": "integer"}, "recommend_why": {"type": "string"}, "agent_task": {"type": "string"},
                   "needs_web": {"type": "boolean"}, "needs_local": {"type": "boolean"}, "pages": {"type": "array", "items": {"type": "string"}}}}}}}


def _batches(items: list[dict], max_items: int = 12, max_chars: int = 14000):
    cur, size, key = [], 0, None
    for it in items:
        n = len(it["body"]) + len(it["title"]) + 80
        if cur and (len(cur) >= max_items or size + n > max_chars or it["file"] != key):
            yield cur
            cur, size = [], 0
        cur.append(it)
        size += n
        key = it["file"]
    if cur:
        yield cur


def triage(cfg, max_n: int = 0, workers: int = 4) -> int:
    st = fold(cfg)
    todo = sorted([it for it in all_items(cfg) if it["src"] == "report" and "triage" not in st.get(it["id"], {})],
                  key=lambda it: it["date"], reverse=True)
    todo = sorted(todo[:max_n] if max_n else todo, key=lambda it: it["file"])

    def one(group):
        body = "".join(f"\n=== id: {it['id']} ===\nfrom inbox/{it['file']} ({it['date']})\n### {it['title']}\n"
                       f"{it['body'] if len(it['body']) < 5000 else it['body'][:5000] + chr(10) + '…'}\n" for it in group)
        with tempfile.TemporaryDirectory(prefix="memex-triage-") as d:
            r = backends.structured(cfg, "worker", i18n.prompt("inbox_triage") + body, TRIAGE_SCHEMA, cwd=d, workdir=d, effort="medium",
                                    timeout=1800, job="inbox")
        if not r.ok:
            return 0
        want, got = {it["id"] for it in group}, 0
        for x in r.result["items"]:
            if x["id"] in want:
                append(cfg, {"id": x["id"], "event": "triaged", "by": r.model or r.backend,
                             "triage": {k: x[k] for k in TRIAGE_SCHEMA["properties"]["items"]["items"]["required"] if k != "id"}})
                got += 1
        return got

    done = 0
    with events.step(cfg.paths.state, "triage", items=len(todo)) as info:
        if todo:
            with cf.ThreadPoolExecutor(workers) as ex:
                for got in ex.map(one, list(_batches(todo))):
                    done += got
            dedupe(cfg)
        rank(cfg)
        info["done"] = done
    return done


def dedupe(cfg) -> int:
    todo = [it for it in merged(cfg) if it["kind"] == "decide" and it["status"] == "open" and not it.get("dup_of") and it["src"] == "report"]
    if len(todo) < 2:
        return 0
    sch = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {"groups": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["keep", "same"],
        "properties": {"keep": {"type": "string"}, "same": {"type": "array", "items": {"type": "string"}}}}}}}
    lines = "\n".join(f"{it['id']}｜{it['date']}｜{(it['triage'] or {}).get('question', '')}｜{(it['triage'] or {}).get('context', '')[:160]}"
                      for it in todo)
    with tempfile.TemporaryDirectory(prefix="memex-dedupe-") as d:
        r = backends.structured(cfg, "worker", i18n.t("inbox.dedupe_prompt") + "\n\n" + lines, sch, cwd=d, workdir=d, effort="medium",
                                timeout=1800, job="inbox")
    ids, n = {it["id"] for it in todo}, 0
    for g in (r.result or {}).get("groups", []) if r.ok else []:
        if g["keep"] in ids:
            for x in g["same"]:
                if x in ids and x != g["keep"]:
                    append(cfg, {"id": x, "event": "dup", "of": g["keep"]})
                    n += 1
    return n


def rank(cfg) -> int:
    """How much does it matter if this is never answered (decide) or never done (agent)? High items go first."""
    j = judge.get(cfg)
    if j.name == "none" or not j.available()[0]:
        return 0
    levels = {i18n.t("inbox.impact_high"): 1.0, i18n.t("inbox.impact_mid"): 0.5, i18n.t("inbox.impact_low"): 0.0}
    n = 0
    for it in [it for it in merged(cfg) if it["kind"] in ("decide", "agent") and it["status"] == "open" and not it.get("impact")]:
        t = it["triage"] or {}
        state = {"task": t.get("agent_task", ""), "report": it["body"][:1500]} if it["kind"] == "agent" else \
            {"question": t.get("question", ""), "context": t.get("context", ""), "options": [f"{o['label']}: {o['effect']}" for o in t.get("options") or []]}
        try:
            r = j.choice(state, {k: "" for k in levels}, i18n.t("inbox.impact_" + it["kind"]))
        except judge.JudgeUnavailable:
            break
        if not r:
            continue
        pr = r["probabilities"]
        score = round(sum(levels.get(k, 0) * float(v) for k, v in pr.items()), 3)
        append(cfg, {"id": it["id"], "event": "ranked", "level": r["choice"], "score": score})
        n += 1
    return n


# ---------------------------------------------------------------- the owner's decisions
def find(cfg, iid: str) -> dict | None:
    return next((it for it in merged(cfg) if it["id"] == iid), None)


def decide(cfg, iid: str, choice: int | None = None, text: str = "", dismiss: bool = False) -> tuple[bool, str]:
    it = find(cfg, iid)
    if not it:
        return False, i18n.t("inbox.no_item", id=iid)
    if it["status"] in ("queued", "applied", "skipped", "failed") and it.get("decision"):
        return False, i18n.t("inbox.already_running")
    ev = {"id": iid, "event": "decided", "dismiss": bool(dismiss), "text": (text or "").strip()[:2000]}
    if choice is not None:
        opts = (it["triage"] or {}).get("options") or []
        if not 0 <= int(choice) < len(opts):
            return False, i18n.t("inbox.no_option")
        ev.update(choice=int(choice), label=opts[int(choice)]["label"])
    elif not dismiss and not ev["text"]:
        return False, i18n.t("inbox.need_choice")
    append(cfg, ev)
    dups = [x for x in merged(cfg) if x.get("dup_of") == iid and x["status"] == "open"]
    for x in dups:
        append(cfg, {**ev, "id": x["id"], "text": (ev["text"] + "\n" if ev["text"] else "") + i18n.t("inbox.same_as", id=iid)})
    return True, i18n.t("inbox.dismissed" if dismiss else "inbox.decided") + (i18n.t("inbox.with_dups", n=len(dups)) if dups else "")


def undo(cfg, iid: str) -> tuple[bool, str]:
    it = find(cfg, iid)
    if not it or it["status"] not in ("decided", "dismissed"):
        return False, i18n.t("inbox.cannot_undo")
    append(cfg, {"id": iid, "event": "undone"})
    return True, i18n.t("inbox.undone")


# ---------------------------------------------------------------- acting
def queue(cfg, max_n: int) -> list[dict]:
    items = merged(cfg)
    stale = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 6 * 3600))
    for it in items:
        if it["status"] == "queued" and (it["queued_ts"] or "") < stale:
            it["status"] = "decided" if it.get("decision") else "open"
    human = sorted([it for it in items if it["status"] == "decided" and it["src"] == "report"], key=lambda it: it["decision"]["ts"])
    agent = [it for it in items if it["status"] == "open" and it["kind"] == "agent" and not (it["triage"] or {}).get("needs_local")
             and (not (it["triage"] or {}).get("needs_web") or (it["research"] and it["research"]["raws"]))]
    agent.sort(key=lambda it: (-((it.get("impact") or {}).get("score") or 0), it["date"]))
    return (human + agent)[:max_n]


def script_actions(cfg) -> set[str]:
    """Decisions that need no writer: intake candidates, category decisions, held branches. Returns files to commit."""
    from memexos.capture.add import add
    changed: set[str] = set()
    for it in [it for it in merged(cfg) if it["status"] == "decided" and it["src"] in ("candidate", "taxonomy", "blocked")]:
        d = it["decision"]
        label, note, ok = d.get("label") or "", "", True
        try:
            if it["src"] == "candidate":
                keep = label == i18n.t("inbox.keep") or (d.get("text") and not label)
                if keep:
                    if not it.get("url"):
                        raise RuntimeError(i18n.t("inbox.no_url"))
                    p = add(cfg, it["url"], via="inbox")
                    note = i18n.t("inbox.captured", path=p.name)
                else:
                    note = i18n.t("inbox.rejected_noted")
                append_jsonl(cfg.paths.ledger / "admission.jsonl", [{"key": it["key"], "decision": "admit" if keep else "reject",
                                                                     "reason": f"person (console){': ' + d['text'] if d.get('text') else ''}",
                                                                     "by": "person", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "raw": [], "candidates": []}])
                changed.add(".memex/admission.jsonl")
            elif it["src"] == "taxonomy":
                from memexos.catalog import classify as C
                opt = next((o for o in (it["triage"] or {}).get("options") or [] if o["label"] == label), None)
                if opt:
                    note, files = C.decide(cfg, it["node_id"], opt.get("action"), into=opt.get("into", ""), why=d.get("text") or "")
                elif d.get("text"):
                    n = C.Taxonomy(cfg).find(it["node_id"])
                    x = C.interpret(cfg, C.Taxonomy(cfg), n, d["text"])
                    if x["action"] == "unclear":
                        raise RuntimeError(x["why"])
                    note, files = C.decide(cfg, it["node_id"], x["action"], x["name"], x["into"], d["text"])
                else:
                    raise RuntimeError(f"unknown decision: {label}")
                changed.update(files)
            elif it["src"] == "blocked":
                if label == i18n.t("inbox.discard"):
                    subprocess.run(["git", "-C", str(cfg.vault), "branch", "-D", it["branch"]], capture_output=True, stdin=subprocess.DEVNULL)
                    note = i18n.t("inbox.branch_deleted")
                else:
                    note = i18n.t("inbox.left_as_is")
        except Exception as e:  # noqa: BLE001 — one failure must not stop the rest
            ok, note = False, str(e)[:300]
        append(cfg, {"id": it["id"], "event": "applied" if ok else "failed", "note": note, "run": "script"})
    return changed


def save_human_feedback(cfg, it: dict) -> str:
    """What the owner wrote in the inbox becomes a feedback file in raw/feedback/, so a page changed because of it
    can cite the owner's own words."""
    if it.get("human_fb") and (cfg.vault / it["human_fb"]).exists():
        return it["human_fb"]
    tri, d = it["triage"] or {}, it["decision"] or {}
    body = i18n.t("inbox.human_fb_body", date=(d.get("ts") or "")[:10], text=d.get("text", "").strip(),
                  question=tri.get("question") or it["title"], context=tri.get("context", ""))
    p = rawfile.write(cfg.paths.raw, "feedback", "human", tri.get("question") or it["title"],
                      {"pages": tri.get("pages") or ["-"], "claim": (tri.get("question") or it["title"])[:600], "inbox": it["id"],
                       "provided_by": "the owner (inbox)", "fetched_via": "inbox-decision", "route": "inbox-apply",
                       "published": (d.get("ts") or "")[:10]}, body, it["id"])
    rel = p.relative_to(cfg.vault).as_posix()
    append(cfg, {"id": it["id"], "event": "human_feedback", "path": rel})
    return rel


def apply_prompt(cfg, q: list[dict]) -> str:
    parts = []
    for it in q:
        tri = it["triage"] or {}
        if it["status"] == "decided":
            d = it["decision"]
            opt = next((o for o in tri.get("options") or [] if o["label"] == (d.get("label") or "")), None)
            fb = save_human_feedback(cfg, it) if (d.get("text") or "").strip() else ""
            what = (i18n.t("inbox.chose", label=opt["label"], effect=opt["effect"]) if opt else "") + \
                (i18n.t("inbox.added_text", text=d["text"]) if d.get("text") else "") + (i18n.t("inbox.fb_saved", path=fb) if fb else "")
            head = i18n.t("inbox.head_decided", question=tri.get("question") or it["title"], context=tri.get("context", ""), what=what)
        else:
            head = i18n.t("inbox.head_agent", task=tri.get("agent_task") or it["title"])
            if it.get("research"):
                head += i18n.t("inbox.research_raws", raws=", ".join(f"`{r}`" for r in it["research"]["raws"]), note=it["research"]["note"])
        body = it["body"] if len(it["body"]) < 4000 else it["body"][:4000] + "\n…"
        parts.append(f"=== id: {it['id']} ===\n{head}\n{i18n.t('inbox.original', file=it['file'])}\n### {it['title']}\n{it['batch']}\n{body}\n")
    return i18n.prompt("inbox_apply", n=len(q), date=datetime.date.today().isoformat(), results=RESULTS_FILE, items="\n".join(parts))


RESEARCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "raws", "note"],
    "properties": {"id": {"type": "string"}, "raws": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}}}}}}


def research(cfg, max_n: int, min_score: float = 0.4) -> int:
    todo = [it for it in merged(cfg) if it["status"] == "open" and it["kind"] == "agent" and (it["triage"] or {}).get("needs_web")
            and not it.get("research") and ((it.get("impact") or {}).get("score") or 0) >= min_score]
    todo = sorted(todo, key=lambda it: (-((it.get("impact") or {}).get("score") or 0), it["date"]))[:max_n]
    if not todo:
        return 0
    me = self_cmd()
    parts = [f"=== id: {it['id']} ===\n{(it['triage'] or {}).get('agent_task', '')}\n### {it['title']}\n{it['body'][:2500]}\n" for it in todo]
    work = sdir(cfg) / "research" / time.strftime("%Y%m%d-%H%M%S")
    before = {p for p in cfg.paths.raw.glob("*")}
    with events.step(cfg.paths.state, "research", items=len(todo)) as info:
        r = backends.agent(cfg, "worker", i18n.prompt("inbox_research", memex=" ".join(me), items="\n".join(parts)), cwd=cfg.vault,
                           workdir=work, policy=backends.Policy(commands=[me + ["add-url"]], web=True, network=True), effort="medium",
                           timeout=3600, schema=RESEARCH_SCHEMA, job="inbox")
        info.update(ok=r.ok, cost_usd=r.cost_usd)
    new = {p.relative_to(cfg.vault).as_posix() for p in set(cfg.paths.raw.glob("*")) - before}
    want, n = {it["id"] for it in todo}, 0
    for x in (r.result or {}).get("items", []) if r.ok and isinstance(r.result, dict) else []:
        if x.get("id") in want:
            raws = [p for p in x.get("raws", []) if p in new or (cfg.vault / p).exists()]
            append(cfg, {"id": x["id"], "event": "researched", "raws": raws, "note": x.get("note", "")[:500]})
            n += 1
    return n


INVESTIGATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "status", "record", "pages", "claim", "visual_raws", "note"],
    "properties": {"id": {"type": "string"}, "status": {"type": "string", "enum": ["done", "deferred", "impossible"]},
                   "record": {"type": "string"}, "pages": {"type": "array", "items": {"type": "string"}}, "claim": {"type": "string"},
                   "visual_raws": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}}}}}}
READ_ONLY_COMMANDS = [["wc"], ["ls"], ["head"], ["tail"], ["stat"], ["shasum"], ["cmp"], ["diff"], ["jq"], ["git", "-C"]]


def investigate(cfg, max_n: int, min_score: float) -> int:
    """Items whose answer is on this machine: a read-only agent establishes the facts; the scrubbed record goes to raw/feedback/."""
    from memexos.intake.admit import DENY_READ
    todo = [it for it in merged(cfg) if it["status"] == "open" and it["kind"] == "agent" and (it["triage"] or {}).get("needs_local")
            and not it.get("research") and ((it.get("impact") or {}).get("score") or 0) >= min_score]
    todo = sorted(todo, key=lambda it: (-((it.get("impact") or {}).get("score") or 0), it["date"]))[:max_n]
    if not todo:
        return 0
    roots = [Path(os.path.expanduser(r)) for r in cfg.get("privacy.investigate_roots") or []]
    me = self_cmd()
    commands = READ_ONLY_COMMANDS + [me + ["add-visual"]]
    work = sdir(cfg) / "investigate" / time.strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    parts = [f"=== id: {it['id']} ===\n{(it['triage'] or {}).get('agent_task', '')}\npages: {', '.join((it['triage'] or {}).get('pages') or []) or '-'}\n"
             f"### {it['title']}\n{it['body'][:3000]}\n" for it in todo]
    prompt = i18n.prompt("inbox_investigate", vault=cfg.vault, roots=", ".join(map(str, roots)) or "-",
                         commands=", ".join(" ".join(c) for c in READ_ONLY_COMMANDS), visual=i18n.t("inbox.visual_hint", memex=" ".join(me)),
                         today=datetime.date.today().isoformat(), items="\n".join(parts))
    pol = backends.Policy(write=[work], read=[cfg.vault, *roots], commands=commands, deny_read=DENY_READ)
    with events.step(cfg.paths.state, "investigate", items=len(todo)) as info:
        r = backends.agent(cfg, "worker", prompt, cwd=work, workdir=work / ".run", policy=pol, effort="high", timeout=5400,
                           schema=INVESTIGATE_SCHEMA, job="inbox")
        info.update(ok=r.ok, cost_usd=r.cost_usd)
    secrets = known_secrets(cfg)
    want, n = {it["id"]: it for it in todo}, 0
    for x in (r.result or {}).get("items", []) if r.ok and isinstance(r.result, dict) else []:
        it = want.get(x.get("id"))
        if not it or x.get("status") != "done" or not x.get("record"):
            continue
        p = work / Path(x["record"]).name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if not text or scan(text, secrets):
            events.emit(cfg.paths.state, "inbox.record_blocked", id=it["id"], path=str(p))
            continue
        pages = [q for q in x.get("pages") or (it["triage"] or {}).get("pages") or [] if q]
        claim = (x.get("claim") or "").strip()
        if not pages or not claim:
            continue
        title = (re.search(r"^#\s+(.+)$", text, re.M) or [None, it["title"]])[1].strip()
        dest = rawfile.write(cfg.paths.raw, "feedback", "local-check", title,
                             {"pages": pages, "claim": claim[:600], "inbox": it["id"], "provided_by": "local check (read-only)",
                              "fetched_via": "local-check", "published": datetime.date.today().isoformat()}, text, it["id"])
        append(cfg, {"id": it["id"], "event": "researched", "raws": [dest.relative_to(cfg.vault).as_posix()] + x.get("visual_raws", []),
                     "note": ("local check: " + x.get("note", ""))[:500]})
        n += 1
    return n


def feedback_done(cfg, results, commit: str) -> int:
    """After `memex correct` published: mark the inbox items behind the applied feedback."""
    n = 0
    for r in results if isinstance(results, list) else []:
        rel = str(r.get("feedback", ""))
        if not rel or not (cfg.vault / rel).exists():
            continue
        fm, _ = split_frontmatter((cfg.vault / rel).read_text(encoding="utf-8"))
        if fm.get("inbox"):
            stt = r.get("status") if r.get("status") in ("applied", "skipped", "failed") else "failed"
            append(cfg, {"id": fm["inbox"], "event": stt, "note": str(r.get("note", ""))[:500], "run": "correct", "commit": commit})
            n += 1
    return n


def apply(cfg) -> int:
    """The nightly inbox run (also the console's "apply now")."""
    from memexos.catalog.classify import catalog_sync
    from memexos.pipeline import compile as C
    from memexos.pipeline.stage import Stage, git
    if C.paused(cfg):
        return 0
    research(cfg, int(cfg.get("inbox.research_max") or 8))
    investigate(cfg, int(cfg.get("inbox.investigate_max") or 6), float(cfg.get("inbox.investigate_min_score") or 0.75))
    C.correct(cfg)
    with lock.held(cfg.paths.locks, "write", wait=5400):
        changed = script_actions(cfg)
        if changed:
            git(cfg.vault, "add", "-A", "raw", *sorted(changed), check=False)
            git(cfg.vault, "commit", "-q", "-m", "inbox: decisions on candidates and categories", check=False)
    if any(c.endswith("catalog.jsonl") for c in changed):
        catalog_sync(cfg)
    q = queue(cfg, int(cfg.get("inbox.apply_max") or 8))
    if not q:
        return 0
    with lock.held(cfg.paths.locks, "write", wait=5400):
        prompt = apply_prompt(cfg, q)
        for it in q:
            append(cfg, {"id": it["id"], "event": "queued"})
        stage = Stage(cfg).open()
        w = C.Writer(cfg, stage, "inbox")

        def requeue():
            for it in q:
                if it.get("decision"):
                    append(cfg, {"id": it["id"], "event": "decided", **{k: it["decision"].get(k) for k in ("choice", "label", "text", "dismiss")}})
                else:
                    append(cfg, {"id": it["id"], "event": "undone"})

        c = w.write(prompt, "lint")
        if c != 0:
            requeue()
            (stage.hold(i18n.t("pipeline.gate1_failed", rounds=2), [], w.report) if c == 2 else stage.discard(w.report))
            return 1
        rf = stage.dir / RESULTS_FILE
        try:
            got = {x["id"]: x for x in json.loads(rf.read_text(encoding="utf-8")) if isinstance(x, dict)}
        except (OSError, ValueError):
            got = {}
        rf.unlink(missing_ok=True)
        rows = [{"id": it["id"], "status": (got.get(it["id"]) or {}).get("status") if (got.get(it["id"]) or {}).get("status") in ("applied", "skipped", "failed") else "failed",
                 "note": str((got.get(it["id"]) or {}).get("note", i18n.t("inbox.no_result")))[:500]} for it in q]
        append_jsonl(stage.dir / ".memex" / "inbox-log.jsonl", [{**r, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")} for r in rows])
        changed_wiki = subprocess.run(["git", "-C", str(stage.dir), "diff", "--quiet", stage.base, "--", "wiki", "index.md"],
                                      stdin=subprocess.DEVNULL).returncode != 0
        dirs: list[Path] = []
        if changed_wiki:
            names = rawref.index(stage.dir)
            raws = C._cited_raws(stage) + [r for it in q for r in rawref.find(it["body"], names, stage.dir)]
            outcome, dirs, rep = C.review_loop(cfg, stage, w, list(dict.fromkeys(r for r in raws if not r.startswith("raw/assets/"))), "fix",
                                               "fix_passed", "inbox", fix_prompt_key="rework_again", what=i18n.t("inbox.what"))
            if outcome in ("unavailable", "writer-failed"):    # temporary: the items go back to the queue
                requeue()
                stage.discard(i18n.t("pipeline.gate2_unavailable") if outcome == "unavailable" else w.report)
                return 1
            if outcome not in ("passed", "off"):
                requeue()
                stage.hold(i18n.t("inbox.review_failed"), [], json.dumps(rep.get("errors"), ensure_ascii=False, indent=1))
                return 1
        from memexos.pipeline import fixqueue
        from memexos.pipeline.stage import add_to_ledger
        add_to_ledger(stage.dir, "fixed-reviews.txt", fixqueue.verified(dirs))    # the last round's minor problems wait for `memex fix`
        if not stage.publish(f"inbox: {len(q)} items"):
            requeue()
            return 1
        commit = git(cfg.vault, "rev-parse", "--short", "HEAD").strip()
        for r in rows:
            append(cfg, {"id": r["id"], "event": r["status"], "note": r["note"], "commit": commit})
    return 0


# ---------------------------------------------------------------- CLI
def cmd_inbox(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex inbox")
    ap.add_argument("action", nargs="?", default="stats",
                    choices=["stats", "list", "triage", "decide", "apply", "resolve", "retriage", "rank"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--choice", type=int)
    ap.add_argument("--text", default="")
    ap.add_argument("--dismiss", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--kind", choices=list(KINDS))
    ap.add_argument("--why", default="")
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args(argv)
    cfg = loader()
    if a.action in ("stats", "list"):
        items = merged(cfg)
        if a.action == "list":
            if a.json:
                print(json.dumps(items, ensure_ascii=False))
            else:
                for it in items:
                    print(f"{it['id']:24} {it['kind'] or '-':7} {it['status']:9} {((it['triage'] or {}).get('question') or it['title'])[:80]}")
        else:
            for (k, s), n in sorted(collections.Counter((it["kind"] or "-", it["status"]) for it in items).items()):
                print(f"{k:8} {s:10} {n}")
        return 0
    if a.action in ("triage", "rank", "apply"):          # model work: a run on the timeline; bookkeeping is not
        with events.run(cfg.paths.state, "inbox"):
            return _write_action(cfg, a)
    return _write_action(cfg, a)


def _write_action(cfg, a) -> int:
    if a.action == "triage":
        print(i18n.t("inbox.triaged", n=triage(cfg, a.max)))
    elif a.action == "rank":
        print(rank(cfg))
    elif a.action == "decide":
        ok, msg = undo(cfg, a.ids[0]) if a.undo else decide(cfg, a.ids[0], a.choice, a.text, a.dismiss)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    elif a.action == "apply":
        return apply(cfg)
    elif a.action == "resolve":
        for iid in a.ids:
            append(cfg, {"id": iid, "event": "resolved", "note": a.note[:1000], "commit": a.commit})
    elif a.action == "retriage":
        for iid in a.ids:
            it = find(cfg, iid)
            if it:
                tri = dict(it.get("triage") or {})
                tri.update(kind=a.kind, why=a.why or tri.get("why", ""))
                append(cfg, {"id": iid, "event": "triaged", "triage": tri, "by": "maintainer"})
    return 0
