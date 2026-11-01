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
from memexos.backends.base import NETWORK_RE, QUOTA_RE, Backend, Policy, Result
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

    def exe(self) -> str | None:
        return which(self.section.get("command") or "codex")

    def available(self) -> tuple[bool, str]:
        return (True, "") if self.exe() else (False, i18n.t("backend.cmd_missing", cmd=self.section.get("command") or "codex"))

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
        return self._run(self._cmd("read-only", Path(cwd), effort, extra_args), prompt, workdir, timeout, schema)

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
