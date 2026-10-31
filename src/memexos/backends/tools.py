"""File and command tools for backends that run their own agent loop (the API backends).

Every path is resolved against the working directory and checked against the Policy: reads only under cwd and
the policy's extra read directories, never matching a deny pattern; writes only to the policy's write paths;
commands only when argv starts with an allowed prefix (no shell, so no pipes, redirection or substitution).
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

from memexos.backends.base import Policy

MAX_READ = 2000          # lines per read
MAX_OUT = 60_000         # characters per tool result


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


DEFS = {
    "read_file": ("Read a text file. Returns numbered lines. Use offset (1-based line) and limit to page through long files.",
                  _schema({"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}})),
    "list_files": ("List files matching a glob pattern relative to the working directory, e.g. 'wiki/**/*.md'.",
                   _schema({"pattern": {"type": "string"}})),
    "grep": ("Search file contents with a regular expression. Returns path:line: text for up to 200 matches. "
             "glob limits the files searched (e.g. 'raw/**/*.md'; use '**/*' for all).",
             _schema({"pattern": {"type": "string"}, "glob": {"type": "string"}})),
    "write_file": ("Create or overwrite a text file with the given content.",
                   _schema({"path": {"type": "string"}, "content": {"type": "string"}})),
    "edit_file": ("Replace one exact, unique occurrence of old_text with new_text in a file.",
                  _schema({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}})),
    "run_command": ("Run an allowed command (argv list, no shell). Returns exit code and output.",
                    _schema({"argv": {"type": "array", "items": {"type": "string"}}})),
}


class Sandbox:
    def __init__(self, cwd: Path, policy: Policy, read_only: bool = False):
        self.cwd = Path(cwd).resolve()
        self.policy = policy
        self.read_only = read_only
        self.reads = [self.cwd] + [Path(p).resolve() for p in policy.read]
        self.writes = [] if read_only else [Path(p).resolve() for p in policy.write]
        self.denied: list[dict] = []

    def tool_names(self) -> list[str]:
        names = ["read_file", "list_files", "grep"]
        if self.writes:
            names += ["write_file", "edit_file"]
        if self.policy.commands and not self.read_only:
            names.append("run_command")
        return names

    def definitions(self) -> list[dict]:
        return [{"name": n, "description": DEFS[n][0], "input_schema": DEFS[n][1], "strict": True} for n in self.tool_names()]

    # -- path checks --
    def _resolve(self, p: str) -> Path:
        q = Path(os.path.expanduser(p))
        return (q if q.is_absolute() else self.cwd / q).resolve()

    def _readable(self, p: Path) -> bool:
        if any(fnmatch.fnmatch(str(p), os.path.expanduser(d)) for d in self.policy.deny_read):
            return False
        return any(p == r or r in p.parents for r in self.reads)

    def _writable(self, p: Path) -> bool:
        return any(p == w or w in p.parents for w in self.writes)

    def _deny(self, tool: str, what: str) -> str:
        self.denied.append({"tool": tool, "input": what[:200]})
        return f"Permission denied: {tool} is not allowed for {what}"

    # -- tools --
    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Returns (result text, is_error)."""
        try:
            fn = getattr(self, "t_" + name)
        except AttributeError:
            return f"Unknown tool {name}", True
        if name not in self.tool_names():
            return self._deny(name, str(args)), True
        try:
            out = fn(**args)
            return (out[:MAX_OUT] + ("\n…(truncated)" if len(out) > MAX_OUT else "")), out.startswith("Permission denied")
        except Exception as e:  # noqa: BLE001 — report tool errors to the model, never crash the loop
            return f"Error: {type(e).__name__}: {e}", True

    def t_read_file(self, path: str, offset: int = 1, limit: int = 400) -> str:
        p = self._resolve(path)
        if not self._readable(p):
            return self._deny("read_file", str(p))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(int(offset or 1), 1) - 1
        end = min(start + min(int(limit or 400), MAX_READ), len(lines))
        body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(start, end))
        return f"{p.relative_to(self.cwd) if self.cwd in p.parents else p} (lines {start + 1}-{end} of {len(lines)})\n{body}"

    def t_list_files(self, pattern: str) -> str:
        out = []
        for root in self.reads[:1] + self.reads[1:]:
            for p in sorted(root.glob(pattern)):
                if p.is_file() and self._readable(p.resolve()) and ".git" not in p.parts:
                    out.append(str(p.relative_to(self.cwd)) if root == self.cwd else str(p))
                if len(out) >= 1000:
                    break
        return "\n".join(out) or "(no files)"

    def t_grep(self, pattern: str, glob: str = "**/*") -> str:
        rx = re.compile(pattern)
        hits = []
        for p in sorted(self.cwd.glob(glob or "**/*")):
            if not p.is_file() or ".git" in p.parts or not self._readable(p.resolve()):
                continue
            try:
                for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{p.relative_to(self.cwd)}:{n}: {line.strip()[:300]}")
                        if len(hits) >= 200:
                            return "\n".join(hits) + "\n…(more matches not shown)"
            except OSError:
                continue
        return "\n".join(hits) or "(no matches)"

    def t_write_file(self, path: str, content: str) -> str:
        p = self._resolve(path)
        if not self._writable(p):
            return self._deny("write_file", str(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} characters to {path}"

    def t_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        p = self._resolve(path)
        if not self._writable(p):
            return self._deny("edit_file", str(p))
        text = p.read_text(encoding="utf-8")
        n = text.count(old_text)
        if n != 1:
            return f"Error: old_text occurs {n} times in {path}; it must occur exactly once (add more context)"
        p.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"edited {path}"

    def t_run_command(self, argv: list[str]) -> str:
        if not any(argv[:len(pre)] == pre for pre in self.policy.commands):
            return self._deny("run_command", " ".join(argv))
        r = subprocess.run(argv, cwd=self.cwd, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        return f"exit {r.returncode}\n{r.stdout}\n{r.stderr}".strip()
