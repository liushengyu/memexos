"""Retrieval evaluation with scripted stand-in models: no network, no cost."""

import json
import subprocess

import pytest

from memexos import backends, config, scaffold
from memexos.backends.base import Backend, Result
from memexos.evals import retrieval as R
from memexos.search import fts

PAGES = {"wiki/concepts/Memex.md": "# Memex\n\nVannevar Bush described the memex in 1945 as a desk that stores books and records on microfilm.\n",
         "wiki/concepts/Associative trail.md": "# Associative trail\n\nA trail links documents in a chain a reader can replay and share.\n",
         "wiki/entities/Ted Nelson.md": "# Ted Nelson\n\nCoined the word hypertext in 1965 and started Project Xanadu.\n"}


class FakeModel(Backend):
    """Writes one question per page it is shown; keeps every candidate; judges every pooled page as answering."""
    name = "fake"
    calls: list = []

    def available(self):
        return True, ""

    def structured(self, prompt, schema, **kw):
        FakeModel.calls.append(prompt[:80])
        props = schema["properties"]
        if "items" in props:
            pages = [p for p in PAGES if f"### {p}" in prompt]
            return Result(ok=True, backend="fake", result={"items": [
                {"type": "fact", "question": {"wiki/concepts/Memex.md": "bush microfilm desk 1945",
                                              "wiki/concepts/Associative trail.md": "chain of linked documents you can replay",
                                              "wiki/entities/Ted Nelson.md": "who coined hypertext"}[p],
                 "gold_pages": [p], "gold_raw": [], "key_facts": ["x"], "answerable": True, "use_rule": "", "domain": "", "why": ""}
                for p in pages]})
        rows = [json.loads(l) for l in prompt.splitlines() if l.startswith("{")]
        if "keep" in json.dumps(schema):
            return Result(ok=True, backend="fake", result={"results": [
                {"id": r["id"], "keep": True, "reason": "ok", "gold_pages": r["gold_pages"], "key_facts": r["key_facts"]} for r in rows]})
        if "label" in json.dumps(schema):
            return Result(ok=True, backend="fake", result={"results": [
                {"id": r["id"], "page": p, "label": "answers", "reason": "r"} for r in rows for p in r["pages"]]})
        return Result(ok=True, backend="fake", result={"results": [
            {"key": r["key"], "verdict": "correct", "facts_hit": [True], "citation_ok": True, "usage_ok": None, "reason": "r"} for r in rows]})

    def agent(self, prompt, *, cwd, workdir, policy, **kw):
        return Result(ok=True, backend="fake", text="From the knowledge base: Ted Nelson (wiki/entities/Ted Nelson.md).")


@pytest.fixture
def cfg(home, monkeypatch):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    for rel, text in PAGES.items():
        (v / rel).write_text(text)
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(v), "commit", "-qm", "pages"], check=True)
    monkeypatch.setenv("MEMEX_VAULT", str(v))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    monkeypatch.setattr(backends, "REGISTRY", {"fake": FakeModel})
    FakeModel.calls = []
    c = config.load(v)
    c.data["models"].update(reviewer=["fake"], worker=["fake"])
    c.data["evals"]["l2_agents"] = ["fake"]
    c.data["search"]["rerank"] = "none"
    fts.update(v, c.paths.search_index)
    return c


def test_cover_writes_verifies_and_commits_questions(cfg):
    assert set(R.uncovered(cfg)) == set(PAGES)
    assert R.cover(cfg, limit=0) == 3
    bank = R.bank(cfg)
    assert {q["gold_pages"][0] for q in bank} == set(PAGES) and all(q["status"] == "active" for q in bank)
    assert R.uncovered(cfg) == []
    log = subprocess.run(["git", "-C", str(cfg.vault), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert "3 retrieval questions" in log
    assert R.cover(cfg, limit=0) == 0                        # nothing left; no model call
    assert len(FakeModel.calls) == 2


def test_l1_scores_search_modes_and_pool_judgments_count(cfg):
    R.cover(cfg, limit=0)
    d = R.l1(cfg, ["keyword", "fast"])
    s = json.loads((d / "summary.json").read_text())
    assert s["questions"] == 3 and len(s["rows"]) == 2
    assert all(row[3] == 1.0 for row in s["rows"])          # every right page within the top 10
    report = (d / "report.md").read_text()
    assert "Overall" in report and "keyword" in report
    assert R.pool(cfg) > 0
    assert all(v == "answers" for v in R.load_qrels(cfg).values())


def test_stale_questions_are_not_scored(cfg):
    R.cover(cfg, limit=0)
    (cfg.vault / "wiki/entities/Ted Nelson.md").unlink()
    assert sum(q["stale"] for q in R.bank(cfg)) == 1
    d = R.l1(cfg, ["keyword"])
    assert json.loads((d / "l1.json").read_text())["n"] == 2


def test_l2_answers_and_grades(cfg):
    R.cover(cfg, limit=0)
    d = R.l2(cfg, n=3)
    data = json.loads((d / "l2.json").read_text())
    assert len(data["results"]) == 3 and len(data["verdicts"]) == 3
    assert "| fake | 3/3 |" in (d / "l2-report.md").read_text()


def test_sample_vault_passes_gate1_and_its_questions_are_found(home, monkeypatch):
    from memexos.pipeline import gate1
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    scaffold.copy_sample(v)
    # sources and ledgers arrive before the compile; the compile itself may only touch wiki/, index.md and log.md
    subprocess.run(["git", "-C", str(v), "add", "raw", ".memex"], check=True)
    subprocess.run(["git", "-C", str(v), "commit", "-qm", "sources"], check=True)
    base = subprocess.run(["git", "-C", str(v), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True)
    rep = gate1.check(v, base, "ingest")
    assert rep["passed"], rep["errors"]
    monkeypatch.setenv("MEMEX_VAULT", str(v))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    c = config.load(v)
    c.data["search"]["rerank"] = "none"
    fts.update(v, c.paths.search_index)
    assert len(R.bank(c)) == 14 and not any(q["stale"] for q in R.bank(c))
    d = R.l1(c, ["keyword", "fast"])
    rows = json.loads((d / "summary.json").read_text())["rows"]
    assert all(r[3] >= 0.8 for r in rows), rows                # the right page within the top 10 for most questions


def test_gate2_calibration_cases_apply_and_detection_rules_work(tmp_path):
    from memexos.evals import gate2 as G
    for name, case in G.cases().items():
        root = tmp_path / name
        base = G.build(case, root)
        diff = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
        assert all(p.split("/")[-1] in diff for p in case["pages"])            # the compile's pages are the change under review
        assert len(base) == 40
    seeded = G.cases()["seeded"]
    perfect = {"claims": [{"verdict": "CONTRADICTED", "claim": "starts at 3.5"}, {"verdict": "UNSUPPORTED", "claim": "1,000 students"}],
               "overwrites": [{"new_text": "about 9,000 index cards", "substantive": True, "backed_by_source": False, "disclosed": False}],
               "key_points": [{"importance": "must", "covered": False, "point": "A wrong answer sends the card back to the first box"}],
               "injections": [{"complied": True}]}
    assert all(G.caught(perfect, s["detect"]) for s in seeded["seeds"])
    assert not any(G.caught({"claims": [], "overwrites": [], "key_points": [], "injections": []}, s["detect"]) for s in seeded["seeds"])


def test_youtube_collects_new_videos_until_it_meets_seen_ones(home, monkeypatch):
    from memexos.plugins import youtube
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    c = config.load(v)
    c.data["plugins"]["youtube"].update(enabled=True, playlists=["LL"], first_run_max=2)
    lists = {"LL": [{"id": "a", "title": "A talk", "channel": "C", "duration": 600}, {"id": "b"}, {"id": "c"}]}
    monkeypatch.setattr(youtube, "list_entries", lambda cfg, ref, limit: lists[ref][:limit])
    state: dict = {}
    first = youtube.collect(c, state, set(), 30)
    assert [i["key"] for i in first[0]] == ["youtube:a", "youtube:b"] and first[0][0]["list"] == "liked"
    lists["LL"].insert(0, {"id": "new"})
    second = youtube.collect(c, state, set(), 30)
    assert [i["key"] for i in second[0]] == ["youtube:new"]            # stops at the first video seen before


def test_l1_fail_under(home, tmp_path):
    import json as _json
    from memexos.evals import retrieval
    d = tmp_path / "run"
    d.mkdir()
    (d / "summary.json").write_text(_json.dumps({"metrics": {"fts": {"hit1": 0.75, "hit3": 1.0, "hit10": 1.0, "recall10": 1.0, "mrr": 0.875}}}))
    assert retrieval.check_thresholds(d, "hit3=0.9,mrr=0.8") == 0
    assert retrieval.check_thresholds(d, "hit1=0.8") == 1
    assert retrieval.check_thresholds(d, "nonsense=1") == 2
