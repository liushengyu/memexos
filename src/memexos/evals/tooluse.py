"""Tool-use evaluation: does an agent call the right memex commands with the right arguments, and how directly?

Correctness is about the calls themselves: for this situation, the right command with the right arguments (keywords
given and in the pages' language, the right page opened, the right kind of report, the right filter). Efficiency is
about the route: every scenario has a reference flow, the calls that must happen and the fewest calls a capable agent
needs. A capable agent gets there in about that many calls; a weaker one searches again and again, opens pages it
does not need, or gets arguments wrong and retries. The order of the reference calls is checked only where it
matters logically (open a page before citing it, check a page before reporting it); otherwise the agent plans its
own route and is measured on how many calls it took to reach the right ones.

What the agent did comes from the command line itself: in an evaluation run every memex call is written to a trace
file with its arguments and exit status (memexos.cli), whichever agent made it, and searches and page reads also
record what they returned. Commands that would change something (saving a link, a report, a login) are recorded but
not carried out, so a run leaves the knowledge base as it was.

Scenarios:
  - every question in the retrieval bank (.memex/evals/retrieval/questions.jsonl) has a default reference flow by type
    (see reference());
  - task scenarios (.memex/evals/tooluse/scenarios.jsonl) describe other jobs: saving a link, reporting a page,
    browsing by evidence, keeping a conclusion, checking status. Each spells out its reference flow.

    memex eval tooluse run [--n 20] [--agents claude-code,codex] [--no-tasks] [--same-as RUN]
    memex eval tooluse report [RUN]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

from memexos import events, i18n
from memexos.util import atomic_write, read_jsonl

# Read-only commands run for real; everything else is only recorded during an evaluation ("cmd:sub" for one action).
STUB = ("add-url,add-video,add-visual,feedback,login,cookies:refresh,ingest,fix,correct,lint,intake,inbox,schedule,"
        "secret,setup,init,reindex,pause,resume,sessions,fetch,console,nightly,eval,models:pull,usage,catalog:sync")
FLAGS = {"json", "inbox", "deep", "yes", "sample", "no-embed"}           # options that take no value
CJK = re.compile(r"[㐀-鿿豈-﫿]")
SEARCH_TOP = 5


def scenarios_path(cfg) -> Path:
    return cfg.paths.ledger / "evals" / "tooluse" / "scenarios.jsonl"


def tasks(cfg) -> list[dict]:
    return [t for t in read_jsonl(scenarios_path(cfg)) if t.get("status", "active") == "active"]


# ---------------------------------------------------------------- calls
def parse(argv: list[str]) -> dict:
    """{"cmd", "pos": [...], "opts": {name: value or True}} from a memex argument list."""
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    pos, opts, i = [], {}, 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            _put(opts, k, v)
        elif a.startswith("-") and len(a) > 1 and not re.fullmatch(r"-\d+(\.\d+)?", a):
            k = a.lstrip("-")
            if k not in FLAGS and i + 1 < len(rest):
                _put(opts, k, rest[i + 1])
                i += 1
            else:
                opts[k] = True
        else:
            pos.append(a)
        i += 1
    return {"cmd": cmd, "pos": pos, "opts": opts}


def _put(opts: dict, k: str, v: str) -> None:
    """A repeated option keeps every value (--kw a --kw b)."""
    if k in opts and opts[k] is not True:
        opts[k] = (opts[k] if isinstance(opts[k], list) else [opts[k]]) + [v]
    else:
        opts[k] = v


def keywords(kw) -> list[str]:
    """The keywords of a search, as memex search reads --kw (see search.cli.keyword_list)."""
    from memexos.search.cli import keyword_list
    if isinstance(kw, list):
        return keyword_list(kw) or []
    if not isinstance(kw, str) or not kw.strip():
        return []
    return keyword_list([kw]) or []


def load_calls(trace: Path, found: list[dict]) -> list[dict]:
    """The agent's memex calls in order, each with what it returned: a successful search is paired with the next search
    event of the run (its top results), a successful get with the page it opened."""
    calls = []
    searches = iter([e["data"] for e in found if e["type"] == "search"])
    reads = iter([e["data"] for e in found if e["type"] == "read"])
    for row in read_jsonl(trace) if trace.exists() else []:
        c = parse(row.get("argv") or [])
        c.update(exit=row.get("exit"), secs=row.get("secs"), argv=row.get("argv") or [])
        if c["exit"] == 0 and c["cmd"] == "search":
            c["top"] = (next(searches, {}) or {}).get("top", [])
        if c["exit"] == 0 and c["cmd"] == "get":
            c["opened"] = (next(reads, {}) or {}).get("path")
        calls.append(c)
    return calls


def arg_problems(c: dict, language: str, question: str = "") -> list[str]:
    """What is wrong with one call's arguments (empty when nothing is). A call that failed is wrong."""
    out = []
    if c.get("exit") not in (0, None):
        out.append("failed")
    if c["cmd"] == "search":
        kws = keywords(c["opts"].get("kw"))
        if not kws:
            out.append("no --kw")
        elif len(kws) > 5:
            out.append("more than 5 keywords")
        if kws:
            has_cjk = any(CJK.search(k) for k in kws)
            asked = (question or "") + " " + " ".join(map(str, c["pos"]))
            if language == "zh" and not has_cjk and CJK.search(asked):
                out.append("keywords not in the pages' language")
            if language != "zh" and has_cjk:
                out.append("keywords not in the pages' language")
        n = c["opts"].get("n")
        if isinstance(n, str) and n.isdigit() and int(n) > 20:
            out.append("-n above 20")
        if not c["pos"]:
            out.append("no question")
    elif c["cmd"] == "feedback":
        if not c["opts"].get("kind"):
            out.append("no --kind")
        if not str(c["opts"].get("why") or "").strip():
            out.append("no --why")
    elif c["cmd"] == "add-url" and not any(str(p).startswith(("http://", "https://")) for p in c["pos"]):
        out.append("no link")
    return out


def total_tokens(usage: dict | None) -> int | None:
    """All tokens of one agent run, cached input included (Claude Code reports cache reads apart from input; Codex
    reports them inside input)."""
    u = usage or {}
    if not u:
        return None
    if "cache_read_tokens" in u:
        keys = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")
    else:
        keys = ("input_tokens", "output_tokens", "reasoning_output_tokens")
    return sum(int(u.get(k) or 0) for k in keys)


def other_commands(workdir: Path, me: list[str]) -> int | None:
    """Shell commands the agent ran that were not memex (reading the knowledge base's files directly, for example),
    where the agent's log has them (Codex); None where it does not."""
    f = workdir / "events.jsonl"
    if not f.exists():
        return None
    mark = " ".join(me[-2:]) if len(me) > 1 else me[0]
    memex_cmd = re.compile(r"(?:^|[\s;&|'\"(/])memex(?:os)?(?:\s|$)")
    n = 0
    for line in open(f, errors="ignore"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        item = e.get("item") or {}
        if e.get("type") == "item.completed" and item.get("type") == "command_execution":
            text = str(item.get("command") or "")
            if mark not in text and not memex_cmd.search(text):
                n += 1
    return n


def memex_shim(run_dir: Path, me: list[str]) -> Path:
    """A directory with a `memex` that runs this MemexOS, so agents call it by the name its help uses."""
    import shlex as _shlex
    d = run_dir / "bin"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "memex"
    if not f.exists():
        f.write_text("#!/bin/sh\nexec " + " ".join(_shlex.quote(x) for x in me) + ' "$@"\n', encoding="utf-8")
        f.chmod(0o755)
    return d


# ---------------------------------------------------------------- reference flows
def reference(it: dict) -> dict:
    """The reference flow of a scenario: {"steps": [...], "order": "as_listed" | "any", "min_calls", "forbid",
    "max_search"}. Task scenarios carry their own ("expect"); questions get one by type."""
    if it.get("expect"):
        return it["expect"]
    gold = [p for p in it.get("gold_pages", []) if p.startswith("wiki/")]
    t = it.get("type")
    if t == "negative" or not it.get("answerable", True):
        return {"steps": [{"cmd": "search", "need": ["kw"]}], "order": "any", "min_calls": 1, "max_search": 3,
                "forbid": [{"cmd": "add-url"}, {"cmd": "feedback"}]}
    if t == "synth":
        k = max(1, min(len(gold), 2))      # the answer is spread over pages: at least two of them read
        return {"steps": [{"cmd": "search", "need": ["kw"], "result": "gold"}] + [{"cmd": "get", "target": "gold"}] * k,
                "order": "as_listed", "min_calls": 1 + k, "forbid": [{"cmd": "add-url"}, {"cmd": "feedback"}]}
    return {"steps": [{"cmd": "search", "need": ["kw"], "result": "gold"}, {"cmd": "get", "target": "gold"}],
            "order": "as_listed", "min_calls": 2, "forbid": [{"cmd": "add-url"}, {"cmd": "feedback"}]}


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _page(vault: Path, name: str) -> str | None:
    from memexos.search.cli import resolve_page
    return resolve_page(vault, name) if name else None


def matches(step: dict, c: dict, gold: set[str], used: set[str], vault: Path) -> bool:
    if c["cmd"] != step["cmd"] or c.get("exit") not in (0, None):
        return False
    if "pos0" in step and _norm((c["pos"] or [""])[0]) != _norm(step["pos0"]):
        return False
    for k, want in (step.get("opts") or {}).items():
        got = c["opts"].get(k)
        if got is None or _norm(want) not in _norm(got):
            return False
    for k in step.get("need") or []:
        if not (keywords(c["opts"].get("kw")) if k == "kw" else c["opts"].get(k)):
            return False
    if step.get("result") == "gold":
        if not set(c.get("top", [])[:SEARCH_TOP]) & gold:
            return False
    target = step.get("target")
    if target:
        page = c.get("opened") or _page(vault, (c["pos"] or [""])[0])
        if target == "gold":
            if page not in gold or page in used:
                return False
            used.add(page)
        elif page != target:
            return False
    return True


def right_pages(it: dict, vault: Path, qrels: dict | None = None) -> set[str]:
    """Pages that hold the answer: the gold pages, the source pages compiled from the gold sources (they carry the same
    facts), the raw sources themselves, and pages judged to answer the question (qrels)."""
    from memexos.search.cli import compiled_page
    pages = set(it.get("gold_pages", [])) | set(it.get("gold_raw", []))
    for raw in it.get("gold_raw", []):
        page = compiled_page(vault, raw)
        if page:
            pages.add(f"wiki/sources/{page}.md")
    for (qid, page), label in (qrels or {}).items():
        if qid == it.get("id") and label == "answers":
            pages.add(page)
    return pages


def score(it: dict, calls: list[dict], vault: Path, language: str, qrels: dict | None = None) -> dict:
    """Correctness and efficiency of one agent's calls for one scenario."""
    ref = reference(it)
    gold = right_pages(it, vault, qrels)
    problems = [arg_problems(c, language, it.get("question") or it.get("task", "")) for c in calls]
    matched, used, at = [], set(), 0
    for step in ref["steps"]:
        start = at if ref.get("order") == "as_listed" else 0
        hit = next((i for i in range(start, len(calls)) if i not in matched and matches(step, calls[i], gold, used, vault)), None)
        if hit is None:
            break
        matched.append(hit)
        at = hit + 1
    forbidden = [i for i, c in enumerate(calls) for f in ref.get("forbid", []) if c["cmd"] == f["cmd"]]
    n_search = sum(1 for c in calls if c["cmd"] == "search")
    too_many = bool(ref.get("max_search")) and n_search > ref["max_search"]
    procedure_ok = len(matched) == len(ref["steps"]) and not forbidden and not too_many
    seen = collections.Counter(tuple(c["argv"]) for c in calls)
    first_gold = next((i + 1 for i, c in enumerate(calls)
                       if set(c.get("top", [])[:SEARCH_TOP]) & gold or (c.get("opened") in gold)), None)
    n = len(calls)
    return {
        "calls": n, "min_calls": ref["min_calls"], "procedure_ok": procedure_ok,
        "steps": f"{len(matched)}/{len(ref['steps'])}",
        "efficiency": round(min(1.0, ref["min_calls"] / n), 3) if procedure_ok and n else 0.0,
        "extra_calls": max(0, n - ref["min_calls"]) if procedure_ok else None,
        "arg_ok": sum(1 for p in problems if not p), "arg_problems": [f"{' '.join(c['argv'])[:120]}: {', '.join(p)}"
                                                                       for c, p in zip(calls, problems) if p],
        "failed_calls": sum(1 for c in calls if c.get("exit") not in (0, None)),
        "repeated_calls": sum(k - 1 for k in seen.values() if k > 1),
        "forbidden": [" ".join(calls[i]["argv"])[:120] for i in forbidden],
        "too_many_searches": too_many,
        "first_gold_call": first_gold,
        "trace": [" ".join(c["argv"])[:160] + ("" if c.get("exit") in (0, None) else f"  → exit {c['exit']}") for c in calls],
    }


# ---------------------------------------------------------------- runs
def run(cfg, n: int = 20, agents: list[str] | None = None, with_tasks: bool = True, same_as: Path | None = None) -> Path:
    from memexos.evals import retrieval as R
    items = [it for it in R.bank(cfg) if not it["stale"]]
    picked = R.sample(items, n) if n else []
    if with_tasks:
        picked += [dict(t, type="task") for t in tasks(cfg)]
    if same_as:
        ids = {r["id"] for r in json.loads((same_as / "l2.json").read_text(encoding="utf-8"))["results"]}
        picked = [it for it in items + [dict(t, type="task") for t in tasks(cfg)] if it["id"] in ids]
    agents = agents or list(cfg.get("evals.l2_agents") or ["claude-code", "codex"])
    d = new_run(cfg)
    R.say(cfg, "tool_start", n=len(picked), agents=", ".join(agents))
    results = []
    for i, (it, ag) in enumerate(((it, ag) for it in picked for ag in agents), 1):
        results.append(R.answer(cfg, ag, it, d))
        atomic_write(d / "l2.json", json.dumps({"agents": agents, "n": len(picked), "results": results, "verdicts": [],
                                                "tasks": [it for it in picked if it.get("type") == "task"]},
                                               ensure_ascii=False, indent=1))
        if i % 5 == 0:
            R.say(cfg, "l2_progress", i=i, n=len(picked) * len(agents))
    R.grade(cfg, d)
    report(cfg, d)
    return d


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def summarize(results: list[dict], verdicts: dict) -> dict:
    """Per agent: how often the whole job was done right, how often the calls were right, and how directly."""
    out = {}
    for ag in dict.fromkeys(r["agent"] for r in results):
        rs = [r for r in results if r["agent"] == ag and r.get("tool")]
        if not rs:
            continue

        def done(r, ag=ag):
            v = verdicts.get(f"{ag}:{r['id']}")
            answered = r.get("type") == "task" or (v and v["verdict"] in ("correct", "honest_gap"))
            return r["tool"]["procedure_ok"] and bool(answered)
        calls = sum(r["tool"]["calls"] for r in rs)
        out[ag] = {
            "scenarios": len(rs),
            "success": sum(1 for r in rs if done(r)) / len(rs),
            "procedure_ok": sum(1 for r in rs if r["tool"]["procedure_ok"]) / len(rs),
            "arg_ok": sum(r["tool"]["arg_ok"] for r in rs) / calls if calls else None,
            "calls": _mean([r["tool"]["calls"] for r in rs]),
            "min_calls": _mean([r["tool"]["min_calls"] for r in rs]),
            "efficiency": _mean([r["tool"]["efficiency"] for r in rs if r["tool"]["procedure_ok"]]),
            "failed_calls": sum(r["tool"]["failed_calls"] for r in rs),
            "repeated_calls": sum(r["tool"]["repeated_calls"] for r in rs),
            "first_gold_call": _mean([r["tool"]["first_gold_call"] for r in rs]),
            "other_commands": sum(r.get("other_commands") or 0 for r in rs),
            "secs": _mean([r.get("secs") for r in rs]),
            "tokens": _mean([r.get("tokens") for r in rs]),
            "by_type": {t: {"n": len(xs), "procedure_ok": sum(1 for r in xs if r["tool"]["procedure_ok"]) / len(xs),
                            "calls": _mean([r["tool"]["calls"] for r in xs]),
                            "min_calls": _mean([r["tool"]["min_calls"] for r in xs])}
                        for t, xs in _group(rs).items()},
        }
    return out


def _group(rs):
    g = collections.defaultdict(list)
    for r in rs:
        g[r.get("type") or "?"].append(r)
    return g


def rescore(cfg, d: Path, data: dict) -> None:
    """Score again from the saved traces, so a run can be re-read after the scoring rules change."""
    from memexos.evals import retrieval as R
    items = {it["id"]: it for it in R.bank(cfg)}
    items.update({t["id"]: dict(t, type="task") for t in data.get("tasks", [])})
    for r in data["results"]:
        it = items.get(r["id"])
        rid = r.get("run")
        trace = d / "l2" / f"{rid}.calls.jsonl" if rid else \
            next(iter(sorted((d / "l2").glob(f"eval-l2-{r['agent'].replace(':', '-')}-{r['id']}-*.calls.jsonl"))), None)
        if not it or not trace or not trace.exists() or not r.get("ok"):
            continue
        rid = rid or trace.name.removesuffix(".calls.jsonl")
        seen = list(events.read(cfg.paths.state, run=rid))
        r["tool"] = score(it, load_calls(trace, seen), cfg.vault, cfg.language, R.load_qrels(cfg))


def report(cfg, d: Path) -> Path:
    data = json.loads((d / "l2.json").read_text(encoding="utf-8"))
    rescore(cfg, d, data)
    atomic_write(d / "l2.json", json.dumps(data, ensure_ascii=False, indent=1))
    verdicts = {v["key"]: v for v in data.get("verdicts", [])}
    s = summarize(data["results"], verdicts)
    pct = lambda x: "—" if x is None else f"{x:.0%}"     # noqa: E731
    num = lambda x, f="{:.1f}": "—" if x is None else f.format(x)    # noqa: E731
    L = [i18n.t("tooluse.title", run=d.name), "", i18n.t("tooluse.intro"), "",
         "| " + " | ".join(i18n.t("tooluse.cols")) + " |", "|" + "---|" * len(i18n.t("tooluse.cols"))]
    for ag, x in s.items():
        L.append(f"| {ag} | {x['scenarios']} | {pct(x['success'])} | {pct(x['procedure_ok'])} | {pct(x['arg_ok'])} | "
                 f"{num(x['calls'])} / {num(x['min_calls'])} | {pct(x['efficiency'])} | {x['failed_calls']} | "
                 f"{x['repeated_calls']} | {num(x['first_gold_call'])} | {num(x['secs'], '{:.0f}s')} | "
                 f"{num(x['tokens'], '{:,.0f}')} |")
    L += ["", "## " + i18n.t("tooluse.by_type"), "", "| " + " | ".join(i18n.t("tooluse.type_cols")) + " |",
          "|" + "---|" * len(i18n.t("tooluse.type_cols"))]
    for ag, x in s.items():
        for t, y in sorted(x["by_type"].items()):
            L.append(f"| {ag} | {t} | {y['n']} | {pct(y['procedure_ok'])} | {num(y['calls'])} / {num(y['min_calls'])} |")
    L += ["", "## " + i18n.t("tooluse.misses"), ""]
    items = {it["id"]: it for it in data.get("tasks", [])}
    from memexos.evals import retrieval as R
    items.update({it["id"]: it for it in R.bank(cfg)})
    for r in data["results"]:
        tl = r.get("tool")
        if not tl or (tl["procedure_ok"] and not tl["arg_problems"] and tl["calls"] <= tl["min_calls"]):
            continue
        it = items.get(r["id"], {})
        L += [f"- **{r['agent']}** · {it.get('question') or it.get('task') or r['id']}",
              "  - " + i18n.t("tooluse.expected", steps=_describe(reference(it)), min=tl["min_calls"]),
              "  - " + i18n.t("tooluse.actual", n=tl["calls"], steps=tl["steps"], ok=i18n.t("tooluse.yes" if tl["procedure_ok"] else "tooluse.no"))]
        for line in tl["trace"][:12]:
            L.append(f"    - `{line}`")
        for p in tl["arg_problems"][:5]:
            L.append("  - " + i18n.t("tooluse.arg_problem", text=p))
        for f in tl["forbidden"]:
            L.append("  - " + i18n.t("tooluse.forbidden", text=f))
    atomic_write(d / "tooluse-report.md", "\n".join(L) + "\n")
    atomic_write(d / "tooluse-summary.json", json.dumps(s, ensure_ascii=False, indent=1))
    return d / "tooluse-report.md"


def _describe(ref: dict) -> str:
    parts = []
    for st in ref["steps"]:
        p = st["cmd"]
        if st.get("pos0"):
            p += f" {st['pos0']}"
        for k, v in (st.get("opts") or {}).items():
            p += f" --{k} {v}"
        if "kw" in (st.get("need") or []):
            p += " --kw …"
        if st.get("target"):
            p += " <" + ("a right page" if st["target"] == "gold" else st["target"]) + ">"
        if st.get("result") == "gold":
            p += " (a right page in the top 5)"
        parts.append(p)
    sep = " → " if ref.get("order") == "as_listed" else " + "
    return sep.join(parts) + (f"; at most {ref['max_search']} searches" if ref.get("max_search") else "")


def runs_root(cfg) -> Path:
    return cfg.paths.state / "evals" / "tooluse"


def new_run(cfg) -> Path:
    import time
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


def latest(cfg) -> Path | None:
    root = runs_root(cfg)
    runs = sorted(x for x in root.iterdir() if (x / "l2.json").exists()) if root.is_dir() else []
    return runs[-1] if runs else None


def cmd(cfg, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex eval tooluse", description=i18n.t("tooluse.help"))
    ap.add_argument("action", choices=["run", "report"])
    ap.add_argument("run_dir", nargs="?", help="report: a run directory (default: the latest)")
    ap.add_argument("--n", type=int, default=20, help="questions from the retrieval bank (0 = tasks only)")
    ap.add_argument("--agents", help="comma-separated backends, e.g. claude-code,codex")
    ap.add_argument("--no-tasks", action="store_true", help="questions only, without the task scenarios")
    ap.add_argument("--same-as", help="the same scenarios as an earlier run directory, to compare before and after a change")
    a = ap.parse_args(argv)
    if a.action == "report":
        d = Path(a.run_dir).expanduser() if a.run_dir else latest(cfg)
        if not d:
            print(i18n.t("eval.no_run"), file=sys.stderr)
            return 1
        print(report(cfg, d))
        return 0
    with events.run(cfg.paths.state, "eval"):
        d = run(cfg, a.n, a.agents.split(",") if a.agents else None, not a.no_tasks,
                Path(a.same_as).expanduser() if a.same_as else None)
    print(d / "tooluse-report.md")
    return 0
