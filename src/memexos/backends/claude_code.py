"""Backend: the Claude Code command line (`claude -p`), using whatever account it is logged in with.

Unattended runs load no user hooks, plugins or MCP servers (`--setting-sources project`, an empty MCP config),
keep no session history, and only get the tools the Policy allows. Tool calls the permission system refuses do
not fail the run; they are returned in Result.denials so callers can log what capability was missing.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from memexos import i18n
from memexos.backends.base import (LOGIN_TTL, NETWORK_RE, QUOTA_RE, READ_ONLY_NOTE, SECRET_PATHS, TRANSIENT_STATUS, Backend, Policy,
                                   Result, parse_json_text, start_failure)
from memexos.util import proxy_env, run, which

EFFORTS = ("low", "medium", "high", "xhigh", "max")
BASE_FLAGS = ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "project",
              "--no-session-persistence", "--output-format", "json"]


def _proxy(section: dict) -> str | None:
    p = (section.get("proxy") or "").strip()
    return None if not p else "" if p == "direct" else p


def _settings_env() -> dict:
    """The "env" block of the Claude Code user settings (where some setups keep their API gateway and token)."""
    import os
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    try:
        env = json.loads(Path(base, "settings.json").read_text(encoding="utf-8")).get("env") or {}
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in env.items() if k.isidentifier()}


def _rule(p) -> str:
    """A permission-rule path: "//abs/path/**" for a directory (Claude Code reads "//" as an absolute path)."""
    p = Path(p).resolve()
    return f"/{p}/**" if p.is_dir() or not p.suffix else f"/{p}"


def same_family(want: str, used: str) -> bool:
    """'opus', 'opus[1m]', 'claude-opus-5-5' all ask for an Opus model; a full id must match the family it names."""
    w = re.sub(r"\[.*\]$", "", want).lower()
    family = next((f for f in ("opus", "sonnet", "haiku") if f in w), w)
    return family in used.lower()


class ClaudeCode(Backend):
    name = "claude-code"
    _login: dict = {}            # exe → (checked at, ok, why)

    def exe(self) -> str | None:
        return which(self.section.get("command") or "claude")

    def env(self) -> dict:
        env = dict(__import__("os").environ)
        if self.section.get("settings_env"):
            env.update(_settings_env())
        return proxy_env(env, _proxy(self.section))

    def available(self) -> tuple[bool, str]:
        exe = self.exe()
        if not exe:
            return False, i18n.t("backend.cmd_missing", cmd=self.section.get("command") or "claude")
        if self.section.get("check_login", True) is False:
            return True, ""
        hit = ClaudeCode._login.get(exe)
        if hit and hit[1] and time.time() - hit[0] < LOGIN_TTL:
            return True, ""
        ok, why = self._check_login(exe)
        ClaudeCode._login[exe] = (time.time(), ok, why)
        return ok, why

    def _check_login(self, exe: str) -> tuple[bool, str]:
        """`claude auth status --json` with the environment the runs will have. An older CLI without that command is
        given the benefit of the doubt."""
        try:
            r = run([exe, "auth", "status", "--json"], env=self.env(), timeout=30)
        except OSError as e:
            return False, i18n.t("backend.cannot_start", cmd=exe, why=start_failure(error=e))
        except Exception:  # noqa: BLE001 — a timeout: cannot tell
            return True, ""
        if start_failure(r):
            return False, i18n.t("backend.cannot_start", cmd=exe, why=start_failure(r))
        try:
            j = json.loads(r.stdout or "{}")
        except ValueError:      # an older CLI without `auth status --json`: cannot tell
            return True, ""
        if j.get("loggedIn") is False:
            return False, i18n.t("backend.not_logged_in", cmd="claude", login="claude auth login") + \
                (" " + i18n.t("backend.settings_env_hint") if not self.section.get("settings_env") and _settings_env() else "")
        return True, ""

    def _base(self, effort: str) -> list[str]:
        cmd = [self.exe() or "claude", "-p"]
        if self.section.get("model"):
            cmd += ["--model", self.section["model"]]
        e = effort if effort in EFFORTS else (self.section.get("effort") or "high")
        return cmd + ["--effort", e] + BASE_FLAGS

    def _run(self, cmd: list[str], prompt: str, cwd: Path, workdir: Path, timeout: float, schema: dict | None) -> Result:
        workdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            r = run(cmd, cwd=cwd, input=prompt, env=self.env(), timeout=timeout)
            rc, out, err = r.returncode, r.stdout, r.stderr
        except Exception as e:  # noqa: BLE001 — timeout or launch failure
            rc, out, err = "timeout" if "Timeout" in type(e).__name__ else "error", "", repr(e)
        (workdir / "claude.json").write_text(out or "", encoding="utf-8")
        (workdir / "claude.err").write_text(err or "", encoding="utf-8")
        res = Result(ok=False, backend=self.name, model=self.section.get("model") or "", rc=rc,
                     elapsed_s=round(time.time() - t0, 1))
        try:
            j = json.loads(out)
        except ValueError:
            res.tail = (err or out or "")[-600:]
            res.transient = bool(NETWORK_RE.search(res.tail))
            return res
        text = str(j.get("result") or "")
        res.text = text
        mu = j.get("modelUsage") or {}
        # The model that did the work (the one with the most output; small helper models may be listed too). Recorded
        # as what actually ran, not what was asked for: an unset model follows the CLI's default, and that default
        # can change with the login method.
        used = max(mu, key=lambda k: (mu[k] or {}).get("outputTokens", 0)) if mu else ""
        if used:
            res.model = used
        u = mu.get(used) or {}
        res.usage = {"input_tokens": u.get("inputTokens", 0), "cache_read_tokens": u.get("cacheReadInputTokens", 0),
                     "cache_write_tokens": u.get("cacheCreationInputTokens", 0), "output_tokens": u.get("outputTokens", 0),
                     "turns": j.get("num_turns")}
        res.cost_usd = j.get("total_cost_usd")
        res.denials = [{"tool": d.get("tool_name"), "input": str((d.get("tool_input") or {}).get("command")
                                                                  or (d.get("tool_input") or {}).get("file_path") or "")[:200]}
                       for d in j.get("permission_denials") or []]
        if j.get("is_error"):
            res.tail = text[-600:]
            res.limited = bool(QUOTA_RE.search(text))
            res.transient = not res.limited and (j.get("api_error_status") in TRANSIENT_STATUS or bool(NETWORK_RE.search(text)))
            return res
        want = str(self.section.get("model") or "")
        if used and want and not same_family(want, used):
            res.tail = f"model mismatch: configured {want}, the CLI ran {used}; the result is discarded"
            return res
        if schema is not None:
            res.result = j.get("structured_output")
            if res.result is None:
                res.result = parse_json_text(text)
            res.ok = res.result is not None
            if not res.ok:
                res.tail = "no JSON in the final answer: " + text[-400:]
        else:
            res.result, res.ok = text, True
        if res.ok:
            (workdir / "out.json").write_text(json.dumps(res.result, ensure_ascii=False, indent=1), encoding="utf-8")
        return res

    def structured(self, prompt, schema, *, cwd, workdir, effort="high", timeout=3600, images=(), extra=""):
        text = prompt + READ_ONLY_NOTE.get(i18n.language(), READ_ONLY_NOTE["en"]) + (("\n" + extra) if extra else "")
        if images:
            text += "\n" + "\n".join(f"- {p}" for p in images) + "\n"
        dirs = sorted({str(Path(p).resolve().parent) for p in images})
        readable = [f"Read({_rule(d)})" for d in [cwd, *dirs]]
        cmd = self._base(effort) + ["--json-schema", json.dumps(schema, ensure_ascii=False),
                                    "--tools", "Read,Glob,Grep", "--permission-mode", "dontAsk",
                                    "--allowedTools", *readable, "Glob", "Grep",
                                    "--disallowedTools", *[f"Read({p})" for p in SECRET_PATHS]]
        for d in dirs:
            cmd += ["--add-dir", d]
        return self._run(cmd, text, Path(cwd), Path(workdir), timeout, schema)

    def agent(self, prompt, *, cwd, workdir, policy: Policy, effort="high", timeout=3600, schema=None, images=()):
        # reading is limited to cwd, the policy's extra folders and the images' folders; writing to policy.write
        img_dirs = {Path(p).resolve().parent for p in images}
        tools = ["Read", "Glob", "Grep"]
        allowed = [f"Read({_rule(d)})" for d in [cwd, *policy.read, *sorted(img_dirs)]] + ["Glob", "Grep"]
        if policy.write:
            tools += ["Edit", "Write"]
            for p in policy.write:
                allowed += [f"Edit({_rule(p)})", f"Write({_rule(p)})"]
        if policy.commands:
            tools.append("Bash")
            allowed += [f"Bash({' '.join(c)} *)" for c in policy.commands] + [f"Bash({' '.join(c)})" for c in policy.commands]
        if policy.web:
            tools += ["WebSearch", "WebFetch"]
            allowed += ["WebSearch", "WebFetch"]
        cmd = self._base(effort) + ["--tools", ",".join(tools), "--permission-mode", "dontAsk", "--allowedTools", *allowed]
        cmd += ["--disallowedTools", *[f"Read({p})" for p in dict.fromkeys(SECRET_PATHS + list(policy.deny_read))]]
        for d in sorted({str(Path(p).resolve()) for p in policy.read} | {str(d) for d in img_dirs}):
            cmd += ["--add-dir", d]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        text = prompt + ("\n\nImages to look at (open them with Read):\n" + "\n".join(f"- {p}" for p in images) if images else "")
        return self._run(cmd, text, Path(cwd), Path(workdir), timeout, schema)
