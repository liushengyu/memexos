"""How the knowledge base is used, and the feedback loop from use back into fixes.

Why conversation logs and not a log written by `memex search` itself: agents often run in read-only sandboxes
where nothing can be written, so a self-written log would miss them. Claude Code and Codex already keep complete
session logs on this machine (the question, every memex command and its output, the final answer), and intake
reads them anyway. They are the single source of truth; this module only extracts the knowledge-base parts.

    memex usage [--days 7] [--all]     questions, searches, most-read pages, unanswered questions, untouched pages, feedback
    memex feedback <page> --kind incorrect|outdated|missing --why "…" [--evidence "…"]
    memex usage judge [--max 40]       did the knowledge base help in real questions? (worker model, read-only)
    memex usage verify [--max 10]      check reported problems against the sources; confirmed ones become fix jobs for `memex fix`

Everything stays in the local state directory (never committed, never sent anywhere except to the configured
models during judge/verify). It includes the people's questions verbatim, because they are the best test set there is.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path

from memexos import backends, i18n
from memexos import events as event_log
from memexos.intake import sessions as S
from memexos.util import append_jsonl, atomic_write, read_jsonl

SCAN_DAYS = 35
KINDS = ("incorrect", "outdated", "missing")
VERDICTS = ("helped", "partly", "not_helped", "honest_gap", "unclear")
HIT_LINE = re.compile(r"^\s*\d+\. \[[^\]]*\] .*?\s\(([^()\s][^()]*\.md)\)\s*$")
NOT_FOUND = re.compile(r"not in the (?:knowledge base|kb)|knowledge base (?:has|contains) nothing|库里没有|知识库里没有|库中没有|库里没找到", re.I)
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
PAGE_REF = re.compile(r"(?:wiki|raw)/[^\s)\]」”，。、`*|]+?\.md")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
JS_CMD = re.compile(r"""cmd\s*:\s*(['"`])((?:\\.|(?!\1).)*)\1""", re.S)


def dirs(cfg) -> Path:
    return cfg.paths.state / "usage"


def vault_file_re(cfg) -> re.Pattern:
    """Paths into this vault as agents write them: absolute, or ~/… ."""
    v = str(cfg.vault)
    home = str(Path.home().resolve())
    forms = [re.escape(v)] + ([re.escape("~" + v[len(home):])] if v.startswith(home) else [])
    return re.compile(r"(?:" + "|".join(forms) + r")/((?:wiki|raw)/[^\n'\"`|;&]+?\.md)")


def command_text(name, args) -> str:
    if isinstance(args, str) and "exec_command" in args and "cmd" in args:
        return "\n".join(re.sub(r"\\(['\"`\\])", r"\1", m.group(2)) for m in JS_CMD.finditer(args))
    if isinstance(args, dict):
        c = args.get("command") or args.get("cmd")
    else:
        try:
            j = json.loads(args) if isinstance(args, str) and args.strip().startswith("{") else {}
        except ValueError:
            j = {}
        c = j.get("command") or j.get("cmd") or (args if isinstance(args, str) else "")
    if isinstance(c, list):
        c = c[2] if len(c) >= 3 and c[1] in ("-lc", "-c") else " ".join(map(str, c))
    return c or ""


def memex_calls(cmd: str) -> list[dict]:
    """memex search/get/feedback calls in command position (not inside quotes or here-documents)."""
    calls, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        i += 1
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            toks = list(lex)
        except ValueError:
            toks = line.split()
        seg, skip = [], False
        for t in toks + [";"]:
            if skip:
                skip = False
                continue
            if t and set(t) <= set("<>&") and set(t) & set("<>"):
                if seg and seg[-1].isdigit():
                    seg.pop()
                skip = True
                continue
            if t and set(t) <= set(";&|()`"):
                while seg and (re.match(r"^\w+=", seg[0]) or seg[0] in ("env", "time", "exec", "command")):
                    seg.pop(0)
                if len(seg) >= 3 and re.search(r"python[\d.]*$", seg[0]) and seg[1] == "-m" and seg[2] == "memexos":
                    seg = ["memex"] + seg[3:]
                if len(seg) >= 2 and re.search(r"(^|/)memex$", seg[0]) and seg[1] in ("search", "get", "feedback"):
                    calls.append(parse_call(seg[1], seg[2:]))
                seg = []
            else:
                seg.append(t)
        m = HEREDOC.search(line)
        if m:
            while i < len(lines) and lines[i].strip() != m.group(1):
                i += 1
            i += 1
    return [c for c in calls if c["op"] != "feedback" or c.get("kind") in KINDS]


def parse_call(op: str, argv: list[str]) -> dict:
    call = {"op": op}
    if op == "search":
        pos, kw, i = [], None, 0
        while i < len(argv):
            if argv[i] == "--kw" and i + 1 < len(argv):
                kw = argv[i + 1]
                i += 2
            elif argv[i] in ("-n", "--mode") and i + 1 < len(argv):
                i += 2
            elif argv[i].startswith("-"):
                i += 1
            else:
                pos.append(argv[i])
                i += 1
        call.update(query=" ".join(pos)[:500], kw=kw)
    elif op == "get":
        pos = [x for x in argv if not x.startswith("-") and not x.isdigit()]
        call["page"] = pos[0] if pos else ""
    else:
        page, kind, why, i = None, None, None, 0
        while i < len(argv):
            if argv[i] == "--kind" and i + 1 < len(argv):
                kind = argv[i + 1]
                i += 2
            elif argv[i] in ("--why", "--evidence") and i + 1 < len(argv):
                if argv[i] == "--why":
                    why = argv[i + 1]
                i += 2
            elif not argv[i].startswith("-") and page is None:
                page = argv[i]
                i += 1
            else:
                i += 1
        call.update(page=page, kind=kind, why=(why or "")[:1000])
    return call


def parse_result(call: dict, out: str) -> dict:
    out = out or ""
    if "\\n" in out and out.count("\n") < 3:
        out = out.replace("\\\\n", "\n").replace("\\n", "\n")
    if call["op"] == "search":
        hits = []
        s = out.strip()
        if s.startswith("{"):
            try:
                hits = [h["path"] for h in json.loads(s).get("hits", [])]
            except (ValueError, KeyError, TypeError):
                pass
        call["results"] = (hits or [m.group(1) for m in map(HIT_LINE.match, out.splitlines()) if m])[:20]
    elif call["op"] == "get":
        m = next((re.match(r"^# (\S.*?\.md)", l) for l in out.splitlines()[:3] if re.match(r"^# (\S.*?\.md)", l)), None)
        call["opened"] = m.group(1) if m else None
        call["found"] = bool(m)
    return call


def turn_events(cfg, src: str, sid: str, meta: dict, turns: list):
    vf = vault_file_re(cfg)
    cwd = meta.get("cwd") or ""
    kind = "eval" if "memex-eval" in cwd else "real"
    last_q = ""
    for ti, t in enumerate(turns):
        q = t["user"] or ""
        follow = q.startswith(("[background task notification]", "[summary carried over", "(start of session)"))
        if not follow:
            last_q = q
        calls, pending = [], []
        for typ, val in t["items"]:
            if typ == "tool":
                name, args = val
                cmd = command_text(name, args)
                found = memex_calls(cmd)
                if isinstance(args, dict) and name == "Read":
                    reads = [m.group(1) for m in [vf.search(str(args.get("file_path") or ""))] if m]
                elif not found:
                    reads = vf.findall(cmd)
                else:
                    reads = []
                found += [{"op": "read", "opened": r, "found": True} for r in dict.fromkeys(reads)]
                pending.append(found)
                calls.extend(found)
            elif typ == "result" and pending:
                for c in pending.pop(0):
                    parse_result(c, val)
        if not calls:
            continue
        says = [v for typ, v in t["items"] if typ == "say"]
        answer = says[-1] if says else ""
        yield {"key": f"{src}:{sid}:{ti}", "agent": src, "session": sid, "turn": ti, "cwd": S.tilde(cwd), "kind": kind,
               "session_last": meta.get("last"), "question": last_q[:2000], "follow_up": follow, "calls": calls,
               "answer": answer[:4000], "cited": sorted(set(PAGE_REF.findall(answer)) | {f"[[{x.strip()}]]" for x in WIKILINK.findall(answer)}),
               "said_not_found": bool(NOT_FOUND.search(answer))}


def extract(cfg) -> tuple[int, int, int]:
    d = dirs(cfg)
    events_file = d / "events.jsonl"
    cutoff = time.time() - SCAN_DAYS * 86400
    old = {e["key"]: e for e in read_jsonl(events_file) if "key" in e}
    new, scanned = {}, 0
    needles = (b"memex search", b"memex get", b"memex feedback", b"memexos search", b"memexos get", str(cfg.vault).encode())
    mine = S.self_paths(cfg)
    for src, files, turns_fn in (("claude", S.claude_files(cfg), S.claude_turns), ("codex", S.codex_files(cfg), S.codex_turns)):
        for f in files:
            try:
                if f.stat().st_mtime < cutoff or not any(n in f.read_bytes() for n in needles):
                    continue
            except OSError:
                continue
            turns, meta = turns_fn(f)
            if any((meta.get("cwd") or "").startswith(m) for m in mine):
                continue
            scanned += 1
            sid = f.stem if src == "claude" else ((S.codex_meta(f) or {}).get("id") or f.stem)
            for e in turn_events(cfg, src, sid, meta, turns):
                new[e["key"]] = e
    edge = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(cutoff))
    old = {k: e for k, e in old.items() if (e.get("session_last") or "") < edge}
    old.update(new)
    atomic_write(events_file, "".join(json.dumps(e, ensure_ascii=False) + "\n"
                                      for e in sorted(old.values(), key=lambda e: (e.get("session_last") or "", e["key"]))))
    return scanned, len(new), len(old)


# ---------------------------------------------------------------- feedback
def fid_of(f: dict) -> str:
    return hashlib.sha1(f"{f.get('page')}\n{(f.get('why') or '')[:200]}".encode()).hexdigest()[:10]


def all_feedback(cfg, events: list[dict]) -> list[dict]:
    d = dirs(cfg)
    out = read_jsonl(d / "feedback.jsonl")
    seen = {(f.get("page"), (f.get("why") or "")[:200]) for f in out}
    for e in events:                 # reports made in read-only sandboxes survive only in the session log
        for c in e["calls"]:
            if c["op"] == "feedback" and (c.get("page"), (c.get("why") or "")[:200]) not in seen:
                seen.add((c.get("page"), (c.get("why") or "")[:200]))
                out.append({"ts": e.get("session_last"), "page": c.get("page"), "kind": c.get("kind"), "why": c.get("why"),
                            "agent": e["agent"], "session": e["session"], "status": "pending", "from": "session", "question": e.get("question", "")})
    for r in read_jsonl(d / "source-errors.jsonl"):
        k = (r["page"], (r.get("why") or "")[:200])
        if k not in seen:
            seen.add(k)
            out.append({"ts": r.get("ts"), "page": r["page"], "kind": "source_error", "why": r.get("why", ""), "agent": "usage-judge",
                        "status": "pending", "from": "judge"})
    st = {r["fid"]: r for r in read_jsonl(d / "feedback-status.jsonl") if "fid" in r}
    for f in out:
        f["fid"] = fid_of(f)
        if f["fid"] in st:
            f["status"] = st[f["fid"]]["status"]
            f["summary"] = st[f["fid"]].get("summary", "")
    return out


def cmd_feedback(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex feedback")
    ap.add_argument("page")
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--why", required=True)
    ap.add_argument("--evidence", default="")
    a = ap.parse_args(argv)
    cfg = loader()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "page": a.page, "kind": a.kind, "why": a.why, "evidence": a.evidence,
           "session": os.environ.get("CLAUDE_CODE_SESSION_ID", ""), "cwd": S.tilde(os.getcwd()), "status": "pending"}
    try:
        append_jsonl(dirs(cfg) / "feedback.jsonl", [rec])
        print(i18n.t("usage.feedback_saved", page=a.page))
    except OSError:
        print(i18n.t("usage.feedback_readonly", page=a.page))
    return 0


VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdict", "summary", "problems", "raws"],
    "properties": {"verdict": {"type": "string", "enum": ["confirmed", "rejected", "unsure"]}, "summary": {"type": "string"},
                   "raws": {"type": "array", "items": {"type": "string"}},
                   "problems": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["kind", "page", "wiki_text", "source_text", "raw_location", "reason"],
                       "properties": {"kind": {"type": "string", "enum": ["CONTRADICTED", "UNSUPPORTED", "PARTIAL", "MISSED"]},
                                      "page": {"type": "string"}, "wiki_text": {"type": "string"}, "source_text": {"type": "string"},
                                      "raw_location": {"type": "string"}, "reason": {"type": "string"}}}}}}


def to_fix_job(cfg, f: dict, res: dict) -> Path:
    """A confirmed report → a review directory in the same format as gate 2, which `memex fix` picks up."""
    d = cfg.paths.state / "gate2" / f"{time.strftime('%Y-%m-%d')}-feedback_{f['fid']}"
    d.mkdir(parents=True, exist_ok=True)
    raws = [r for r in res["raws"] if r.startswith("raw/")]
    claims, points = [], []
    for p in res["problems"]:
        if p["kind"] == "MISSED":
            points.append({"point": p["source_text"] or p["reason"], "importance": "must", "covered": False, "where": p["page"]})
        else:
            claims.append({"page": p["page"], "claim": p["wiki_text"], "verdict": p["kind"], "critical": p["kind"] == "CONTRADICTED",
                           "evidence": p["source_text"], "raw_location": p["raw_location"], "reason": f"{p['reason']} ({res['summary'][:200]})"})
    atomic_write(d / "review.json", json.dumps({"claims": claims, "key_points": points, "overwrites": [], "injections": [],
                                                "integration_notes": [], "notes_on_eval": f"feedback {f['fid']}: {res['summary']}"},
                                               ensure_ascii=False, indent=1))
    atomic_write(d / "report.json", json.dumps({"raws": raws, "mode": "feedback", "passed": False}, ensure_ascii=False))
    return d


def verify(cfg, max_n: int = 10) -> int:
    extract(cfg)
    events = read_jsonl(dirs(cfg) / "events.jsonl")
    todo = [f for f in all_feedback(cfg, events) if f.get("status") == "pending"][:max_n]
    n = 0
    for f in todo:
        if f.get("kind") == "source_error":
            prompt = i18n.prompt("usage_source_error", page=f.get("page"), why=f.get("why"))
        else:
            q = (f.get("question") or "").strip()
            prompt = i18n.prompt("usage_verify", page=f.get("page"), kind=f.get("kind"), why=f.get("why"), evidence=f.get("evidence") or "-",
                                 question=i18n.t("usage.asked", q=q[:600]) if q else "")
        with event_log.step(cfg.paths.state, "verify", page=f.get("page")) as info:
            r = backends.structured(cfg, "worker", prompt, VERIFY_SCHEMA, cwd=cfg.vault, workdir=dirs(cfg) / "verify" / f["fid"],
                                    effort="high", timeout=1800, job="usage")
            info.update(ok=r.ok, result=(r.result or {}).get("verdict") if r.ok else "error")
        if not r.ok:
            continue
        res = r.result
        rec = {"fid": f["fid"], "status": res["verdict"], "summary": res["summary"], "by": r.model or r.backend, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if res["verdict"] == "confirmed" and res["problems"]:
            rec["fix_dir"] = str(to_fix_job(cfg, f, res))
            rec["status"] = "to_fix"
        append_jsonl(dirs(cfg) / "feedback-status.jsonl", [rec])
        n += 1
    return n


JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["n", "verdict", "reason", "bad_pages"],
    "properties": {"n": {"type": "integer"}, "verdict": {"type": "string", "enum": list(VERDICTS)}, "reason": {"type": "string"},
                   "bad_pages": {"type": "array", "items": {"type": "string"}}}}}}}


def judge_usage(cfg, max_n: int = 40) -> int:
    extract(cfg)
    d = dirs(cfg)
    done = {r["key"] for r in read_jsonl(d / "judgments.jsonl") if "key" in r}
    events = read_jsonl(d / "events.jsonl")
    todo = [e for e in events if e["kind"] == "real" and e["key"] not in done and e.get("answer") and not e.get("follow_up")][:max_n]
    n = 0
    for s in range(0, len(todo), 10):
        batch = todo[s:s + 10]
        recs = []
        for i, e in enumerate(batch, 1):
            opened = [c.get("opened") or c.get("page") for c in e["calls"] if c["op"] in ("get", "read")]
            searched = [c.get("query") for c in e["calls"] if c["op"] == "search"]
            recs.append(f"### {i}\n- question: {e['question'][:1500]}\n- searched: {'; '.join(x for x in searched if x) or '-'}\n"
                        f"- opened: {'; '.join(x for x in opened if x) or '-'}\n- final answer (excerpt): {e['answer'][:2500]}\n")
        with event_log.step(cfg.paths.state, "usage", questions=len(batch)) as info:
            r = backends.structured(cfg, "worker", i18n.prompt("usage_judge", records="\n".join(recs)), JUDGE_SCHEMA, cwd=cfg.vault,
                                    workdir=d / "judge" / time.strftime("%Y%m%d-%H%M%S"), effort="medium", timeout=1800, job="usage")
            info["ok"] = r.ok
        if not r.ok:
            continue
        rows, fb, srcerr = [], [], []
        for it in r.result["items"]:
            if not 1 <= it["n"] <= len(batch):
                continue
            e = batch[it["n"] - 1]
            rows.append({"key": e["key"], "verdict": it["verdict"], "reason": it["reason"], "bad_pages": it["bad_pages"],
                         "by": r.model or r.backend, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            for pg in it["bad_pages"]:
                rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "page": pg, "why": it["reason"], "from_event": e["key"]}
                (srcerr if pg.startswith("raw/") else fb).append(rec if pg.startswith("raw/") else {**rec, "kind": "incorrect", "evidence": "",
                                                                                                    "agent": "usage-judge", "status": "pending"})
        append_jsonl(d / "judgments.jsonl", rows)
        append_jsonl(d / "feedback.jsonl", fb)
        append_jsonl(d / "source-errors.jsonl", srcerr)
        n += len(rows)
    return n


# ---------------------------------------------------------------- reporting
def norm_page(cfg, p: str | None) -> str:
    p = (p or "").strip()
    if p.startswith("[["):
        p = p[2:-2]
    if not p.endswith(".md"):
        hits = list((cfg.vault / "wiki").rglob(f"{p}.md"))
        return hits[0].relative_to(cfg.vault).as_posix() if hits else p
    return p


def summary(cfg, days: int = 7, include_eval: bool = False, refresh: bool = True) -> dict:
    scanned, n_new, n_all = extract(cfg) if refresh else (0, 0, 0)
    d = dirs(cfg)
    events = read_jsonl(d / "events.jsonl")
    since = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - days * 86400))
    recent = [e for e in events if (e.get("session_last") or "") >= since and (include_eval or e["kind"] == "real")]
    opened = collections.Counter(norm_page(cfg, c.get("opened") or c.get("page")) for e in recent for c in e["calls"]
                                 if c["op"] in ("get", "read") and c.get("found", True))
    month_edge = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() - 30 * 86400))
    month = [e for e in events if (e.get("session_last") or "") >= month_edge and e["kind"] == "real"]
    touched = {norm_page(cfg, c.get("opened") or c.get("page")) for e in month for c in e["calls"] if c["op"] in ("get", "read")} | \
              {p for e in month for c in e["calls"] if c["op"] == "search" for p in c.get("results", [])[:3]}
    wiki = sorted(p.relative_to(cfg.vault).as_posix() for p in (cfg.vault / "wiki").rglob("*.md"))
    jd = {r["key"]: r for r in read_jsonl(d / "judgments.jsonl") if "key" in r}
    judged = [jd[e["key"]] for e in recent if e["key"] in jd]
    cnt = collections.Counter(r["verdict"] for r in judged)
    clear = [r for r in judged if r["verdict"] != "unclear"]
    for e in recent:
        e["verdict"] = jd.get(e["key"], {}).get("verdict")
        e["verdict_reason"] = jd.get(e["key"], {}).get("reason", "")
    return {"days": days, "scanned": scanned, "n_new": n_new, "n_all": n_all, "n_recent": len(recent),
            "by_agent": collections.Counter(e["agent"] for e in recent).most_common(),
            "projects": collections.Counter(Path(e["cwd"].rstrip("/")).name or "?" for e in recent).most_common(),
            "n_search": sum(c["op"] == "search" for e in recent for c in e["calls"]), "n_open": sum(opened.values()),
            "opened": opened.most_common(30),
            "n_no_open": sum(1 for e in recent if any(c["op"] == "search" for c in e["calls"]) and not any(c["op"] in ("get", "read") for c in e["calls"])),
            "gaps": [{"question": e["question"][:300], "agent": e["agent"]} for e in recent if e["said_not_found"]],
            "wiki_total": len(wiki), "touched_30d": len(set(wiki) & touched), "never_30d": len([p for p in wiki if p not in touched]),
            "judged": len(judged), "verdicts": cnt.most_common(),
            "helped_rate": round((cnt["helped"] + cnt["honest_gap"]) / len(clear), 3) if clear else None,
            "feedback": all_feedback(cfg, events),
            "per_day": sorted(collections.Counter((e.get("session_last") or "")[:10] for e in recent).items()),
            "events": [{k: e.get(k) for k in ("key", "agent", "cwd", "session_last", "question", "calls", "answer", "cited", "said_not_found",
                                               "verdict", "verdict_reason", "kind")} for e in sorted(recent, key=lambda e: e.get("session_last") or "", reverse=True)[:200]]}


def report_text(cfg, days: int = 7, include_eval: bool = False) -> str:
    d = summary(cfg, days, include_eval)
    L = [i18n.t("usage.head", days=days, n=d["n_recent"], searches=d["n_search"], opens=d["n_open"], no_open=d["n_no_open"])]
    if d["opened"]:
        L.append(i18n.t("usage.most_read"))
        L += [f"  {n}  {p}" for p, n in d["opened"][:10]]
    L.append(i18n.t("usage.gaps", n=len(d["gaps"])))
    L += [f"  - {g['question'][:100].replace(chr(10), ' ')} ({g['agent']})" for g in d["gaps"][:15]]
    if d["judged"]:
        L.append(i18n.t("usage.judged", n=d["judged"], verdicts=", ".join(f"{i18n.t('usage.v_' + k)} {v}" for k, v in d["verdicts"]),
                        rate=f"{d['helped_rate']:.0%}" if d["helped_rate"] is not None else "-"))
    L.append(i18n.t("usage.untouched", total=d["wiki_total"], touched=d["touched_30d"], never=d["never_30d"]))
    fb = d["feedback"]
    L.append(i18n.t("usage.feedback_head", n=len(fb)))
    L += [f"  - [{f.get('status')}] {f.get('page')} {f.get('kind')}: {(f.get('why') or '')[:120]}" for f in fb[-15:]]
    return "\n".join(L)


def cmd_usage(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex usage")
    ap.add_argument("action", nargs="?", default="report", choices=["report", "extract", "judge", "verify"])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max", type=int)
    a = ap.parse_args(argv)
    cfg = loader()
    if a.action == "extract":
        print(i18n.t("usage.extracted", **dict(zip(("scanned", "new", "total"), extract(cfg)))))
    elif a.action == "judge":
        from memexos import events
        with event_log.run(cfg.paths.state, "usage"):
            print(i18n.t("usage.judge_done", n=judge_usage(cfg, a.max or 40)))
    elif a.action == "verify":
        from memexos import events
        with event_log.run(cfg.paths.state, "usage"):
            print(i18n.t("usage.verify_done", n=verify(cfg, a.max or 10)))
    else:
        print(report_text(cfg, a.days, a.all))
    return 0
