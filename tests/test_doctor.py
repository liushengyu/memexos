"""memex doctor must tell the truth about what is broken, not just whether a program exists."""

import json
import os
import sys
from pathlib import Path

from memexos import config, doctor, scaffold
from memexos.backends import claude_code, codex

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"


def fake_bin(tmp_path, scripts=None):
    """Stand-ins for `claude` and `codex` that run the fake with this interpreter by absolute path, so the tests do not
    depend on a `python3` being in the minimal PATH they set (python images keep it in /usr/local/bin, uv elsewhere)."""
    b = tmp_path / "fakebin"
    b.mkdir()
    for name in ("claude", "codex"):
        w = b / name
        w.write_text((scripts or {}).get(name) or f'#!/bin/sh\nFAKE_CLI_NAME={name} exec "{sys.executable}" "{FAKE}" "$@"\n')
        w.chmod(0o755)
    return b


def rows_for(cfg, label_part):
    return [r for r in doctor.checks(cfg) if label_part in r["label"]]


def test_doctor_reports_cli_not_logged_in(home, tmp_path, monkeypatch):
    bin_dir = fake_bin(tmp_path)
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


def test_doctor_reports_a_cli_that_cannot_start(home, tmp_path, monkeypatch):
    # an interpreter that is not there (the kernel refuses to start it) and a shell that cannot find its program
    bin_dir = fake_bin(tmp_path, {"claude": "#!/nonexistent/python3\n", "codex": "#!/bin/sh\necho 'env: python3: not found' >&2\nexit 127\n"})
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(claude_code.ClaudeCode, "_login", {})
    monkeypatch.setattr(codex.Codex, "_login", {})
    monkeypatch.setattr(codex, "user_config", lambda home: {})
    scaffold.init(home / "memex", "en")
    cfg = config.load(home / "memex")
    cc, cx = rows_for(cfg, "claude-code:")[0], rows_for(cfg, "codex:")[0]
    assert not cc["ok"] and "does not start" in cc["hint"]               # not "logged in, probably"
    assert not cx["ok"] and "does not start" in cx["hint"] and "not found" in cx["hint"]


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


def test_doctor_json_is_only_json(home, monkeypatch, capsys):
    import json
    from memexos.cli import main
    monkeypatch.setenv("MEMEX_VAULT", str(home / "v"))
    main(["doctor", "--json"])
    out = capsys.readouterr().out
    rows = json.loads(out)
    assert isinstance(rows, list) and all({"section", "label", "ok", "required", "hint"} <= set(r) for r in rows)
