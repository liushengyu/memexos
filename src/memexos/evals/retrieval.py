"""Retrieval evaluation, the read side (docs/evals.md).

    memex eval retrieval mine            questions the owner asked in past agent sessions (candidates for `real`)
    memex eval retrieval gen [--n 40]    write questions by quota (fact / synth / usage / negative / real)
    memex eval retrieval cover           one question for each wiki page that has none yet, then verify (nightly)
    memex eval retrieval usage [--n 10]  "correct use" questions only
    memex eval retrieval verify [RUN]    an independent run checks every candidate; the kept ones join the bank
    memex eval retrieval l1              every search mode on every question: hit@1/3/10, recall@10, MRR, time
    memex eval retrieval pool            judge the pooled top results page by page (qrels), so other correct pages count
    memex eval retrieval l2 [--n 20]     agents answer using only the command line interface; another model grades
    memex eval retrieval report [RUN]    rewrite the reports of a run
    memex eval retrieval status          the bank and the latest results

The bank (questions.jsonl) and the page judgments (qrels.jsonl) live in the vault's .memex/evals/retrieval/ and are
versioned with it; runs go to <state>/evals/retrieval/<time>/. Writing, checking and judging questions use the
`evals.role` models (the reviewer by default, so the writer does not grade itself); L1 runs locally and costs nothing.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import secrets
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from memexos import backends, events, i18n, lock
from memexos.util import append_jsonl, atomic_write, read_jsonl, self_cmd

TYPES = ("fact", "synth", "usage", "negative", "real")
QUOTA = {"fact": .35, "synth": .20, "usage": .15, "negative": .10}
# questions in a session: a question mark, or a question word in English or Chinese
QUESTIONISH = re.compile(r"[?？]|^(what|why|how|which|who|when|where|is|are|does|do|can|should)\b|什么|怎么|为什么|为何|如何|哪些|哪个|是不是|"
                         r"能不能|有没有|吗[。！!]?$|多少|区别|对比|原理", re.I)
NOISE = re.compile(r"^(continue|go on|ok|okay|yes|no|thanks|继续|好的?|可以|用中文|\[|/|<)|^.{0,5}$", re.I)
VERDICTS = ("correct", "partial", "wrong", "honest_gap", "false_gap", "invented")

ITEM = {"type": "object", "additionalProperties": False,
        "required": ["type", "question", "gold_pages", "gold_raw", "key_facts", "answerable", "use_rule", "domain", "why"],
        "properties": {"type": {"type": "string", "enum": list(TYPES)}, "question": {"type": "string"},
                       "gold_pages": {"type": "array", "items": {"type": "string"}},
                       "gold_raw": {"type": "array", "items": {"type": "string"}},
                       "key_facts": {"type": "array", "items": {"type": "string"}},
                       "answerable": {"type": "boolean"}, "use_rule": {"type": "string"},
                       "domain": {"type": "string"}, "why": {"type": "string"}}}
ITEMS = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": ITEM}}}
CHECKED = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "keep", "reason", "gold_pages", "key_facts"],
    "properties": {"id": {"type": "string"}, "keep": {"type": "boolean"}, "reason": {"type": "string"},
                   "gold_pages": {"type": "array", "items": {"type": "string"}},
                   "key_facts": {"type": "array", "items": {"type": "string"}}}}}}}
POOLED = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["id", "page", "label", "reason"],
    "properties": {"id": {"type": "string"}, "page": {"type": "string"},
                   "label": {"type": "string", "enum": ["answers", "partial", "no"]}, "reason": {"type": "string"}}}}}}
GRADED = {"type": "object", "additionalProperties": False, "required": ["results"], "properties": {"results": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["key", "verdict", "facts_hit", "citation_ok", "usage_ok", "reason"],
    "properties": {"key": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
                   "facts_hit": {"type": "array", "items": {"type": "boolean"}}, "citation_ok": {"type": "boolean"},
                   "usage_ok": {"type": ["boolean", "null"]}, "reason": {"type": "string"}}}}}}


# ---------------------------------------------------------------- places
def bank_path(cfg) -> Path:
    return cfg.paths.ledger / "evals" / "retrieval" / "questions.jsonl"


def qrels_path(cfg) -> Path:
    return cfg.paths.ledger / "evals" / "retrieval" / "qrels.jsonl"


def runs_root(cfg) -> Path:
    return cfg.paths.state / "evals" / "retrieval"


def new_run(cfg) -> Path:
    base = runs_root(cfg) / time.strftime("%Y%m%d-%H%M%S")
    base.parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, 100):
        d = base if i == 1 else base.with_name(f"{base.name}-{i}")
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"cannot create a run directory under {base.parent}")


def latest_run(cfg, having: str | None = None) -> Path | None:
    root = runs_root(cfg)
    runs = sorted(d for d in root.iterdir() if d.is_dir()) if root.is_dir() else []
    runs = [d for d in runs if not having or (d / having).exists()]
    return runs[-1] if runs else None


def say(cfg, key: str, **kw) -> None:
    """Progress goes to the event log (status view, console) and to stderr."""
    events.emit(cfg.paths.state, "eval.progress", job="eval", text=i18n.t(f"eval.{key}", **kw))
    print(i18n.t(f"eval.{key}", **kw), file=sys.stderr)


def model_call(cfg, prompt: str, schema: dict, work: Path, effort: str = "high", timeout: float = 5400, step: str = "eval_write") -> dict:
    role = cfg.get("evals.role") or "reviewer"
    with events.step(cfg.paths.state, step) as info:
        r = backends.structured(cfg, role, prompt, schema, cwd=cfg.vault, workdir=work, effort=effort, timeout=timeout, job="eval")
        info.update(ok=r.ok, model=r.model, cost_usd=r.cost_usd)
    if not r.ok or not isinstance(r.result, dict):
        raise RuntimeError(f"{r.backend}: {r.tail[-300:] or 'no result'} ({work})")
    return r.result


def commit_bank(cfg, message: str) -> None:
    """The bank is part of the vault: commit it (under the write lock, so it never races a publish)."""
    from memexos.pipeline.stage import ensure_identity, git
    try:
        with lock.held(cfg.paths.locks, "write", wait=1800):
            ensure_identity(cfg.vault)
            git(cfg.vault, "add", "--", ".memex/evals")
            git(cfg.vault, "commit", "-q", "-m", message, "--", ".memex/evals", check=False)
    except lock.Busy:
        pass                                  # committed with the next one


# ---------------------------------------------------------------- the bank
def bank(cfg) -> list[dict]:
    """Active questions; a question whose gold page was renamed or merged is `stale` and not scored until updated."""
    out = []
    for it in read_jsonl(bank_path(cfg)):
        if it.get("status") != "active":
            continue
        it["stale"] = bool(it.get("answerable")) and any(not (cfg.vault / p).exists() for p in it.get("gold_pages", []))
        out.append(it)
    return out


def load_qrels(cfg) -> dict[tuple[str, str], str]:
    """{(question, page): answers | partial | no}; the latest judgment of a pair wins."""
    return {(r["id"], r["page"]): r["label"] for r in read_jsonl(qrels_path(cfg)) if "id" in r and "page" in r}


def relevant(it: dict, qrels: dict) -> set[str]:
    """The pages that count as right: the gold standard, minus gold pages judged `no`, plus other pages judged `answers`.
    (Gold pages of a synthesis question each hold only part of the answer, so `partial` keeps them.)"""
    gold = set(it.get("gold_pages", [])) | set(it.get("gold_raw", []))
    judged = {p: lab for (qid, p), lab in qrels.items() if qid == it["id"]}
    return {p for p, lab in judged.items() if lab == "answers"} | {p for p in gold if judged.get(p) != "no"}


def domain_counts(cfg) -> str:
    try:
        from memexos.catalog.classify import load
        from memexos.catalog.taxonomy import Taxonomy
    except ImportError:
        return "-"
    c = collections.Counter(Taxonomy.top(r.get("domain")) for r in load(cfg).values() if r.get("domain"))
    return "\n".join(f"- {d}: {n}" for d, n in c.most_common(40)) or "-"


# ---------------------------------------------------------------- writing questions
def mine(cfg) -> Path:
    from memexos.intake import sessions as S
    own = S.self_paths(cfg)
    out, seen = [], set()
    files = [("claude", f) for f in S.claude_files(cfg)] + [("codex", f) for f in S.codex_files(cfg)]
    for kind, f in files:
        if kind == "codex":
            m = S.codex_meta(f) or {}
            if m.get("source") not in (None, "cli") or m.get("thread_source", "user") != "user":
                continue
            turns, meta = S.codex_turns(f)
        else:
            turns, meta = S.claude_turns(f)
        cwd = meta.get("cwd") or ""
        if any(cwd.startswith(p) for p in own):
            continue                                   # MemexOS's own unattended runs are not the owner asking
        for t in turns:
            u = t["user"].strip()
            if NOISE.search(u) or len(u) > 400 or not QUESTIONISH.search(u):
                continue
            key = re.sub(r"\W+", "", u.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            out.append({"q": u, "when": (meta.get("last") or "")[:10], "agent": kind})
    d = new_run(cfg)
    append_jsonl(d / "mined.jsonl", out)
    say(cfg, "mined", n=len(out), sessions=len(files))
    return d


def _write_candidates(d: Path, name: str, items: list[dict], prefix: str) -> int:
    rows = [{"id": f"{prefix}{datetime.date.today():%y%m%d}-{secrets.token_hex(3)}", **it} for it in items]
    append_jsonl(d / name, rows)
    return len(rows)


def gen(cfg, n: int = 40) -> Path:
    d = latest_run(cfg, "mined.jsonl") or mine(cfg)
    quota = {k: round(n * v) for k, v in QUOTA.items()}
    quota["real"] = n - sum(quota.values())
    work = d / "gen"
    work.mkdir(exist_ok=True)
    prompt = i18n.prompt("eval_gen", spec=i18n.prompt("eval_spec"), n=n, quota=json.dumps(quota, ensure_ascii=False),
                         mined=d / "mined.jsonl", domains=domain_counts(cfg))
    res = model_call(cfg, prompt, ITEMS, work)
    k = _write_candidates(d, "candidates.jsonl", res["items"], "g")
    say(cfg, "generated", n=k, run=d.name)
    return d


def usage_questions(cfg, n: int = 10) -> Path:
    d = new_run(cfg)
    work = d / "usage"
    work.mkdir()
    res = model_call(cfg, i18n.prompt("eval_usage", spec=i18n.prompt("eval_spec"), n=n), ITEMS, work)
    k = _write_candidates(d, "candidates.jsonl", [{**it, "type": "usage"} for it in res["items"]], "u")
    say(cfg, "generated", n=k, run=d.name)
    return d


def uncovered(cfg) -> list[str]:
    covered = {p for q in read_jsonl(bank_path(cfg)) for p in q.get("gold_pages", [])}
    root = runs_root(cfg)
    if root.is_dir():                          # written but not verified yet also counts
        for f in root.glob("*/candidates.jsonl"):
            covered |= {p for q in read_jsonl(f) for p in q.get("gold_pages", [])}
        for f in root.glob("*/dropped.jsonl"):
            covered |= {p for q in read_jsonl(f) for p in q.get("gold_pages", [])[:1]}
    pages = sorted(p.relative_to(cfg.vault).as_posix() for p in (cfg.vault / "wiki").rglob("*.md"))
    return [p for p in pages if p not in covered]


def cover(cfg, limit: int | None = None, batch: int = 10, check: bool = True) -> int:
    """One question for each page without one (a batch per night), then verify. Returns the questions added."""
    limit = limit if limit is not None else int(cfg.get("evals.cover_per_night") or 20)
    pages = uncovered(cfg)[:limit] if limit else uncovered(cfg)
    if not pages:
        return 0
    d = new_run(cfg)
    say(cfg, "covering", n=len(pages))
    n = 0
    for b in range(0, len(pages), batch):
        part = pages[b:b + batch]
        docs = "\n\n".join(f"### {p}\n{(cfg.vault / p).read_text(encoding='utf-8', errors='replace')[:7000]}" for p in part)
        work = d / f"cover-{b // batch + 1:03d}"
        work.mkdir()
        try:
            res = model_call(cfg, i18n.prompt("eval_cover", spec=i18n.prompt("eval_spec"), n=len(part), docs=docs), ITEMS, work)
        except RuntimeError as e:
            say(cfg, "batch_failed", what="cover", i=b // batch + 1, why=str(e)[:200])
            continue
        n += _write_candidates(d, "candidates.jsonl", res["items"], "c")
    return verify(cfg, d) if check and n else 0


def verify(cfg, d: Path | None = None, batch: int = 25) -> int:
    d = d or latest_run(cfg, "candidates.jsonl")
    if not d:
        return 0
    done = {q["id"] for q in read_jsonl(bank_path(cfg))} | {q["id"] for q in read_jsonl(d / "dropped.jsonl")}
    items = [it for it in read_jsonl(d / "candidates.jsonl") if it["id"] not in done]       # resumable after a failure
    kept, dropped = [], []
    for b in range(0, len(items), batch):
        part = items[b:b + batch]
        work = d / f"verify-{b // batch + 1:02d}-{time.strftime('%H%M%S')}"
        work.mkdir()
        try:
            res = model_call(cfg, i18n.prompt("eval_verify", items="\n".join(json.dumps(it, ensure_ascii=False) for it in part)), CHECKED, work,
                             step="eval_check")
        except RuntimeError as e:
            say(cfg, "batch_failed", what="verify", i=b // batch + 1, why=str(e)[:200])
            continue
        by = {r["id"]: r for r in res["results"]}
        for it in part:
            r = by.get(it["id"])
            if r and r["keep"] and all((cfg.vault / p).exists() for p in r["gold_pages"] if it.get("answerable")):
                kept.append({**it, "gold_pages": r["gold_pages"], "key_facts": r["key_facts"], "verified": r["reason"],
                             "status": "active", "added": str(datetime.date.today())})
            else:
                dropped.append({**it, "drop_reason": r["reason"] if r else "no verdict"})
    if kept:
        bank_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(bank_path(cfg), kept)
        commit_bank(cfg, f"eval: {len(kept)} retrieval questions added to the bank")
    append_jsonl(d / "dropped.jsonl", dropped)
    say(cfg, "verified", kept=len(kept), dropped=len(dropped))
    return len(kept)


# ---------------------------------------------------------------- L1: the search tool
def l1_modes(cfg) -> list[str]:
    from memexos.search import engine
    return ["keyword", "fast", "auto"] + (["vector", "deep"] if engine.backend(cfg) == "qmd" else [])


def l1(cfg, modes: list[str] | None = None, limit: int | None = None) -> Path:
    modes = modes or l1_modes(cfg)
    items = [it for it in bank(cfg) if it["answerable"] and not it["stale"]][:limit]
    d = new_run(cfg)
    rows = []
    with events.step(cfg.paths.state, "eval_search", questions=len(items), modes=len(modes)):
        _l1_rows(cfg, items, modes, rows)
    atomic_write(d / "l1.json", json.dumps({"modes": modes, "n": len(items), "rows": rows}, ensure_ascii=False, indent=1))
    report(cfg, d)
    return d


def _l1_rows(cfg, items: list[dict], modes: list[str], rows: list[dict]) -> None:
    from memexos.search import engine
    for i, it in enumerate(items, 1):
        for m in modes:
            t0 = time.time()
            try:
                hits, _, degraded = engine.search(cfg, it["question"], None, 10, m)
                rows.append({"id": it["id"], "type": it["type"], "mode": m, "secs": round(time.time() - t0, 2),
                             "top": [h["path"] for h in hits], "degraded": degraded})
            except Exception as e:  # noqa: BLE001 — one failing mode is recorded, not fatal
                rows.append({"id": it["id"], "type": it["type"], "mode": m, "secs": round(time.time() - t0, 2), "error": repr(e)[:200]})
        if i % 20 == 0:
            say(cfg, "l1_progress", i=i, n=len(items))


def score(cfg, rows: list[dict], items: dict[str, dict]) -> list[dict]:
    """Rank of the first right page, and recall@10 of the gold pages; with page judgments, judged-right pages count."""
    qrels = load_qrels(cfg)
    for r in rows:
        it = items.get(r["id"])
        if not it or r.get("error"):
            continue
        right = relevant(it, qrels)
        ranks = [i + 1 for i, p in enumerate(r["top"]) if p in right]
        r["first"] = ranks[0] if ranks else None
        r["recall10"] = len(set(r["top"][:10]) & set(it["gold_pages"])) / max(len(it["gold_pages"]), 1)
    return rows


def summarize(rows: list[dict], mode: str, typ: str | None = None) -> dict | None:
    rs = [r for r in rows if r["mode"] == mode and (typ is None or r["type"] == typ) and ("first" in r or r.get("error"))]
    if not rs:
        return None
    ok = [r for r in rs if not r.get("error")]
    hit = lambda k: sum(1 for r in ok if r["first"] and r["first"] <= k) / len(rs)   # noqa: E731
    return {"n": len(rs), "errors": len(rs) - len(ok), "hit1": hit(1), "hit3": hit(3), "hit10": hit(10),
            "recall10": statistics.mean(r["recall10"] for r in ok) if ok else 0,
            "mrr": statistics.mean(1 / r["first"] if r["first"] else 0 for r in ok) if ok else 0,
            "secs": statistics.median(r["secs"] for r in rs)}


def report(cfg, d: Path) -> Path:
    data = json.loads((d / "l1.json").read_text(encoding="utf-8"))
    items = {it["id"]: it for it in bank(cfg)}
    rows = score(cfg, data["rows"], items)
    pct = lambda x: f"{x * 100:.0f}%"   # noqa: E731
    name = lambda m: i18n.t(f"eval.mode_{m}") if i18n.has(f"eval.mode_{m}") else m   # noqa: E731
    L = [i18n.t("eval.l1_title", run=d.name), "", i18n.t("eval.l1_intro", n=data["n"]), "", "## " + i18n.t("eval.overall"), "",
         "| " + " | ".join(i18n.t("eval.cols")) + " |", "|---|---|---|---|---|---|---|---|"]
    summary, metrics = [], {}
    for m in data["modes"]:
        s = summarize(rows, m)
        if s:
            metrics[m] = {k: round(s[k], 3) for k in ("hit1", "hit3", "hit10", "recall10", "mrr")}
            L.append(f"| {name(m)} | {pct(s['hit1'])} | {pct(s['hit3'])} | {pct(s['hit10'])} | {pct(s['recall10'])} | "
                     f"{s['mrr']:.2f} | {s['secs']:.2f}s | {s['errors']} |")
            summary.append([name(m), round(s["hit1"], 3), round(s["hit3"], 3), round(s["hit10"], 3), round(s["mrr"], 3)])
    L += ["", "## " + i18n.t("eval.by_type"), "", "| | " + " | ".join(name(m) for m in data["modes"]) + " |",
          "|---|" + "---|" * len(data["modes"])]
    for t in TYPES:
        cells = [(lambda s: f"{pct(s['hit10'])} ({s['n']})" if s else "—")(summarize(rows, m, t)) for m in data["modes"]]
        if any(c != "—" for c in cells):
            L.append(f"| {i18n.t('eval.type_' + t)} | " + " | ".join(cells) + " |")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["id"]].append(r)
    misses = [i for i, rs in by.items() if all(not r.get("first") for r in rs)]
    L += ["", "## " + i18n.t("eval.misses"), ""]
    for i in misses[:15]:
        it = items.get(i, {})
        top = next((r.get("top") or [] for r in by[i] if r["mode"] == data["modes"][-1]), [])
        L += [f"- {i18n.t('eval.asked')}: {it.get('question', i)} ({i18n.t('eval.type_' + it.get('type', 'fact'))})",
              f"  - {i18n.t('eval.should')}: {', '.join(it.get('gold_pages', []))}",
              f"  - {i18n.t('eval.got')}: {', '.join(top[:3]) or '-'}"]
    L += ["", i18n.t("eval.miss_count", n=len(misses))]
    atomic_write(d / "report.md", "\n".join(L) + "\n")
    atomic_write(d / "summary.json", json.dumps({"run": d.name, "questions": data["n"], "rows": summary, "metrics": metrics,
                                                 "judged": bool(load_qrels(cfg))}, ensure_ascii=False))
    return d / "report.md"


def pool(cfg, depth: int = 3, batch: int = 25, days: int = 2) -> int:
    """Pooled judging (the usual practice in search evaluation): the top results of recent L1 runs for each question are
    judged page by page, so a right page that is not in the gold standard is not counted as a miss."""
    items = {it["id"]: it for it in bank(cfg) if it["answerable"] and not it["stale"]}
    since = time.strftime("%Y%m%d", time.localtime(time.time() - days * 86400))
    cand = collections.defaultdict(set)
    root = runs_root(cfg)
    for f in sorted(root.glob("*/l1.json")) if root.is_dir() else []:
        if f.parent.name[:8] < since:
            continue
        for r in json.loads(f.read_text(encoding="utf-8")).get("rows", []):
            if r["id"] in items:
                cand[r["id"]] |= set(r.get("top", [])[:depth])
    qrels = load_qrels(cfg)
    todo = []
    for qid, pages in cand.items():
        it = items[qid]
        pages = sorted(p for p in pages | set(it["gold_pages"]) | set(it["gold_raw"]) if (qid, p) not in qrels and (cfg.vault / p).exists())
        if pages:
            todo.append((qid, pages))
    if not todo:
        return 0
    d = new_run(cfg)
    n = 0
    for b in range(0, len(todo), batch):
        part = todo[b:b + batch]
        blocks = "\n\n".join(json.dumps({"id": q, "question": items[q]["question"], "key_facts": items[q]["key_facts"], "pages": ps},
                                        ensure_ascii=False) for q, ps in part)
        work = d / f"pool-{b // batch + 1:03d}"
        work.mkdir()
        try:
            res = model_call(cfg, i18n.prompt("eval_pool", blocks=blocks), POOLED, work, step="eval_judge")
        except RuntimeError as e:
            say(cfg, "batch_failed", what="pool", i=b // batch + 1, why=str(e)[:200])
            continue
        want = {(q, p) for q, ps in part for p in ps}
        rows = [{"id": r["id"], "page": r["page"], "label": r["label"], "reason": r["reason"][:300], "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
                for r in res["results"] if (r["id"], r["page"]) in want]
        qrels_path(cfg).parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(qrels_path(cfg), rows)
        n += len(rows)
    if n:
        commit_bank(cfg, f"eval: {n} page judgments for retrieval questions")
    return n


# ---------------------------------------------------------------- L2: agents using the interface
def sample(items: list[dict], n: int) -> list[dict]:
    """Stratified by type: each type in proportion, at least one."""
    by = collections.defaultdict(list)
    for it in items:
        by[it["type"]].append(it)
    out = []
    for xs in by.values():
        k = max(1, round(n * len(xs) / max(len(items), 1)))
        out += sorted(xs, key=lambda x: x["id"])[:: max(1, len(xs) // k)][:k]
    return out[:n]


def answer(cfg, agent: str, it: dict, d: Path) -> dict:
    """One agent answers in an empty directory that has nothing to do with the vault, as when the owner asks from
    another project, with only the command line interface. Every memex call it makes is traced (command, arguments,
    exit status) and scored for tool use (evals/tooluse.py); what its searches returned and which pages it opened
    come from the search/read events of its own run id. Commands with side effects are only recorded."""
    from memexos.evals import tooluse
    me = self_cmd()
    rid = f"eval-l2-{agent.replace(':', '-')}-{it['id']}-{secrets.token_hex(2)}"
    work = Path(tempfile.mkdtemp(prefix="memex-l2-"))
    trace = d / "l2" / f"{rid}.calls.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("MEMEX_RUN", "MEMEX_TRACE", "MEMEX_EVAL_STUB", "PATH")}
    os.environ.update(MEMEX_RUN=rid, MEMEX_TRACE=str(trace), MEMEX_EVAL_STUB=tooluse.STUB)
    t0 = time.time()
    task = it.get("type") == "task"
    try:
        # As in real use: `memex` is on the PATH and the agent has read `memex help` (its text is in the prompt).
        os.environ["PATH"] = f"{tooluse.memex_shim(d, me)}{os.pathsep}{os.environ.get('PATH', '')}"
        from memexos import agenthelp
        prompt = i18n.prompt("eval_task_context" if task else "eval_l2_context", memex="memex", help=agenthelp.text("main", cfg) or "",
                             question=it.get("task") if task else it["question"])
        # only the command line: no direct reads of the knowledge base's files. The state directory is writable so
        # the agent's memex calls can record what they did.
        pol = backends.Policy(write=[work, cfg.paths.state, trace.parent], commands=[["memex"], me])
        with events.step(cfg.paths.state, "eval_answer", agent=agent, question=it["id"]) as info:
            r = backends.agent(cfg, "@" + agent, prompt, cwd=work, workdir=d / "l2" / rid, policy=pol, effort="medium",
                               timeout=float(cfg.get("evals.l2_timeout") or 1200), job="eval")
            info.update(ok=r.ok, model=r.model, cost_usd=r.cost_usd)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(work, ignore_errors=True)
    seen = list(events.read(cfg.paths.state, since=t0 - 1, run=rid))
    calls = tooluse.load_calls(trace, seen)
    searched = [e["data"].get("query") for e in seen if e["type"] == "search"]
    found = {p for e in seen if e["type"] == "search" for p in e["data"].get("top", [])[:5]} | \
            {e["data"].get("path") for e in seen if e["type"] == "read"}
    gold = set(it.get("gold_pages", [])) | set(it.get("gold_raw", []))
    return {"id": it["id"], "agent": agent, "run": rid, "type": it.get("type"), "answer": r.text or "", "ok": r.ok,
            "why": "" if r.ok else r.tail[-300:], "secs": round(time.time() - t0), "cost_usd": r.cost_usd, "model": r.model,
            "tokens": tooluse.total_tokens(r.usage), "searched": searched,
            "opened": sorted(e["data"].get("path") for e in seen if e["type"] == "read"),
            "used_kb": bool(calls) or bool(seen), "found_gold": bool(found & gold),
            "tool": tooluse.score(it, calls, cfg.vault, cfg.language, load_qrels(cfg)) if r.ok else None,
            "denials": len(r.denials), "other_commands": tooluse.other_commands(d / "l2" / rid, me)}


def l2(cfg, n: int = 20, agents: list[str] | None = None, same_as: Path | None = None) -> Path:
    items = [it for it in bank(cfg) if not it["stale"]]
    if same_as:                                # the same questions as an earlier run, so the two compare directly
        ids = {r["id"] for r in json.loads((same_as / "l2.json").read_text(encoding="utf-8"))["results"]}
        picked = [it for it in items if it["id"] in ids]
    else:
        picked = sample(items, n)
    agents = agents or list(cfg.get("evals.l2_agents") or ["claude-code", "codex"])
    d = new_run(cfg)
    say(cfg, "l2_start", n=len(picked), agents=", ".join(agents))
    results = []
    for i, (it, ag) in enumerate(((it, ag) for it in picked for ag in agents), 1):
        results.append(answer(cfg, ag, it, d))
        atomic_write(d / "l2.json", json.dumps({"agents": agents, "n": len(picked), "results": results, "verdicts": []},
                                               ensure_ascii=False, indent=1))
        if i % 5 == 0:
            say(cfg, "l2_progress", i=i, n=len(picked) * len(agents))
    grade(cfg, d)
    return d


def grade(cfg, d: Path, batch: int = 12) -> int:
    """Grade the answers in another, independent run. Resumable: graded answers are kept, failed batches retried."""
    path = d / "l2.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    items = {it["id"]: it for it in bank(cfg)}
    done = {v["key"] for v in data["verdicts"]}
    # no answer means the agent did not run (quota, network): that is a failed run, not a wrong answer
    todo = [r for r in data["results"] if f"{r['agent']}:{r['id']}" not in done and r["id"] in items and r["answer"].strip()]
    failed = 0
    for b in range(0, len(todo), batch):
        part = todo[b:b + batch]
        blocks = "\n".join(json.dumps({"key": f"{r['agent']}:{r['id']}", "question": items[r["id"]]["question"],
                                       "type": items[r["id"]]["type"], "answerable": items[r["id"]]["answerable"],
                                       "key_facts": items[r["id"]]["key_facts"], "use_rule": items[r["id"]].get("use_rule", ""),
                                       "gold_pages": items[r["id"]]["gold_pages"], "answer": r["answer"][:6000]}, ensure_ascii=False)
                           for r in part)
        work = d / f"grade-{time.strftime('%H%M%S')}-{b // batch + 1:02d}"
        work.mkdir()
        try:
            res = model_call(cfg, i18n.prompt("eval_l2_judge", blocks=blocks), GRADED, work, step="eval_grade")
        except RuntimeError as e:
            failed += 1
            say(cfg, "batch_failed", what="grade", i=b // batch + 1, why=str(e)[:200])
            continue
        keys = {f"{r['agent']}:{r['id']}" for r in part}
        data["verdicts"] += [v for v in res["results"] if v["key"] in keys]
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1))
    l2_report(cfg, d)
    return 1 if failed else 0


def l2_report(cfg, d: Path) -> Path:
    data = json.loads((d / "l2.json").read_text(encoding="utf-8"))
    v = {x["key"]: x for x in data["verdicts"]}
    items = {it["id"]: it for it in bank(cfg)}
    L = [i18n.t("eval.l2_title", run=d.name), "", i18n.t("eval.l2_intro", n=data["n"]), "",
         "| " + " | ".join(i18n.t("eval.l2_cols")) + " |", "|" + "---|" * len(i18n.t("eval.l2_cols"))]
    for ag in data["agents"]:
        allrs = [r for r in data["results"] if r["agent"] == ag]
        rs = [r for r in allrs if r["answer"].strip()]
        if not rs:
            L.append(f"| {ag} | " + " | ".join(["—"] * (len(i18n.t("eval.l2_cols")) - 2)) + f" | {len(allrs)}/{len(allrs)} |")
            continue
        vs = [v[k] for k in (f"{ag}:{r['id']}" for r in rs) if k in v]
        cnt = collections.Counter(x["verdict"] for x in vs)
        neg = sum(1 for r in rs if not items.get(r["id"], {}).get("answerable", True))
        pos = len(rs) - neg
        frac = lambda k, base, cnt=cnt: f"{cnt[k]}/{base}" if base else "—"
        L.append(f"| {ag} | {frac('correct', pos)} | {frac('partial', pos)} | {frac('wrong', pos)} | {frac('honest_gap', neg)} | "
                 f"{frac('false_gap', pos)} | {frac('invented', neg)} | {sum(r['used_kb'] for r in rs)}/{len(rs)} | "
                 f"{sum(r['found_gold'] for r in rs if items.get(r['id'], {}).get('answerable', True))}/{pos} | "
                 f"{sum(1 for x in vs if x['citation_ok'])}/{len(vs)} | {statistics.mean(r['secs'] for r in rs):.0f}s | "
                 f"{len(allrs) - len(rs)}/{len(allrs)} |")
    L += ["", "## " + i18n.t("eval.l2_misses"), ""]
    for r in data["results"]:
        x = v.get(f"{r['agent']}:{r['id']}")
        if x and x["verdict"] not in ("correct", "honest_gap"):
            it = items.get(r["id"], {})
            L += [f"- **{r['agent']}** · {i18n.t('eval.asked')}: {it.get('question', r['id'])}",
                  f"  - {i18n.t('eval.verdict')}: {i18n.t('eval.v_' + x['verdict'])}. {x['reason']}",
                  f"  - {i18n.t('eval.trace', searched='; '.join(r['searched'][:3]) or '-', opened=', '.join(r['opened'][:3]) or '-', gold=', '.join(it.get('gold_pages', [])[:2]))}"]
    atomic_write(d / "l2-report.md", "\n".join(L) + "\n")
    return d / "l2-report.md"


def status(cfg) -> str:
    items = bank(cfg)
    c = collections.Counter(it["type"] for it in items)
    out = [i18n.t("eval.status_bank", n=len(items), stale=sum(it["stale"] for it in items),
                  types=", ".join(f"{i18n.t('eval.type_' + t)} {c[t]}" for t in TYPES if c[t]),
                  uncovered=len(uncovered(cfg)), judged=len(load_qrels(cfg)))]
    d = latest_run(cfg, "summary.json")
    if d:
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        out.append(i18n.t("eval.status_l1", run=s["run"]) + "".join(f"\n  {r[0]}: @1 {r[1]:.0%} · @3 {r[2]:.0%} · @10 {r[3]:.0%}" for r in s["rows"]))
    d = latest_run(cfg, "l2-report.md")
    if d:
        out.append(i18n.t("eval.status_l2", path=d / "l2-report.md"))
    return "\n".join(out)


# ---------------------------------------------------------------- CLI
def check_thresholds(d: Path, spec: str) -> int:
    """1 when any mode of an L1 run scores below a threshold ("hit3=0.9,mrr=0.8"), printing what failed."""
    want = {}
    for part in filter(None, (x.strip() for x in spec.split(","))):
        k, _, v = part.partition("=")
        if k not in ("hit1", "hit3", "hit10", "recall10", "mrr") or not v:
            print(i18n.t("eval.bad_threshold", part=part), file=sys.stderr)
            return 2
        want[k] = float(v)
    metrics = json.loads((d / "summary.json").read_text(encoding="utf-8")).get("metrics") or {}
    failed = [(m, k, got[k], v) for m, got in metrics.items() for k, v in want.items() if got.get(k, 0) < v]
    for m, k, got, v in failed:
        print(i18n.t("eval.below_threshold", mode=m, metric=k, got=got, want=v), file=sys.stderr)
    if not metrics:
        print(i18n.t("eval.no_metrics"), file=sys.stderr)
        return 1
    return 1 if failed else 0


def cmd(cfg, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex eval retrieval")
    ap.add_argument("action", choices=["mine", "gen", "cover", "usage", "verify", "l1", "pool", "l2", "grade", "report", "status"])
    ap.add_argument("run", nargs="?", help="a run directory (verify, grade, report); default: the latest")
    ap.add_argument("--n", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--modes", help="l1: comma-separated search modes")
    ap.add_argument("--agents", help="l2: comma-separated backends, e.g. claude-code,codex")
    ap.add_argument("--same-as", help="l2: reuse the questions of an earlier run directory")
    ap.add_argument("--fail-under", metavar="METRIC=VALUE,...",
                    help="l1: exit with status 1 when any search mode scores below these, e.g. hit3=0.9,mrr=0.8 "
                         "(metrics: hit1, hit3, hit10, recall10, mrr); for CI")
    a = ap.parse_args(argv)
    run_dir = Path(a.run).expanduser() if a.run else None
    if a.action == "status":
        print(status(cfg))
        return 0
    if a.action == "report":
        d = run_dir or latest_run(cfg, "l1.json")
        if not d:
            print(i18n.t("eval.no_run"), file=sys.stderr)
            return 1
        print(report(cfg, d) if (d / "l1.json").exists() else l2_report(cfg, d))
        return 0
    with events.run(cfg.paths.state, "eval"):
        if a.action == "mine":
            print(mine(cfg))
        elif a.action == "gen":
            print(gen(cfg, a.n or 40))
        elif a.action == "usage":
            print(usage_questions(cfg, a.n or 10))
        elif a.action == "cover":
            print(i18n.t("eval.added", n=cover(cfg, a.limit)))
        elif a.action == "verify":
            print(i18n.t("eval.added", n=verify(cfg, run_dir)))
        elif a.action == "l1":
            d = l1(cfg, a.modes.split(",") if a.modes else None, a.limit)
            print(d / "report.md")
            if a.fail_under:
                return check_thresholds(d, a.fail_under)
        elif a.action == "pool":
            print(i18n.t("eval.judged", n=pool(cfg)))
        elif a.action == "l2":
            d = l2(cfg, a.n or 20, a.agents.split(",") if a.agents else None, Path(a.same_as).expanduser() if a.same_as else None)
            print(d / "l2-report.md")
        elif a.action == "grade":
            d = run_dir or latest_run(cfg, "l2.json")
            return grade(cfg, d) if d else 1
    return 0
