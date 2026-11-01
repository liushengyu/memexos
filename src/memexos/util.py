"""Small helpers shared by every module: atomic writes, frontmatter, subprocesses."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n+", re.S)


def expand(p: str | os.PathLike | None) -> Path | None:
    """Expand ~ and $VARS; return None for empty values."""
    if p is None or str(p).strip() == "":
        return None
    return Path(os.path.expandvars(os.path.expanduser(str(p)))).resolve()


def atomic_write(path: str | os.PathLike, data: str | bytes, mode: int | None = None) -> None:
    """Write via a hidden temp file in the same directory, then rename.

    Readers (the compiler watching raw/, the console, other agents) never see a
    half-written file, because rename is atomic on the same filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
            f.write(data)
        # mkstemp creates 0600 files; use the caller's mode, else what a plain open() would give.
        os.chmod(tmp, mode if mode is not None else (0o666 & ~_umask()))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_UMASK: int | None = None


def _umask() -> int:
    global _UMASK
    if _UMASK is None:
        _UMASK = os.umask(0o022)
        os.umask(_UMASK)
    return _UMASK


def append_jsonl(path: str | os.PathLike, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: str | os.PathLike) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (fields, body). Values are strings unless written as JSON (quoted strings, lists).

    The parser is deliberately small: raw files and wiki pages use flat
    `key: value` frontmatter, and pulling in a YAML library for that would make
    the core depend on a third-party package.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if not sep or not k.strip() or k.startswith((" ", "\t")):
            continue
        v = v.strip()
        if v.startswith(("\"", "[", "{")):
            try:
                v = json.loads(v)
            except ValueError:
                if v.startswith("[") and v.endswith("]"):  # YAML-style [a, b]
                    v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                else:
                    v = v.strip('"')
        fm[k.strip()] = v
    return fm, text[m.end():]


def render_value(v: Any) -> str:
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v if not isinstance(v, tuple) else list(v), ensure_ascii=False)
    v = str(v)
    return v if re.fullmatch(r"[A-Za-z0-9_./][A-Za-z0-9_./:+-]*", v) else json.dumps(v, ensure_ascii=False)


def render_frontmatter(fields: dict[str, Any], order: Iterable[str] = ()) -> str:
    order = list(order)
    keys = [k for k in order if k in fields] + [k for k in fields if k not in order]
    lines = [f"{k}: {render_value(fields[k])}" for k in keys if fields[k] not in (None, "", [], {})]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def which(cmd: str, extra: Iterable[str] = ()) -> str | None:
    """Find an executable on PATH, then in a few well-known install locations.

    Scheduled jobs (launchd, systemd, cron) start with a minimal PATH, so tools
    installed with Homebrew, pipx, uv or npm are often invisible to them.
    """
    found = shutil.which(cmd)
    if found:
        return found
    home = Path.home()
    for d in [*extra, "/opt/homebrew/bin", "/usr/local/bin", home / ".local/bin", home / ".cargo/bin", home / "bin"]:
        p = Path(d) / cmd
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def run(cmd: list[str], *, cwd: str | os.PathLike | None = None, env: dict | None = None, timeout: float | None = None,
        input: str | bytes | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """subprocess.run with text output and no inherited stdin (stdin is reserved for prompts)."""
    kw: dict[str, Any] = {"cwd": cwd, "env": env, "timeout": timeout, "capture_output": True}
    if input is None:
        kw["stdin"] = subprocess.DEVNULL
    else:
        kw["input"] = input.encode("utf-8") if isinstance(input, str) else input
    r = subprocess.run(cmd, **kw)
    out = subprocess.CompletedProcess(r.args, r.returncode,
                                      r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else r.stdout,
                                      r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else r.stderr)
    if check and out.returncode != 0:
        raise subprocess.CalledProcessError(out.returncode, cmd, out.stdout, out.stderr)
    return out


def proxy_env(base: dict | None, proxy: str | None) -> dict:
    """Environment for a child process with an explicit proxy (or explicitly none).

    proxy=None leaves the inherited environment alone; proxy="" removes every
    proxy variable (direct connection); any other value sets all of them.
    """
    env = dict(os.environ if base is None else base)
    keys = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
    if proxy is None:
        return env
    for k in keys:
        env.pop(k, None)
    if proxy:
        env.update({k: proxy for k in keys})
    else:
        env["NO_PROXY"] = env["no_proxy"] = "*"
    return env


def self_cmd() -> list[str]:
    """How to invoke this installation's `memex` from a child process (an agent's shell may not have it on PATH)."""
    import sys
    exe = shutil.which("memex")
    return [exe] if exe else [sys.executable, "-m", "memexos"]
