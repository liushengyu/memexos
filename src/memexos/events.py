"""Structured event log: the single source of truth for "what is the system doing".

Every job appends JSON lines to <state>/events.jsonl:

    {"ts": "...", "run": "ingest-20261101-031700-4f2a", "job": "ingest",
     "type": "gate2.result", "data": {"passed": false, "problems": 3}}

The status view and the console read these events. A human-readable line is
also written to <state>/memex.log, rendered from the language pack
(event.<type>), but nothing parses that file: wording can change freely.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Iterator

from memexos import i18n

_run: dict[str, str] = {}


def new_run(job: str) -> str:
    """Start a run (or join the parent's, passed down through MEMEX_RUN)."""
    rid = os.environ.get("MEMEX_RUN") or f"{job}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    _run["id"], _run["job"] = rid, job
    os.environ["MEMEX_RUN"] = rid
    return rid


def current_run() -> str:
    return _run.get("id") or os.environ.get("MEMEX_RUN", "")


def emit(state_dir: Path, type: str, job: str | None = None, **data: Any) -> dict:
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": current_run(), "job": job or _run.get("job", ""),
          "type": type, "data": data}
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    line = render(ev)
    with open(state_dir / "memex.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    return ev


def render(ev: dict) -> str:
    key = f"event.{ev['type']}"
    data = {k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in (ev.get("data") or {}).items()}
    if i18n.has(key):
        return f"{ev.get('job') or '-'}: " + i18n.t(key, **data)
    extra = " ".join(f"{k}={v}" for k, v in data.items())
    return f"{ev.get('job') or '-'}: {ev['type']} {extra}".rstrip()


def read(state_dir: Path, since: float | None = None, types: tuple[str, ...] = (), run: str | None = None) -> Iterator[dict]:
    p = state_dir / "events.jsonl"
    if not p.exists():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if types and not ev.get("type", "").startswith(types):
                continue
            if run and ev.get("run") != run:
                continue
            if since is not None and _epoch(ev.get("ts", "")) < since:
                continue
            yield ev


def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0
