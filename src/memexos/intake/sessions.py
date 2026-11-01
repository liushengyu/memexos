"""Conversation logs as material: read Claude Code and Codex session files into a form an agent can review.

    memex sessions list [--since-days 30] [--idle-hours 6] [--source claude|codex] [--all]
        finished top-level sessions (no activity for idle-hours) not yet admitted, one JSON object per line
    memex sessions show <key> [--turn-from N] [--turn-to M] [--result-chars 240]
        a compact rendering: user messages and assistant replies in full, tool calls reduced to name and key argument,
        tool output truncated, system reminders and reasoning removed; paged by turn (one user message = one turn)
    memex sessions docs <key>
        documents (md/html/pdf/…) the session wrote, and whether they still exist

Keys look like claude:<session id> or codex:<session id>. Read-only. Sessions run by MemexOS itself (compiling,
review, tests) are skipped, so the knowledge base never ingests its own machinery.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

DOC_EXT = (".md", ".markdown", ".html", ".htm", ".pdf", ".txt", ".docx", ".pptx", ".xlsx", ".csv")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
SAY_MAX = 12000
CONTINUED_MAX = 3000


def claude_dir(cfg) -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / "projects" if base else Path(os.path.expanduser(cfg.get("intake.sessions.claude_dir") or "~/.claude/projects"))


def codex_dir(cfg) -> Path:
    base = os.environ.get("CODEX_HOME")
    return Path(base) / "sessions" if base else Path(os.path.expanduser(cfg.get("intake.sessions.codex_dir") or "~/.codex/sessions"))


def self_paths(cfg) -> list[str]:
    """Working directories of MemexOS's own unattended runs."""
    return [str(cfg.paths.state), str(cfg.paths.staging), os.environ.get("TMPDIR", "/tmp").rstrip("/") + "/memex-"]


def short(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def short_keep(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"\n…(truncated; {len(s)} characters in total)"


def clean_user(text: str) -> str:
    if text.startswith("<task-notification>"):
        m = re.search(r"<summary>(.*?)</summary>", text, re.S)
        return "[background task notification] " + short(m.group(1) if m else text, 200)
    if text.startswith("This session is being continued from a previous conversation"):
        return "[summary carried over after context compaction] " + short(text, CONTINUED_MAX)
    return text


def ledger_keys(cfg) -> set[str]:
    out = set()
    p = cfg.paths.ledger / "admission.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.add(json.loads(line)["key"])
            except (ValueError, KeyError):
                pass
    return out


# ---------------------------------------------------------------- Claude Code
def claude_files(cfg):
    yield from claude_dir(cfg).glob("*/*.jsonl")         # sub-agent logs live deeper and are not separate sessions


def _events(f: Path):
    with open(f, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def claude_turns(f: Path):
    turns, cur, meta = [], None, {"cwd": None, "first": None, "last": None, "title": None}
    for e in _events(f):
        ts = e.get("timestamp")
        if ts:
            meta["first"] = meta["first"] or ts
            meta["last"] = ts
        meta["cwd"] = meta["cwd"] or e.get("cwd")
        if e.get("type") == "summary" and e.get("summary"):
            meta["title"] = e["summary"]
        if e.get("isSidechain") or e.get("isMeta"):
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user":
            if isinstance(content, str):
                text = REMINDER.sub("", content).strip()
                if text and not text.startswith("<local-command-caveat>"):
                    cur = {"user": clean_user(text), "items": []}
                    turns.append(cur)
            elif isinstance(content, list):
                texts = [t for t in (REMINDER.sub("", c.get("text", "")).strip() for c in content if c.get("type") == "text") if t]
                for c in content:
                    if c.get("type") == "tool_result" and cur is not None:
                        out = c.get("content")
                        if isinstance(out, list):
                            out = " ".join(x.get("text", "") for x in out if isinstance(x, dict))
                        cur["items"].append(("result", ("ERROR " if c.get("is_error") else "") + REMINDER.sub("", str(out or ""))))
                if texts:
                    cur = {"user": clean_user("\n".join(texts)), "items": []}
                    turns.append(cur)
        elif e.get("type") == "assistant" and isinstance(content, list):
            if cur is None:
                cur = {"user": "(start of session)", "items": []}
                turns.append(cur)
            for c in content:
                if c.get("type") == "text" and c.get("text", "").strip():
                    cur["items"].append(("say", short_keep(c["text"].strip(), SAY_MAX)))
                elif c.get("type") == "tool_use":
                    cur["items"].append(("tool", (c.get("name"), c.get("input") or {})))
    return turns, meta


def claude_docs(f: Path) -> dict:
    docs: dict[str, set] = {}
    for e in _events(f):
        content = (e.get("message") or {}).get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_use" and c.get("name") in ("Write", "Edit", "NotebookEdit"):
                p = (c.get("input") or {}).get("file_path") or (c.get("input") or {}).get("notebook_path")
                if p and p.lower().endswith(DOC_EXT):
                    docs.setdefault(p, set()).add(c["name"])
    return docs


# ---------------------------------------------------------------- Codex
def codex_files(cfg):
    yield from codex_dir(cfg).rglob("*.jsonl")


def codex_meta(f: Path) -> dict | None:
    try:
        e = json.loads(open(f, encoding="utf-8", errors="ignore").readline())
    except (ValueError, OSError):
        return None
    return e.get("payload") if e.get("type") == "session_meta" else None


def codex_turns(f: Path):
    turns, cur, meta = [], None, {"cwd": None, "first": None, "last": None, "title": None}
    for e in _events(f):
        ts = e.get("timestamp")
        if ts:
            meta["first"] = meta["first"] or ts
            meta["last"] = ts
        p = e.get("payload") or {}
        if e.get("type") == "session_meta":
            meta["cwd"] = meta["cwd"] or p.get("cwd")
            continue
        if e.get("type") != "response_item":
            continue
        t = p.get("type")
        if t == "message":
            text = REMINDER.sub("", " ".join(c.get("text", "") for c in p.get("content") or []
                                             if c.get("type") in ("input_text", "output_text"))).strip()
            if not text or text.startswith(("<environment_context>", "<user_instructions>", "# AGENTS.md", "<permissions")):
                continue
            if p.get("role") == "user":
                cur = {"user": clean_user(text), "items": []}
                turns.append(cur)
            elif p.get("role") == "assistant":
                if cur is None:
                    cur = {"user": "(start of session)", "items": []}
                    turns.append(cur)
                cur["items"].append(("say", short_keep(text, SAY_MAX)))
        elif t in ("function_call", "custom_tool_call", "local_shell_call") and cur is not None:
            cur["items"].append(("tool", (p.get("name") or t, p.get("arguments") or p.get("input") or p.get("action") or "")))
        elif t in ("function_call_output", "custom_tool_call_output") and cur is not None:
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("output") or json.dumps(out, ensure_ascii=False)
            cur["items"].append(("result", str(out or "")))
    return turns, meta


def codex_docs(f: Path) -> dict:
    docs: dict[str, set] = {}
    for line in open(f, encoding="utf-8", errors="ignore"):
        if "apply_patch" not in line and "*** " not in line:
            continue
        for m in re.finditer(r"\*\*\* (Add|Update) File: ([^\\\n\"]+)", line):
            p = m.group(2).strip()
            if p.lower().endswith(DOC_EXT):
                docs.setdefault(p, set()).add(m.group(1))
    return docs


# ---------------------------------------------------------------- common
def find(cfg, key: str) -> tuple[str, Path]:
    src, sid = key.split(":", 1)
    if src == "claude":
        for f in claude_files(cfg):
            if f.stem == sid:
                return src, f
    else:
        for f in codex_files(cfg):
            m = codex_meta(f)
            if m and m.get("id") == sid:
                return src, f
    raise KeyError(key)


def render_tool(name, args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return f"{name}: {short(args, 220)}"
    if not isinstance(args, dict):
        return f"{name}: {short(args, 220)}"
    for k in ("command", "cmd", "file_path", "path", "pattern", "query", "url", "description", "prompt", "skill"):
        if k in args:
            v = args[k] if not isinstance(args[k], list) else " ".join(map(str, args[k]))
            return f"{name}: {short(v, 220)}"
    return f"{name}: {short(json.dumps(args, ensure_ascii=False), 220)}"


def tilde(p: str | None) -> str:
    home = str(Path.home())
    return (p or "").replace(home, "~")


def list_sessions(cfg, since_days: float = 30, idle_hours: float = 6, source: str | None = None, include_done: bool = False) -> list[dict]:
    done = set() if include_done else ledger_keys(cfg)
    now, since, idle = time.time(), time.time() - since_days * 86400, idle_hours * 3600
    mine = self_paths(cfg)
    rows = []
    if source in (None, "claude"):
        for f in claude_files(cfg):
            mt = f.stat().st_mtime
            key = "claude:" + f.stem
            if mt < since or now - mt < idle or key in done:
                continue
            turns, meta = claude_turns(f)
            if turns and not any(meta["cwd"] and meta["cwd"].startswith(m) for m in mine):
                rows.append((mt, key, meta, turns))
    if source in (None, "codex"):
        for f in codex_files(cfg):
            mt = f.stat().st_mtime
            if mt < since or now - mt < idle:
                continue
            m = codex_meta(f)
            # sub-agent threads and exec automation are not sources: their results are in the parent session
            if not m or m.get("source") not in (None, "cli") or m.get("thread_source") not in (None, "user") \
                    or any((m.get("cwd") or "").startswith(x) for x in mine):
                continue
            key = "codex:" + m["id"]
            if key in done:
                continue
            turns, meta = codex_turns(f)
            if turns:
                rows.append((mt, key, meta, turns))
    out = []
    for mt, key, meta, turns in sorted(rows, key=lambda r: r[0]):
        chars = sum(len(t["user"]) + sum(len(x[1]) if x[0] == "say" else 120 for x in t["items"]) for t in turns)
        out.append({"key": key, "cwd": tilde(meta["cwd"]), "first": meta["first"], "last": meta["last"], "title": meta["title"],
                    "turns": len(turns), "approx_chars": chars, "first_user_msg": short(turns[0]["user"], 160)})
    return out


def show(cfg, key: str, turn_from: int = 1, turn_to: int | None = None, result_chars: int = 240) -> str:
    src, f = find(cfg, key)
    turns, meta = (claude_turns if src == "claude" else codex_turns)(f)
    hi = min(turn_to or len(turns), len(turns))
    out = [f"# {key}\ncwd: {tilde(meta['cwd'])} · {meta['first']} → {meta['last']} · {len(turns)} turns, showing {turn_from}–{hi}\n"]
    for i in range(turn_from - 1, hi):
        t = turns[i]
        out.append(f"## turn {i + 1}\n**user:** {t['user']}\n")
        for kind, val in t["items"]:
            if kind == "say":
                out.append(f"**assistant:** {val}\n")
            elif kind == "tool":
                out.append(f"  → {render_tool(*val)}")
            elif kind == "result" and result_chars > 0:
                out.append(f"    ← {short(val, result_chars)}")
        out.append("")
    return "\n".join(out)


def docs(cfg, key: str) -> list[dict]:
    src, f = find(cfg, key)
    out = []
    for p, ops in sorted((claude_docs if src == "claude" else codex_docs)(f).items()):
        full = Path(os.path.expanduser(p))
        out.append({"path": tilde(p), "ops": sorted(ops), "exists": full.exists(), "bytes": full.stat().st_size if full.exists() else None})
    return out


def cmd_sessions(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex sessions")
    sp = ap.add_subparsers(dest="cmd", required=True)
    l = sp.add_parser("list")
    l.add_argument("--since-days", type=float, default=30)
    l.add_argument("--idle-hours", type=float, default=6)
    l.add_argument("--source", choices=["claude", "codex"])
    l.add_argument("--all", action="store_true", help="include sessions already admitted")
    s = sp.add_parser("show")
    s.add_argument("key")
    s.add_argument("--turn-from", type=int, default=1)
    s.add_argument("--turn-to", type=int)
    s.add_argument("--result-chars", type=int, default=240)
    d = sp.add_parser("docs")
    d.add_argument("key")
    a = ap.parse_args(argv)
    cfg = loader()
    try:
        if a.cmd == "list":
            for row in list_sessions(cfg, a.since_days, a.idle_hours, a.source, a.all):
                print(json.dumps(row, ensure_ascii=False))
        elif a.cmd == "show":
            print(show(cfg, a.key, a.turn_from, a.turn_to, a.result_chars))
        else:
            for row in docs(cfg, a.key):
                print(json.dumps(row, ensure_ascii=False))
    except KeyError as e:
        print(f"no such session: {e}", file=sys.stderr)
        return 1
    return 0
