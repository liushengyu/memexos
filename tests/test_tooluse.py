"""The tool-use evaluation: calls are recorded whichever agent makes them, side effects are only recorded, and each
scenario's calls are scored for the right command, the right arguments and how many calls it took."""

import json
import os
import sys
from pathlib import Path

import pytest

from memexos import config, scaffold
from memexos.cli import main
from memexos.evals import tooluse as T

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"
SM2 = "wiki/concepts/SM-2.md"
FACT = {"id": "s001", "type": "fact", "question": "what's the starting ease factor in supermemo's old algorithm?",
        "gold_pages": [SM2], "gold_raw": [], "answerable": True}


def call(argv, exit=0, top=None, opened=None):
    c = T.parse(argv)
    c.update(argv=argv, exit=exit)
    if top is not None:
        c["top"] = top
    if opened:
        c["opened"] = opened
    return c


@pytest.fixture
def vault(home):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False, sample=True)
    return v


def test_parse_reads_commands_as_agents_write_them():
    c = T.parse(["search", "how often", "--kw", '"spaced repetition" interval', "-n", "5", "--json"])
    assert c["cmd"] == "search" and c["pos"] == ["how often"] and c["opts"]["n"] == "5" and c["opts"]["json"] is True
    assert T.keywords(c["opts"]["kw"]) == ["spaced repetition", "interval"]
    assert T.parse(["feedback", "SM-2", "--kind=incorrect", "--why", "x"])["opts"]["kind"] == "incorrect"


def test_argument_checks():
    assert T.arg_problems(call(["search", "q", "--kw", "spaced repetition"]), "en") == []
    assert "no --kw" in T.arg_problems(call(["search", "q"]), "en")
    assert "keywords not in the pages' language" in T.arg_problems(call(["search", "q", "--kw", "间隔 重复"]), "en")
    assert "keywords not in the pages' language" in T.arg_problems(call(["search", "间隔重复多久", "--kw", "spaced"]), "zh")
    assert T.arg_problems(call(["search", "how is RRF used", "--kw", "RRF"]), "zh") == []        # an English question
    assert "failed" in T.arg_problems(call(["search"], exit=2), "en")
    assert T.arg_problems(call(["feedback", "SM-2", "--kind", "missing"]), "en") == ["no --why"]


def test_the_direct_route_scores_full_efficiency(vault):
    calls = [call(["search", "starting ease factor", "--kw", "SM-2 ease factor"], top=[SM2]), call(["get", "SM-2"], opened=SM2)]
    s = T.score(FACT, calls, vault, "en")
    assert s["procedure_ok"] and s["efficiency"] == 1.0 and s["extra_calls"] == 0 and s["arg_ok"] == 2
    assert s["first_gold_call"] == 1


def test_detours_cost_efficiency_and_are_counted(vault):
    calls = [call(["search", "ease"]),                                                     # no keywords
             call(["search", "--bad"], exit=2),                                            # wrong arguments
             call(["search", "starting ease", "--kw", "ease factor"], top=["wiki/concepts/Anki.md"]),
             call(["search", "starting ease", "--kw", "ease factor"], top=["wiki/concepts/Anki.md"]),
             call(["search", "SM-2 starting ease", "--kw", "SM-2"], top=[SM2]),
             call(["get", "Anki"], opened="wiki/entities/Anki.md"),
             call(["get", "SM-2"], opened=SM2)]
    s = T.score(FACT, calls, vault, "en")
    assert s["procedure_ok"] and s["calls"] == 7 and s["efficiency"] == round(2 / 7, 3) and s["extra_calls"] == 5
    assert s["failed_calls"] == 1 and s["repeated_calls"] == 1 and s["arg_ok"] == 5 and s["first_gold_call"] == 5


def test_answering_without_opening_the_page_misses_a_step(vault):
    s = T.score(FACT, [call(["search", "q", "--kw", "SM-2"], top=[SM2])], vault, "en")
    assert not s["procedure_ok"] and s["steps"] == "1/2" and s["efficiency"] == 0.0


def test_negative_questions_allow_a_few_searches_and_nothing_else(vault):
    neg = {"id": "n1", "type": "negative", "question": "what did Leitner say about mobile apps?", "gold_pages": [], "answerable": False}
    ok = T.score(neg, [call(["search", "q", "--kw", "Leitner apps"], top=[])], vault, "en")
    assert ok["procedure_ok"] and ok["efficiency"] == 1.0
    many = T.score(neg, [call(["search", f"q{i}", "--kw", "Leitner"], top=[]) for i in range(4)], vault, "en")
    assert not many["procedure_ok"] and many["too_many_searches"]
    saved = T.score(neg, [call(["search", "q", "--kw", "x"], top=[]), call(["add-url", "https://x.example"])], vault, "en")
    assert not saved["procedure_ok"] and saved["forbidden"]


def test_order_is_checked_where_it_matters(vault):
    t = json.loads((vault / ".memex/evals/tooluse/scenarios.jsonl").read_text().splitlines()[1])        # report a page
    page = "wiki/concepts/Leitner system.md"
    right = [call(["get", "Leitner system"], opened=page),
             call(["feedback", "Leitner system", "--kind", "missing", "--why", "variant: down one box"])]
    assert T.score(t, right, vault, "en")["procedure_ok"]
    reported_first = list(reversed(right))
    assert not T.score(t, reported_first, vault, "en")["procedure_ok"]
    wrong_kind = [right[0], call(["feedback", "Leitner system", "--kind", "incorrect", "--why", "x"])]
    assert not T.score(t, wrong_kind, vault, "en")["procedure_ok"]


def test_calls_are_traced_and_side_effects_only_recorded(vault, tmp_path, monkeypatch, capsys):
    trace = tmp_path / "calls.jsonl"
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_TRACE", str(trace))
    monkeypatch.setenv("MEMEX_EVAL_STUB", T.STUB)
    before = sorted(p.name for p in (vault / "raw").iterdir())
    assert main(["add-url", "https://example.com/a"]) == 0
    assert "recorded and not carried out" in capsys.readouterr().out
    assert sorted(p.name for p in (vault / "raw").iterdir()) == before
    with pytest.raises(SystemExit):
        main(["search"])                                       # wrong arguments: argparse exits, still recorded
    assert main(["search", "ease factor", "--kw", "SM-2"]) == 0
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [r["argv"][0] for r in rows] == ["add-url", "search", "search"] and [r["exit"] for r in rows] == [0, 2, 0]


def test_a_run_with_an_agent_that_takes_a_detour(vault, home, tmp_path, monkeypatch):
    """The whole loop with a stand-in agent: it runs real memex commands; the report shows the route it took."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for n in ("claude", "codex"):
        (bin_dir / n).symlink_to(FAKE)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    monkeypatch.setenv("FAKE_MEMEX", json.dumps([sys.executable, "-m", "memexos"]))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    plans = {"supermemo": [["search", "starting ease factor"], ["search", "starting ease factor", "--kw", "SM-2 ease"],
                           ["get", "SM-2"]],
             "keep this article": [["help"], ["add-url", "https://example.com/articles/interleaved-practice"]]}
    monkeypatch.setenv("FAKE_CLI_REPLY", json.dumps({"plans": plans, "result": "done", "structured": {"results": []},
                                                     "other_commands": ["cat ~/memex/index.md"]}))
    from memexos.backends import claude_code, codex
    monkeypatch.setattr(claude_code.ClaudeCode, "_login", {})
    monkeypatch.setattr(codex.Codex, "_login", {})
    monkeypatch.setattr(codex, "user_config", lambda home: {})
    cfg = config.load(vault)
    from memexos.search import fts
    fts.update(vault, cfg.paths.search_index)
    monkeypatch.setattr(T, "tasks", lambda cfg: [t for t in T.read_jsonl(T.scenarios_path(cfg)) if t["id"] == "t001"])
    from memexos.evals import retrieval as R
    monkeypatch.setattr(R, "sample", lambda items, n: [it for it in items if it["id"] == "s001"])
    d = T.run(cfg, n=1, agents=["codex"])
    data = json.loads((d / "l2.json").read_text())
    by = {r["id"]: r for r in data["results"]}
    q = by["s001"]["tool"]
    assert q["calls"] == 3 and q["procedure_ok"] and q["efficiency"] == round(2 / 3, 3) and q["arg_ok"] == 2
    t = by["t001"]["tool"]
    assert t["procedure_ok"] and t["calls"] == 2 and t["efficiency"] == 0.5 and t["extra_calls"] == 1   # an unneeded help
    assert by["t001"]["other_commands"] == 1
    assert sorted(p.name for p in (vault / "raw").glob("*.md")) == sorted(p.name for p in (scaffold.sample_dir() / "raw").glob("*.md"))
    rep = (d / "tooluse-report.md").read_text()
    assert "codex" in rep and "search starting ease factor" in rep
    summary = json.loads((d / "tooluse-summary.json").read_text())["codex"]
    assert summary["scenarios"] == 2 and summary["procedure_ok"] == 1.0


def test_both_keyword_forms_agents_use_are_understood():
    from memexos.search.cli import keyword_list
    assert keyword_list(['"rank fusion" RRF']) == ["rank fusion", "RRF"]
    assert keyword_list(["Leitner", "spaced repetition"]) == ["Leitner", "spaced repetition"]        # one --kw each
    assert keyword_list(["spaced repetition interval"]) == ["spaced", "repetition", "interval"]
    assert keyword_list(None) is None
    c = T.parse(["search", "q", "--kw", "Leitner", "--kw", "spaced repetition"])
    assert T.keywords(c["opts"]["kw"]) == ["Leitner", "spaced repetition"]
    assert T.arg_problems(dict(c, argv=[], exit=0), "en") == []


def test_repeated_kw_all_reach_the_search(vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    from memexos.search import fts
    fts.update(vault, config.load(vault).paths.search_index)
    assert main(["search", "boxes", "--kw", "Leitner", "--kw", "first box", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["keywords"] == ["Leitner", "first box"]


def test_comma_separated_keywords_are_keywords():
    from memexos.search.cli import keyword_list
    assert keyword_list(["slip-box, Zettelkasten, flashcards, spaced repetition, Anki"]) == \
        ["slip-box", "Zettelkasten", "flashcards", "spaced repetition", "Anki"]
    assert keyword_list(["间隔重复，莱特纳盒子"]) == ["间隔重复", "莱特纳盒子"]
    c = T.parse(["search", "q", "--kw", "slip-box, Zettelkasten, flashcards, spaced repetition, Anki"])
    assert T.arg_problems(dict(c, argv=[], exit=0), "en") == []


def test_a_source_page_with_the_same_facts_counts_as_right(vault):
    it = dict(FACT, gold_raw=["raw/2026-01-05-web-example-how-spaced-repetition-works.md"])
    src = "wiki/sources/How spaced repetition works.md"
    calls = [call(["search", "q", "--kw", "SM-2"], top=[src]), call(["get", "How spaced repetition works"], opened=src)]
    assert T.score(it, calls, vault, "en")["procedure_ok"]
