"""Every flag MemexOS passes to the real `claude` and `codex` command lines must still exist in them.

The command lines are captured from the stand-in (tests/fakes/fake_cli.py) exactly as tests/test_cli_backends.py
builds them, then each flag is looked up in the real tool's --help. The tools change often; a renamed or removed
flag would otherwise make every scheduled run fail. Skipped when a real tool is not installed, unless
MEMEX_REAL_CLI=1 (CI installs both and sets it).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from memexos import backends, config
from memexos.backends import claude_code, codex
from memexos.backends.base import Policy

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


def real_tool(name: str, fake_dir: Path) -> str | None:
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if p and Path(p) != fake_dir)
    return shutil.which(name, path=path)


def help_text(*cmd: str) -> str:
    from conftest import ORIGINAL_ENV
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, env=ORIGINAL_ENV)
    return (r.stdout or "") + (r.stderr or "")


def flags(argv: list[str]) -> set[str]:
    return {a.split("=", 1)[0] for a in argv if re.fullmatch(r"--?[A-Za-z][\w-]*(=.*)?", a)}


@pytest.fixture
def captured(home, tmp_path, monkeypatch):
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    for name in ("claude", "codex"):
        (fake_dir / name).symlink_to(FAKE)
    log = tmp_path / "calls.jsonl"
    real = {name: real_tool(name, fake_dir) for name in ("claude", "codex")}
    monkeypatch.setenv("PATH", f"{fake_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setenv("FAKE_CLI_REPLY", json.dumps({"structured": {"ok": True}, "result": "ok"}))
    monkeypatch.setattr(claude_code.ClaudeCode, "_login", {})
    monkeypatch.setattr(codex.Codex, "_login", {})
    monkeypatch.setattr(codex, "user_config", lambda home: {})
    cfg = config.load(home / "v")
    cfg.data["backends"]["claude-code"]["model"] = "sonnet"
    cfg.data["backends"]["codex"]["model"] = "gpt-test"
    work = tmp_path / "stage"
    (work / "wiki").mkdir(parents=True)
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8")
    pol = Policy(write=[work / "wiki"], read=[tmp_path], commands=[["git", "-C"]], web=True, deny_read=["~/x/**"])
    for name in ("claude-code", "codex"):
        b = backends.get(cfg, name)
        b.available()
        b.structured("q", SCHEMA, cwd=work, workdir=tmp_path / f"{name}-s", images=[img], extra="ctx")
        b.agent("q", cwd=work, workdir=tmp_path / f"{name}-a", policy=pol, schema=SCHEMA, images=[img])
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    return rows, real


def _require(real: str | None, name: str):
    if real:
        return
    if os.environ.get("MEMEX_REAL_CLI") == "1":
        pytest.fail(f"the real `{name}` command line is required here (MEMEX_REAL_CLI=1) but was not found")
    pytest.skip(f"`{name}` is not installed")


def test_claude_flags_exist_in_the_real_cli(captured):
    rows, real = captured
    _require(real["claude"], "claude")
    main_help = help_text(real["claude"], "--help")
    used = set()
    for r in rows:
        if r["tool"] == "claude" and r["argv"][:1] == ["-p"]:
            used |= flags(r["argv"])
    assert {"-p", "--json-schema", "--permission-mode", "--allowedTools", "--disallowedTools", "--setting-sources"} <= used
    missing = sorted(f for f in used if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", main_help))
    assert not missing, f"flags MemexOS passes to claude that `claude --help` no longer lists: {missing}"
    assert "dontAsk" in main_help, "permission mode dontAsk is no longer offered"
    auth_help = help_text(real["claude"], "auth", "status", "--help")
    assert "--json" in auth_help, "`claude auth status --json` (the login check) is gone"


def test_codex_flags_exist_in_the_real_cli(captured):
    rows, real = captured
    _require(real["codex"], "codex")
    exec_help = help_text(real["codex"], "exec", "--help")
    used = set()
    for r in rows:
        if r["tool"] == "codex" and r["argv"][:1] == ["exec"]:
            used |= flags(r["argv"][1:])
    assert {"--output-schema", "--ephemeral", "--json", "-s", "-C", "-o"} <= used
    missing = sorted(f for f in used if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", exec_help))
    assert not missing, f"flags MemexOS passes to `codex exec` that its --help no longer lists: {missing}"
    for mode in ("read-only", "workspace-write"):
        assert mode in exec_help, f"sandbox mode {mode} is no longer offered"
    login_help = help_text(real["codex"], "login", "--help")
    assert "status" in login_help, "`codex login status` (the login check) is gone"
