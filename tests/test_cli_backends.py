"""The exact command lines MemexOS builds for the Claude Code and Codex command lines, checked against a stand-in
that records what it was called with (tests/fakes/fake_cli.py), plus how their answers are read.

The flags themselves are checked against the real tools in tests/test_cli_flags.py, and real calls with logged-in
tools in tests/test_cli_live.py.
"""

import json
import os
from pathlib import Path

import pytest

from memexos import backends, config
from memexos.backends import claude_code, codex
from memexos.backends.base import SECRET_PATHS, Policy

FAKE = Path(__file__).parent / "fakes" / "fake_cli.py"


@pytest.fixture
def fake(home, tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        (bin_dir / name).symlink_to(FAKE)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.delenv("FAKE_CLI_REPLY", raising=False)
    monkeypatch.setattr(claude_code.ClaudeCode, "_login", {})
    monkeypatch.setattr(codex.Codex, "_login", {})
    monkeypatch.setattr(codex, "user_config", lambda home: {})
    cfg = config.load(home / "v")

    def calls(tool=None):
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in rows if tool is None or r["tool"] == tool]

    def reply(**kw):
        monkeypatch.setenv("FAKE_CLI_REPLY", json.dumps(kw))
    return cfg, calls, reply


def flag_value(argv, flag):
    return argv[argv.index(flag) + 1]


def values_after(argv, flag):
    """The values that follow a flag up to the next flag (--allowedTools a b c --next)."""
    i = argv.index(flag) + 1
    out = []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}


# ---------------------------------------------------------------- Claude Code
def test_claude_structured_command_and_result(fake, tmp_path):
    cfg, calls, reply = fake
    reply(structured={"ok": True})
    work = tmp_path / "stage"
    work.mkdir()
    r = backends.get(cfg, "claude-code").structured("Review this.", SCHEMA, cwd=work, workdir=tmp_path / "run", extra="DIFF-TEXT")
    assert r.ok and r.result == {"ok": True}
    assert r.cost_usd == 0.0123 and r.model == "claude-test-model" and r.usage["input_tokens"] == 100
    call = [c for c in calls("claude") if c["argv"][0] == "-p"][-1]
    argv = call["argv"]
    assert argv[0] == "-p" and call["cwd"] == str(work.resolve())
    assert flag_value(argv, "--permission-mode") == "dontAsk"
    assert flag_value(argv, "--setting-sources") == "project"
    assert "--strict-mcp-config" in argv and flag_value(argv, "--mcp-config") == '{"mcpServers":{}}'
    assert "--no-session-persistence" in argv and flag_value(argv, "--output-format") == "json"
    assert json.loads(flag_value(argv, "--json-schema")) == SCHEMA
    allowed = values_after(argv, "--allowedTools")
    assert f"Read(/{work.resolve()}/**)" in allowed and "Read" not in allowed       # reads scoped to the work folder
    assert not any(a.startswith(("Edit", "Write", "Bash")) for a in allowed)      # a structured call cannot change anything
    denied = values_after(argv, "--disallowedTools")
    assert all(f"Read({p})" in denied for p in SECRET_PATHS)
    assert "Review this." in call["stdin"] and "DIFF-TEXT" in call["stdin"]


def test_claude_agent_command_follows_the_policy(fake, tmp_path):
    cfg, calls, reply = fake
    reply(result="written")
    stage = tmp_path / "stage"
    (stage / "wiki").mkdir(parents=True)
    extra = tmp_path / "docs"
    extra.mkdir()
    pol = Policy(write=[stage / "wiki", stage / "index.md"], read=[extra], commands=[["git", "-C"]], web=True,
                 deny_read=["~/private/**"])
    r = backends.get(cfg, "claude-code").agent("Write pages.", cwd=stage, workdir=tmp_path / "run", policy=pol)
    assert r.ok and r.result == "written"
    argv = [c for c in calls("claude") if c["argv"][0] == "-p"][-1]["argv"]
    allowed = values_after(argv, "--allowedTools")
    assert f"Edit(/{(stage / 'wiki').resolve()}/**)" in allowed and f"Write(/{(stage / 'index.md').resolve()})" in allowed
    assert f"Read(/{stage.resolve()}/**)" in allowed and f"Read(/{extra.resolve()}/**)" in allowed and "Read" not in allowed
    assert "Bash(git -C *)" in allowed and "WebSearch" in allowed and "WebFetch" in allowed
    assert flag_value(argv, "--tools").split(",") == ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "WebSearch", "WebFetch"]
    denied = values_after(argv, "--disallowedTools")
    assert "Read(~/private/**)" in denied and "Read(~/.ssh/**)" in denied
    assert str(extra.resolve()) in [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]


def test_claude_denials_and_errors_are_reported(fake, tmp_path):
    cfg, calls, reply = fake
    reply(result="partial", denials=[{"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}}])
    r = backends.get(cfg, "claude-code").agent("x", cwd=tmp_path, workdir=tmp_path / "run", policy=Policy())
    assert r.denials == [{"tool": "Write", "input": "/etc/passwd"}]
    reply(is_error=True, result="Claude AI usage limit reached")
    r = backends.get(cfg, "claude-code").structured("x", SCHEMA, cwd=tmp_path, workdir=tmp_path / "run2")
    assert not r.ok and r.limited


def test_claude_not_logged_in_hands_over_to_the_next_backend(fake, tmp_path):
    cfg, calls, reply = fake
    reply(claude_logged_in=False, structured={"ok": True})
    ok, why = backends.get(cfg, "claude-code").available()
    assert not ok and "claude" in why and "not logged in" in why
    cfg.data["models"]["worker"] = ["claude-code", "codex"]
    r = backends.structured(cfg, "worker", "x", SCHEMA, cwd=tmp_path, workdir=tmp_path / "run")
    assert r.ok and r.backend == "codex"
    assert not [c for c in calls("claude") if c["argv"][0] == "-p"]


# ---------------------------------------------------------------- Codex
def test_codex_structured_command_gets_the_precomputed_context(fake, tmp_path):
    cfg, calls, reply = fake
    reply(structured={"ok": False})
    stage = tmp_path / "stage"
    stage.mkdir()
    r = backends.get(cfg, "codex").structured("Review this.", SCHEMA, cwd=stage, workdir=tmp_path / "run", extra="DIFF-TEXT")
    assert r.ok and r.result == {"ok": False} and r.usage["input_tokens"] == 100
    call = [c for c in calls("codex") if c["argv"][0] == "exec"][-1]
    argv = call["argv"]
    assert argv[:3] == ["exec", "--ignore-user-config", "--ignore-rules"]
    assert flag_value(argv, "-s") == "read-only" and flag_value(argv, "-C") == str(stage)
    assert "--ephemeral" in argv and "--json" in argv and "--skip-git-repo-check" in argv
    assert json.loads(Path(flag_value(argv, "--output-schema")).read_text()) == SCHEMA
    assert argv[-1] == "-" and "-o" in argv
    assert "Review this." in call["stdin"] and "DIFF-TEXT" in call["stdin"]          # the diff reaches the reviewer


def test_codex_agent_sandbox_and_extra_folders(fake, tmp_path):
    cfg, calls, reply = fake
    reply(result="done")
    stage = tmp_path / "stage"
    stage.mkdir()
    out = tmp_path / "elsewhere"
    out.mkdir()
    pol = Policy(write=[stage / "wiki", out], web=True)
    r = backends.get(cfg, "codex").agent("Do it.", cwd=stage, workdir=tmp_path / "run", policy=pol)
    assert r.ok
    argv = [c for c in calls("codex") if c["argv"][0] == "exec"][-1]["argv"]
    assert flag_value(argv, "-s") == "workspace-write"
    assert str(out.resolve()) in [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert "sandbox_workspace_write.network_access=true" in argv and "tools.web_search=true" in argv
    assert "sandbox_workspace_write.exclude_tmpdir_env_var=true" in argv and "sandbox_workspace_write.exclude_slash_tmp=true" in argv


def test_codex_quota_and_login(fake, tmp_path):
    cfg, calls, reply = fake
    reply(error="You've hit your usage limit. Try again in 3 hours.")
    r = backends.get(cfg, "codex").structured("x", SCHEMA, cwd=tmp_path, workdir=tmp_path / "run")
    assert not r.ok and r.limited
    codex.Codex._login.clear()
    reply(logged_in=False)
    ok, why = backends.get(cfg, "codex").available()
    assert not ok and "codex login" in why


def test_codex_user_provider_disables_isolation(fake, tmp_path, monkeypatch):
    cfg, calls, reply = fake
    monkeypatch.setattr(codex, "user_config", lambda home: {"model_provider": "my-gateway", "model": "gw-model"})
    reply(structured={"ok": True})
    backends.get(cfg, "codex").structured("x", SCHEMA, cwd=tmp_path, workdir=tmp_path / "run")
    argv = [c for c in calls("codex") if c["argv"][0] == "exec"][-1]["argv"]
    assert "--ignore-user-config" not in argv and flag_value(argv, "-m") == "gw-model"
