"""memex doctor must tell the truth about what is broken, not just whether a program exists."""

import json
import os
from pathlib import Path

from memexos import config, doctor, scaffold
from memexos.backends import claude_code, codex

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"


def rows_for(cfg, label_part):
    return [r for r in doctor.checks(cfg) if label_part in r["label"]]


def test_doctor_reports_cli_not_logged_in(home, tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        (bin_dir / name).symlink_to(FAKE)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("FAKE_CLI_REPLY", json.dumps({"claude_logged_in": False, "codex_logged_in": True}))
    monkeypatch.setattr(claude_code.ClaudeCode, "_login", {})
    monkeypatch.setattr(codex.Codex, "_login", {})
    monkeypatch.setattr(codex, "user_config", lambda home: {})
    scaffold.init(home / "memex", "en")
    cfg = config.load(home / "memex")
    cc = rows_for(cfg, "claude-code:")[0]
    cx = rows_for(cfg, "codex:")[0]
    assert not cc["ok"] and "not logged in" in cc["hint"]
    assert cx["ok"]
    writer = [r for r in doctor.checks(cfg) if r["label"].startswith("writer")][0]
    assert not writer["ok"] and writer["required"]          # the writer's only backend is unusable: required item fails


def test_doctor_reports_missing_cli_and_search_state(home, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    scaffold.init(home / "memex", "en")
    cfg = config.load(home / "memex")
    rows = doctor.checks(cfg)
    cc = [r for r in rows if r["label"].startswith("claude-code")][0]
    assert not cc["ok"] and "install" in cc["hint"].lower()
    fts = [r for r in rows if "full-text index" in r["label"]][0]
    assert not fts["ok"]                                     # nothing indexed yet
    code = doctor.main(lambda v=None: cfg, [])
    assert code == 1                                         # required items missing → non-zero exit
