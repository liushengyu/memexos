"""What every model backend provides, and the records it returns.

Two kinds of calls:
  structured(...)  answer in JSON matching a schema; read-only (it may read files under cwd)
  agent(...)       do a task with tools under a Policy (which files it may write, which commands it may run,
                   whether it may use the web) and report in text, or in JSON when a schema is given

Backends never raise for model-side failures; they return a Result with ok=False and say why. `limited` means
the account's quota or credit is used up (the router then switches to the next backend for a while); `transient`
means a temporary gateway problem (429/5xx, connection reset) worth retrying.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

QUOTA_RE = re.compile(r"usage limit|DAILY_LIMIT_EXCEEDED|session limit|weekly limit|hit your [a-z ]*limit|"
                      r"credit balance is too low|insufficient[_ ]quota|billing", re.I)
TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
NETWORK_RE = re.compile(r"Connection refused|ECONNREFUSED|ECONNRESET|ETIMEDOUT|socket hang up|fetch failed|"
                        r"Connection reset|timed out|overloaded", re.I)


@dataclass
class Policy:
    """What an agent may do. Paths are absolute; cwd is always readable."""
    write: list[Path] = field(default_factory=list)       # files or directories it may create/edit
    read: list[Path] = field(default_factory=list)        # extra readable directories besides cwd
    commands: list[list[str]] = field(default_factory=list)  # allowed command prefixes, e.g. [["git", "-C"]]
    web: bool = False                                     # web search / fetch tools
    network: bool = False                                 # commands may reach the network (sandboxed backends)
    deny_read: list[str] = field(default_factory=list)    # glob patterns never to read (credentials)


@dataclass
class Result:
    ok: bool
    backend: str
    model: str = ""
    result: Any = None            # parsed JSON (structured, or agent with schema) or final text (agent)
    text: str = ""                # final text, when there is one
    rc: Any = None
    elapsed_s: float = 0.0
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    limited: bool = False
    transient: bool = False
    tail: str = ""                # error detail for logs (never secrets)
    denials: list[dict] = field(default_factory=list)   # tool calls the permission system refused
    tried: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"backend": self.backend, "model": self.model, "ok": self.ok, "rc": self.rc, "elapsed_s": self.elapsed_s,
                "limited": self.limited, "transient": self.transient}


class Backend:
    name = "base"

    def __init__(self, cfg, section: dict):
        self.cfg = cfg
        self.section = section

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def structured(self, prompt: str, schema: dict, *, cwd: Path, workdir: Path, effort: str = "high",
                   timeout: float = 3600, images: list[Path] = (), extra: str = "") -> Result:
        raise NotImplementedError

    def agent(self, prompt: str, *, cwd: Path, workdir: Path, policy: Policy, effort: str = "high",
              timeout: float = 3600, schema: dict | None = None, images: list[Path] = ()) -> Result:
        raise NotImplementedError


def start_failure(r=None, error: BaseException | None = None) -> str:
    """Why a command line could not even start (missing interpreter, not executable), or "" if it ran. A login check
    gives an older CLI without the check the benefit of the doubt, but must not do so for one that cannot start."""
    if isinstance(error, OSError):
        return str(error)[-200:]
    if r is not None and r.returncode in (126, 127):          # the shell's "cannot execute" / "not found"
        return ((r.stderr or "") + (r.stdout or "")).strip()[-200:] or f"exit {r.returncode}"
    return ""


def parse_json_text(text: str) -> Any:
    """JSON from a model's final text: the whole text, else the last fenced or brace-delimited block."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for m in reversed(list(re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S))):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except ValueError:
            pass
    return None


# Never readable by any agent, whatever the task: credentials and browser profiles.
SECRET_PATHS = ["~/.ssh/**", "~/.gnupg/**", "~/.aws/**", "~/.config/gh/**", "~/.codex/auth.json", "~/.claude/.credentials.json",
                "~/.config/memexos/cookies/**", "~/.local/share/memexos/browser/**", "**/.env", "**/.env.*",
                "~/Library/Keychains/**", "~/Library/Application Support/Google/Chrome/**", "~/.config/google-chrome/**",
                "~/.mozilla/**"]

LOGIN_TTL = 600          # seconds a positive login check is trusted within one process


READ_ONLY_NOTE = {
    "en": "\n\n---\n(Environment: you can read files with your file tools but must not modify anything. Where the instructions "
          "mention shell commands (cat, rg, ls, git), use the file tools instead; command output the task needs, such as a "
          "git diff, is attached below. Finish with output that strictly matches the given JSON schema.)\n",
    "zh": "\n\n---\n（运行环境说明：你只能用文件工具读文件，不能修改任何东西。上文提到的 cat / rg / ls / git 等命令，用文件工具代替；"
          "任务需要的命令输出（如 git diff）已由程序算好附在下面。最后严格按给定的 JSON schema 输出。）\n",
}


def schema_errors(schema: dict, value: Any, path: str = "$") -> list[str]:
    """A small JSON Schema check (type, required, properties, items, enum, additionalProperties) for backends whose
    provider does not enforce the schema. Returns readable problems; empty when the value fits."""
    errs: list[str] = []
    t = schema.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    py = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}
    if types and not any(isinstance(value, py[x]) and not (x in ("integer", "number") and isinstance(value, bool)) for x in types if x in py):
        return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in value:
                errs.append(f"{path}: missing {k}")
        if schema.get("additionalProperties") is False:
            errs += [f"{path}: unexpected {k}" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errs += schema_errors(props[k], v, f"{path}.{k}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value[:200]):
            errs += schema_errors(schema["items"], v, f"{path}[{i}]")
    return errs[:20]
