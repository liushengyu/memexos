"""The search indexes stay usable without anyone looking after them: a damaged full-text index is found and rebuilt,
a reindex asked for while one is running is not dropped, and the vector index follows the embedding model setting."""

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from memexos import config, lock, scaffold
from memexos.cli import main
from memexos.search import fts, qmd


@pytest.fixture
def vault(home):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    for i in range(400):
        (v / f"wiki/concepts/Page {i}.md").write_text(f"# Page {i}\n\n" + f"alpha{i} beta gamma{i} " * 40 + "\n")
    (v / "wiki/concepts/Spaced repetition.md").write_text("# Spaced repetition\n\nReview at growing intervals.\n")
    return v


def _search(v, db, degraded=None):
    return [h["path"] for h in fts.search(v, db, ["spaced repetition"], 5, degraded)]


def test_a_file_that_is_not_a_database_is_rebuilt(vault):
    db = config.load(vault).paths.search_index
    fts.update(vault, db)
    db.write_bytes(b"this is not sqlite" * 100)
    degraded = []
    assert _search(vault, db, degraded) == ["wiki/concepts/Spaced repetition.md"]      # the file scan still answers
    assert "damaged" in degraded[0] and fts.damage_note(db).exists()
    r = fts.update(vault, db)
    assert r["rebuilt"] and r["docs"] >= 401
    assert not fts.damage_note(db).exists() and db.with_name(db.name + ".corrupt").exists()
    assert _search(vault, db) == ["wiki/concepts/Spaced repetition.md"]


def test_silent_damage_is_found_by_the_daily_check(vault):
    """Damage inside the full-text tables can leave plain queries working with pages missing. The daily check finds it."""
    db = config.load(vault).paths.search_index
    fts.update(vault, db)
    b = bytearray(db.read_bytes())
    mid = len(b) // 2
    b[mid:mid + 8192] = b"\xab" * 8192
    db.write_bytes(bytes(b))
    assert fts.check(db)                                           # SQLite reports the damage
    r = fts.update(vault, db)
    assert not r.get("rebuilt")                                    # checked less than a day ago: not looked at yet
    c = sqlite3.connect(db)
    c.execute("UPDATE meta SET v='0' WHERE k='checked'")
    c.commit()
    c.close()
    r = fts.update(vault, db)
    assert r.get("rebuilt") and fts.check(db) == ""
    assert _search(vault, db) == ["wiki/concepts/Spaced repetition.md"]


def test_a_healthy_index_is_checked_once_a_day_and_left_alone(vault, monkeypatch):
    db = config.load(vault).paths.search_index
    fts.update(vault, db)
    calls = []
    monkeypatch.setattr(fts, "check", lambda d: calls.append(d) or "")
    fts.update(vault, db)
    assert not calls
    monkeypatch.setattr(fts, "CHECK_EVERY", -1)
    r = fts.update(vault, db)
    assert calls and "rebuilt" not in r and r["added"] == 0                     # incremental, not rebuilt


def test_reindex_cli_reports_a_rebuild(vault, monkeypatch, capsys):
    monkeypatch.setenv("MEMEX_VAULT", str(vault))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    db = config.load(vault).paths.search_index
    assert main(["reindex"]) == 0
    db.write_bytes(b"\0" * 4096)
    capsys.readouterr()
    assert main(["reindex"]) == 0
    assert "damaged" in capsys.readouterr().out
    events = (config.load(vault).paths.state / "events").glob("*.jsonl")
    names = [json.loads(line)["type"] for f in events for line in f.read_text().splitlines()]
    assert "index.rebuilt" in names


def test_a_read_only_sandbox_still_answers_from_a_damaged_index(vault):
    db = config.load(vault).paths.search_index
    fts.update(vault, db)
    db.write_bytes(b"garbage" * 600)
    os.chmod(db.parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        degraded = []
        assert _search(vault, db, degraded) == ["wiki/concepts/Spaced repetition.md"]
        assert "damaged" in degraded[0]
    finally:
        os.chmod(db.parent, stat.S_IRWXU)


# ---------------------------------------------------------------- one more run instead of a dropped one
def test_a_request_during_a_run_makes_the_holder_run_once_more(tmp_path):
    runs = []

    def work():
        runs.append(time.time())
        if len(runs) == 1:                     # two more requests arrive while the first run is under way
            assert lock.coalesced(tmp_path, "reindex", work) is False
            assert lock.coalesced(tmp_path, "reindex", work) is False
    assert lock.coalesced(tmp_path, "reindex", work) is True
    assert len(runs) == 2                      # one extra run covers both requests
    assert not (tmp_path / "reindex.again").exists()


def test_a_request_with_nobody_running_runs_once(tmp_path):
    runs = []
    assert lock.coalesced(tmp_path, "reindex", lambda: runs.append(1)) is True
    assert runs == [1]


def test_requests_that_never_stop_do_not_loop_forever(tmp_path):
    runs = []

    def work():
        runs.append(1)
        (tmp_path / "reindex.again").touch()
    lock.coalesced(tmp_path, "reindex", work, max_rounds=3)
    assert len(runs) == 3


# ---------------------------------------------------------------- qmd's index file and embedding model
FAKE_QMD = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_QMD_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
print("Indexed: 3 new" if "update" in sys.argv else "Embedded 3 chunks")
"""


@pytest.fixture
def fake_qmd(vault, tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "qmd"
    exe.parent.mkdir()
    exe.write_text(FAKE_QMD)
    exe.chmod(0o755)
    log = tmp_path / "qmd.jsonl"
    monkeypatch.setenv("PATH", f"{exe.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_QMD_LOG", str(log))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def make_db(cfg):
        qmd.db_file(cfg).parent.mkdir(parents=True, exist_ok=True)
        qmd.db_file(cfg).write_bytes(b"")
    return calls, make_db


def test_index_file_follows_the_settings(vault, fake_qmd):
    calls, make_db = fake_qmd
    cfg = config.load(vault)
    cfg.data["language"] = "zh"
    assert qmd.ensure_index(cfg) == "created"
    text = qmd.config_file(cfg).read_text()
    assert qmd.QWEN3_EMBED in text and str(vault) in text and '"log.md"' in text
    assert qmd.ensure_index(cfg) == "unchanged"
    cfg.data["search"]["qmd"]["ignore"] = ["raw/transcripts/**"]
    cfg.data["language"] = "en"                        # auto: English keeps qmd's default model
    assert qmd.ensure_index(cfg) == "updated"
    text = qmd.config_file(cfg).read_text()
    assert "raw/transcripts/**" in text and "models:" not in text


def test_changing_the_embedding_model_embeds_everything_again(vault, fake_qmd):
    calls, make_db = fake_qmd
    cfg = config.load(vault)
    make_db(cfg)
    assert qmd.update(cfg)["ok"]
    assert ["--index", qmd.index_name(cfg), "embed"] in calls()
    assert qmd.ready(cfg)
    n = len(calls())
    cfg.data["search"]["qmd"]["embed_model"] = "hf:example/other-embedding.gguf"
    r = qmd.update(cfg)
    assert r["model_changed"] == "hf:example/other-embedding.gguf"
    assert calls()[n:][-1] == ["--index", qmd.index_name(cfg), "embed", "-f"]
    assert "hf:example/other-embedding.gguf" in qmd.config_file(cfg).read_text()
    assert qmd.ready(cfg)
    n = len(calls())
    qmd.update(cfg)                                        # same model again: an ordinary incremental embed
    assert calls()[n:][-1] == ["--index", qmd.index_name(cfg), "embed"]


def test_search_skips_vectors_while_a_new_model_is_embedding(vault, fake_qmd):
    calls, make_db = fake_qmd
    cfg = config.load(vault)
    make_db(cfg)
    qmd.ensure_index(cfg)
    qmd._save_marker(cfg, "hf:new.gguf", embedded=False)
    assert not qmd.ready(cfg)
    qmd._save_marker(cfg, "hf:new.gguf", embedded=True)
    assert qmd.ready(cfg)


def test_an_index_embedded_before_the_model_was_recorded_is_not_redone(vault, fake_qmd):
    calls, make_db = fake_qmd
    cfg = config.load(vault)
    make_db(cfg)
    qmd.ensure_index(cfg)
    qmd.marker(cfg).parent.mkdir(parents=True, exist_ok=True)
    qmd.marker(cfg).write_text(json.dumps({"index": qmd.index_name(cfg), "embedded_at": "2026-11-01T00:00:00"}))
    assert qmd.ready(cfg)
    r = qmd.update(cfg)
    assert "model_changed" not in r and calls()[-1][-1] == "embed"


def test_the_shipped_default_picks_the_model_by_language(home):
    cfg = config.load(home / "v")
    assert cfg.get("search.qmd.embed_model") == "auto"
    cfg.data["language"] = "zh"
    assert qmd.embed_model(cfg) == qmd.QWEN3_EMBED
    cfg.data["language"] = "en"
    assert qmd.embed_model(cfg) == ""


def test_paths_are_plain(tmp_path):
    assert isinstance(fts.damage_note(Path(tmp_path / "x.sqlite")), Path)
