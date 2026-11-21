"""Backend: the Codex command line (`codex exec`), using whatever account it is logged in with.

Structured calls run in Codex's read-only sandbox, where it may run read-only commands (git, rg, cat). Agent
calls run in the workspace-write sandbox rooted at cwd; extra writable paths are added with --add-dir. Codex
sandboxes commands rather than allow-listing them, so Policy.commands is advisory here (stated in the prompt).
Runs are ephemeral: they leave no session files behind (which would otherwise be collected as conversations).

Runs are isolated from the person's own Codex setup (`isolate = true`): the user config.toml (MCP servers, personal
instructions, profiles) and execpolicy rules are not loaded; only the login is shared. The model named in the user
config is still used unless backends.codex.model says otherwise. A user config that routes Codex through a custom
model provider cannot be dropped, so isolation is skipped there (memex doctor says so).
"""

from __future__ import annotations

import functools
import json
import os
import time
import tomllib
from pathlib import Path

from memexos import i18n
from memexos.backends.base import LOGIN_TTL, NETWORK_RE, QUOTA_RE, READ_ONLY_NOTE, Backend, Policy, Result, start_failure
from memexos.util import proxy_env, run, which


def _proxy(section: dict) -> str | None:
    p = (section.get("proxy") or "").strip()
    return None if not p else "" if p == "direct" else p


def _usage(events: Path) -> dict:
    tot = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    try:
        for line in open(events, errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "turn.completed":
                for k in tot:
                    tot[k] += int((e.get("usage") or {}).get(k) or 0)
    except OSError:
        pass
    return tot


def _final_text(events: Path) -> str:
    last = ""
    try:
        for line in open(events, errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            item = e.get("item") or {}
            if e.get("type") == "item.completed" and item.get("type") in ("agent_message", "assistant_message"):
                last = item.get("text") or last
    except OSError:
        pass
    return last


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))


@functools.cache
def user_config(home: str) -> dict:
    try:
        return tomllib.loads((Path(home) / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Codex(Backend):
    name = "codex"
    _login: dict = {}

    def exe(self) -> str | None:
        return which(self.section.get("command") or "codex")

    def available(self) -> tuple[bool, str]:
        exe = self.exe()
        if not exe:
            return False, i18n.t("backend.cmd_missing", cmd=self.section.get("command") or "codex")
        if self.section.get("check_login", True) is False:
            return True, ""
        hit = Codex._login.get(exe)
        if hit and hit[1] and time.time() - hit[0] < LOGIN_TTL:
            return True, ""
        why = ""
        try:
            r = run([exe, "login", "status"], env=proxy_env(None, _proxy(self.section)), timeout=30)
            out = (r.stdout + r.stderr).lower()
            ok = r.returncode == 0 and "not logged in" not in out
            if start_failure(r):
                why = i18n.t("backend.cannot_start", cmd=exe, why=start_failure(r))
        except OSError as e:
            ok, why = False, i18n.t("backend.cannot_start", cmd=exe, why=start_failure(error=e))
        except Exception:  # noqa: BLE001 — a timeout: cannot tell; let the run itself report
            ok = True
        why = why or ("" if ok else i18n.t("backend.not_logged_in", cmd="codex", login="codex login"))
        Codex._login[exe] = (time.time(), ok, why)
        return ok, why

    def isolated(self) -> bool:
        uc = user_config(str(codex_home()))
        custom_provider = (uc.get("model_provider") or "openai") != "openai"
        return self.section.get("isolate", True) is not False and not custom_provider

    def model(self) -> str:
        return self.section.get("model") or str(user_config(str(codex_home())).get("model") or "")

    def _cmd(self, sandbox: str, cwd: Path, effort: str, extra: list[str]) -> list[str]:
        cmd = [self.exe() or "codex", "exec"]
        if self.isolated():
            cmd += ["--ignore-user-config", "--ignore-rules"]
        if self.model():
            cmd += ["-m", self.model()]
        cmd += ["-c", f"model_reasoning_effort={effort or self.section.get('effort') or 'high'}", "-s", sandbox, "-C", str(cwd),
                "--skip-git-repo-check", "--ephemeral", "--json", *extra]
        return cmd

    def _run(self, cmd: list[str], prompt: str, workdir: Path, timeout: float, schema: dict | None) -> Result:
        workdir.mkdir(parents=True, exist_ok=True)
        out, events, err = workdir / "out.json", workdir / "events.jsonl", workdir / "codex.err"
        for stale in (out, events):
            stale.unlink(missing_ok=True)
        t0 = time.time()
        env = proxy_env(None, _proxy(self.section))
        try:
            r = run(cmd + ["-o", str(out), "-"], input=prompt, env=env, timeout=timeout)
            rc = r.returncode
            events.write_text(r.stdout or "", encoding="utf-8")
            err.write_text(r.stderr or "", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            rc = "timeout" if "Timeout" in type(e).__name__ else "error"
            err.write_text(repr(e), encoding="utf-8")
        res = Result(ok=False, backend=self.name, model=self.model(), rc=rc,
                     elapsed_s=round(time.time() - t0, 1), usage=_usage(events))
        text = ""
        try:
            text = out.read_text(encoding="utf-8")
        except OSError:
            pass
        res.text = text or _final_text(events)
        if schema is not None:
            try:
                res.result = json.loads(text)
                res.ok = True
            except ValueError:
                res.result = None
        else:
            res.result, res.ok = res.text, bool(res.text) and rc == 0
        if not res.ok:
            # Only error events and stderr count: the model's own answer may talk about quotas.
            errs = []
            try:
                errs += [l.strip()[:400] for l in open(events, errors="ignore") if '"type":"error"' in l or '"turn.failed"' in l]
            except OSError:
                pass
            try:
                errs.append(err.read_text(errors="ignore")[-3000:])
            except OSError:
                pass
            tail = "\n".join(errs)
            hit = next((l for l in tail.splitlines() if QUOTA_RE.search(l)), "")
            res.limited = bool(hit)
            res.transient = not res.limited and bool(NETWORK_RE.search(tail))
            res.tail = (hit or tail[-600:])[-600:]
        return res

    def structured(self, prompt, schema, *, cwd, workdir, effort="high", timeout=3600, images=(), extra=""):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        sp = workdir / "schema.json"
        sp.write_text(json.dumps(schema), encoding="utf-8")
        extra_args = ["--output-schema", str(sp)]
        for im in images:
            extra_args += ["-i", str(im)]
        # the same prompt every backend gets: the read-only note and whatever the caller computed for it (a git diff…)
        text = prompt + READ_ONLY_NOTE.get(i18n.language(), READ_ONLY_NOTE["en"]) + (("\n" + extra) if extra else "")
        return self._run(self._cmd("read-only", Path(cwd), effort, extra_args), text, workdir, timeout, schema)

    def agent(self, prompt, *, cwd, workdir, policy: Policy, effort="high", timeout=3600, schema=None, images=()):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        extra = []
        cwd = Path(cwd).resolve()
        for p in policy.write:
            p = Path(p).resolve()
            d = p if p.is_dir() or not p.suffix else p.parent
            if d != cwd and cwd not in d.parents:
                extra += ["--add-dir", str(d)]
        # workspace-write also lets Codex write to $TMPDIR and /tmp unless told otherwise; only cwd and the policy's
        # folders are meant to be writable (found by tests/test_cli_live.py)
        extra += ["-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true", "-c", "sandbox_workspace_write.exclude_slash_tmp=true"]
        if policy.web or policy.network:
            extra += ["-c", "sandbox_workspace_write.network_access=true"]
        if policy.web:
            extra += ["-c", "tools.web_search=true"]
        if schema is not None:
            sp = workdir / "schema.json"
            sp.write_text(json.dumps(schema), encoding="utf-8")
            extra += ["--output-schema", str(sp)]
        for im in images:
            extra += ["-i", str(im)]
        note = ""
        if policy.commands:
            note = "\n\nCommands you may run: " + "; ".join(" ".join(c) + " …" for c in policy.commands) + \
                   ". Do not run anything else.\n"
        sandbox = "workspace-write" if policy.write or policy.commands else "read-only"
        return self._run(self._cmd(sandbox, cwd, effort, extra), prompt + note, workdir, timeout, schema)
