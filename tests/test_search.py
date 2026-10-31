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
    raw = next(h for h in out["hits"] if h["path"].startswith("raw/"))
    assert raw["compiled_page"] == "RRF paper"
    src = next(h for h in out["hits"] if h["path"] == "wiki/sources/RRF paper.md")
    assert "academic research" in src["classification"]
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
