import json
import os
import stat

import pytest

from memexos import config, scaffold
from memexos.cli import main
from memexos.search import engine, fts
from memexos.search import text as T


def test_tokens_mix_scripts():
    assert T.tokens("检索重排 BM25 works") == ["检索", "索重", "重排", "bm25", "works"]
    assert T.tokens("单") == ["单"]


def test_keywords_drop_fillers():
    zh = T.keywords("为什么门 2 不通过时会重做？")
    assert "重做" in zh and not any("为什么" in k for k in zh)
    kws = T.keywords("How does reciprocal rank fusion combine lists?")
    assert "reciprocal" in kws and "how" not in [k.lower() for k in kws]


@pytest.fixture
def vault(home):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    (v / "wiki/concepts/Reciprocal rank fusion.md").write_text(
        "---\ntags: [concept]\n---\n# Reciprocal rank fusion\n\nCombines ranked lists by summing 1/(k + rank). "
        "Robust when backends have incomparable scores.\n\n## Sources\n- `raw/2026-01-02-web-example-rrf.md`\n")
    (v / "wiki/concepts/检索重排.md").write_text("# 检索重排\n\n重排模型读问题和页面，判断页面是否包含答案。\n")
    (v / "wiki/sources/RRF paper.md").write_text("---\ndomain: AI/Models and research\ncontent_type: paper\nevidence: academic research\n"
                                                  "---\n# RRF paper\n\nSummary.\n\n## Sources\n- `raw/2026-01-02-web-example-rrf.md`\n")
    (v / "raw/2026-01-02-web-example-rrf.md").write_text("---\nsource_type: web\ntitle: RRF\n---\n# RRF\n\nrank fusion text\n")
    (v / "inbox/2026-01-02-ingest.md").write_text("## [FYI] reciprocal rank fusion note\n")
    return v


def test_fts_index_and_search(vault, home):
    cfg = config.load(vault)
    r = fts.update(vault, cfg.paths.search_index)
    assert r["added"] >= 4
    assert fts.update(vault, cfg.paths.search_index)["added"] == 0          # incremental: nothing changed
    hits = fts.search(vault, cfg.paths.search_index, ["reciprocal rank fusion"], 5)
    assert hits[0]["path"] == "wiki/concepts/Reciprocal rank fusion.md"
    zh = fts.search(vault, cfg.paths.search_index, ["重排"], 5)
    assert zh and zh[0]["path"] == "wiki/concepts/检索重排.md"


def test_search_without_index_scans_files(vault):
    cfg = config.load(vault)
    degraded = []
    hits = fts.search(vault, cfg.paths.search_index, ["reciprocal rank fusion"], 5, degraded)
    assert hits and hits[0]["path"].endswith("Reciprocal rank fusion.md")
    assert degraded  # the fallback is reported, never silent


def test_engine_excludes_inbox_by_default(vault, monkeypatch):
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    cfg = config.load(vault)
    fts.update(vault, cfg.paths.search_index)
    hits, kws, _ = engine.search(cfg, "reciprocal rank fusion", n=10)
    assert not any(h["path"].startswith("inbox/") for h in hits)
    hits, _, _ = engine.search(cfg, "reciprocal rank fusion", n=10, include_inbox=True)
    assert any(h["path"].startswith("inbox/") for h in hits)


def test_rrf():
    a = [{"path": "x"}, {"path": "y"}]
    b = [{"path": "y"}, {"path": "z"}]
    assert [h["path"] for h in engine.rrf([a, b])][0] == "y"


def test_cli_search_get_reindex(vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    assert main(["reindex"]) == 0
    capsys.readouterr()
    assert main(["search", "how is RRF used", "--kw", "rank fusion", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    paths = [h["path"] for h in out["hits"]]
    assert "wiki/concepts/Reciprocal rank fusion.md" in paths
    assert not [h for h in out["hits"] if h["path"].startswith("raw/")]      # its compiled page is listed instead
    src = next(h for h in out["hits"] if h["path"] == "wiki/sources/RRF paper.md")
    assert "academic research" in src["classification"] and src["sources"] == 1
    rrf = next(h for h in out["hits"] if h["path"] == "wiki/concepts/Reciprocal rank fusion.md")
    assert rrf["matched"] == ["rank", "fusion"] or "rank" in rrf["matched"]
    assert main(["get", "reciprocal rank fusion", "--lines", "3"]) == 0
    assert "Reciprocal rank fusion" in capsys.readouterr().out


def test_search_works_in_read_only_sandbox(vault, monkeypatch, capsys):
    """Agents may run search where nothing is writable: no state dir, read-only index."""
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    cfg = config.load(vault)
    fts.update(vault, cfg.paths.search_index)
    db = cfg.paths.search_index
    os.chmod(db.parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert main(["search", "reciprocal", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["hits"]
    finally:
        os.chmod(db.parent, stat.S_IRWXU)


def test_the_subject_word_counts_even_when_most_pages_have_it(tmp_path):
    """SQLite's bm25 gives a term on more than half of the pages almost no weight; in a small knowledge base about one
    subject that is the subject itself, and a rare word like "who" used to win. Keywords keep a non-zero weight and
    filler words are not searched."""
    from memexos.search import fts, text as T
    assert fts.idf(5, 4, True) > 0.1 and fts.idf(5, 4, False) < 1e-3
    v = tmp_path / "v"
    (v / "wiki" / "concepts").mkdir(parents=True)
    (v / "raw").mkdir()
    pages = {"Leitner system": "Flashcards sorted into boxes; a wrong answer sends a card back to the first box.",
             "Hermann Ebbinghaus": "A German psychologist who published the first systematic experiments on forgetting.",
             "Spaced repetition": "Leitner boxes and SM-2 both schedule reviews; Leitner used paper.",
             "SM-2": "SM-2 is not Leitner; it multiplies intervals.",
             "Anki": "Anki replaced paper Leitner boxes for many people."}
    for name, body in pages.items():
        (v / "wiki" / "concepts" / f"{name}.md").write_text(f"# {name}\n\n{body}\n")
    db = tmp_path / "i.sqlite"
    fts.update(v, db)
    q = "who invented the leitner box system"
    kws = T.keywords(q)
    hits = fts.search(v, db, list(dict.fromkeys(kws + [q])), 3, None, kws)
    assert hits[0]["path"] == "wiki/concepts/Leitner system.md"


def test_sqlite_varints():
    from memexos.search.fts import _varints
    assert _varints(bytes([0x02, 0x04])) == [2, 4]
    assert _varints(bytes([0x81, 0x00])) == [128]
    assert _varints(bytes([0x82, 0x2C, 0x05])) == [300, 5]


def test_qmd_download_route(home, monkeypatch):
    from memexos.search import qmd
    cfg = config.load(home / "v")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("NODE_USE_ENV_PROXY", raising=False)
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    env = qmd._env(cfg)
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080" and env["NODE_USE_ENV_PROXY"] == "1"
    cfg.data["search"]["qmd"]["proxy"] = "direct"
    assert "HTTPS_PROXY" not in qmd._env(cfg)
    cfg.data["search"]["qmd"]["proxy"] = "http://other.example:3128"
    cfg.data["fetch"]["proxy_cacert"] = "~/ca.pem"
    env = qmd._env(cfg)
    assert env["https_proxy"] == "http://other.example:3128" and env["NODE_EXTRA_CA_CERTS"] == os.path.expanduser("~/ca.pem")


def test_a_raw_file_shows_its_page_unless_the_page_is_listed():
    from memexos.search.cli import dedupe
    hits = [{"path": "raw/a.md", "compiled_page": "A"}, {"path": "wiki/sources/A.md"}, {"path": "raw/b.md", "compiled_page": "B"}]
    assert [h["path"] for h in dedupe(hits)] == ["wiki/sources/A.md", "raw/b.md"]


def test_get_accepts_what_agents_paste(vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    for form in ("[[Reciprocal rank fusion]]", "[[Reciprocal rank fusion|RRF]]", "`reciprocal rank fusion`",
                 "wiki/concepts/Reciprocal rank fusion", "Reciprocal rank fusion  (wiki/concepts/Reciprocal rank fusion.md)",
                 "\"Reciprocal rank fusion\"", "[[Reciprocal rank fusion#Sources]]"):
        assert main(["get", form, "--lines", "1"]) == 0, form
        assert "Reciprocal rank fusion" in capsys.readouterr().out
    assert main(["get", "Reciprocal rank fussion"]) == 1
    assert "Reciprocal rank fusion" in capsys.readouterr().err                # suggested
    assert main(["get", "../../etc/passwd"]) == 1


def test_feedback_needs_a_page_that_exists(vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    assert main(["feedback", "Reciprocal rank fussion", "--kind", "incorrect", "--why", "x"]) == 1
    assert "Reciprocal rank fusion" in capsys.readouterr().err
    assert main(["feedback", "[[Reciprocal rank fusion]]", "--kind", "outdated", "--why", "x"]) == 0
    assert "wiki/concepts/Reciprocal rank fusion.md" in capsys.readouterr().out
    assert main(["feedback", "Vector databases", "--kind", "missing", "--why", "no page about them"]) == 0


def test_excerpt_is_where_the_keywords_meet():
    body = "Unrelated opening about rank. " * 30 + "Reciprocal rank fusion sums 1/(k + rank) over lists. " + "tail " * 100
    snip = T.snippet(body, ["fusion", "rank", "how are lists combined"])
    assert "Reciprocal rank fusion" in snip
