"""Structured event log: the single source of truth for "what is the system doing".

Every job appends JSON lines to <state>/events.jsonl:

    {"ts": "...", "run": "ingest-20261101-031700-4f2a", "job": "ingest",
     "type": "gate2.result", "data": {"passed": false, "problems": 3}}

The status view and the console read these events. A human-readable line is
also written to <state>/memex.log, rendered from the language pack
(event.<type>), but nothing parses that file: wording can change freely.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Iterator

from memexos import i18n

_run: dict[str, str] = {}


def new_run(job: str) -> str:
    """Start a run. A run started by another run's process (through MEMEX_RUN) records that run as its parent."""
    parent = os.environ.get("MEMEX_RUN", "")
    rid = f"{job}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    _run.update(id=rid, job=job, parent=parent if parent != rid else "")
    os.environ["MEMEX_RUN"] = rid
    return rid


@contextlib.contextmanager
def run(state_dir: Path, job: str) -> Iterator[str]:
    """A whole command: run.start (with the pid, so readers can tell a live run from a crashed one) … run.end."""
    rid = new_run(job)
    t0 = time.time()
    emit(state_dir, "run.start", pid=os.getpid())
    status = "error"
    try:
        yield rid
        status = "ok"
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except SystemExit as e:
        status = "ok" if not e.code else "error"
        raise
    finally:
        emit(state_dir, "run.end", seconds=round(time.time() - t0), status=status)


@contextlib.contextmanager
def job(name: str) -> Iterator[None]:
    """Attribute the events inside to another job name (a nightly run doing intake, then fixes, …)."""
    prev = _run.get("job", "")
    _run["job"] = name
    try:
        yield
    finally:
        _run["job"] = prev


@contextlib.contextmanager
def step(state_dir: Path, name: str, **data: Any) -> Iterator[dict]:
    """One stage of the work (write, gate2, admit, …): step.start … step.end with its duration.
    The body may fill the yielded dict (result, cost, model…); it is recorded on step.end."""
    info: dict[str, Any] = {}
    t0 = time.time()
    emit(state_dir, "step.start", name=name, **data)
    try:
        yield info
    except BaseException as e:
        info.setdefault("result", "error")
        info.setdefault("why", repr(e)[:200])
        raise
    finally:
        emit(state_dir, "step.end", name=name, seconds=round(time.time() - t0, 1), **info)


def current_run() -> str:
    return _run.get("id") or os.environ.get("MEMEX_RUN", "")


def emit(state_dir: Path, type: str, job: str | None = None, **data: Any) -> dict:
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": current_run(), "job": job or _run.get("job", ""),
          "type": type, "data": data}
    if _run.get("parent"):
        ev["parent"] = _run["parent"]
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
    line = render(ev)
    with open(state_dir / "memex.log", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    return ev


def render(ev: dict, lang: str | None = None, job: bool = True) -> str:
    """One readable line from the language pack. Lists also give {key_n} (their length); a boolean `passed`
    picks the variant event.<type>_pass / _fail; steps get {label} from step.<name>."""
    raw = ev.get("data") or {}
    data: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            data[k], data[f"{k}_n"] = ", ".join(map(str, v)), len(v)
        elif isinstance(v, float):
            data[k] = round(v, 2)
        else:
            data[k] = v
    if ev["type"].startswith("step."):
        data["label"] = i18n.t(f"step.{raw.get('name', '')}", lang) if i18n.has(f"step.{raw.get('name', '')}") else raw.get("name", "")
    if isinstance(raw.get("cost_usd"), (int, float)):
        data["cost"] = f"${raw['cost_usd']:.2f}"
    else:
        data["cost"] = "-"
    keys = [f"event.{ev['type']}"]
    if isinstance(raw.get("passed"), bool):
        keys.insert(0, f"event.{ev['type']}_{'pass' if raw['passed'] else 'fail'}")
    head = f"{ev.get('job') or '-'}: " if job else ""
    for key in keys:
        if i18n.has(key):
            return head + i18n.t(key, lang, **data)
    extra = " ".join(f"{k}={v}" for k, v in data.items() if k != "cost" or v != "-")
    return f"{head}{ev['type']} {extra}".rstrip()


def read(state_dir: Path, since: float | None = None, types: tuple[str, ...] = (), run: str | None = None) -> Iterator[dict]:
    p = state_dir / "events.jsonl"
    if not p.exists():
        return
    with open(p, "rb") as f:
        if since is not None:
            f.seek(_offset_since(f, since))
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


def _offset_since(f, since: float) -> int:
    """Events are appended in time order, so the first one at or after `since` can be found by bisecting byte offsets."""
    f.seek(0, os.SEEK_END)
    lo, hi = 0, f.tell()
    while hi - lo > 4096:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()                     # skip the partial line
        line = f.readline()
        try:
            t = _epoch(json.loads(line).get("ts", "")) if line else float("inf")
        except ValueError:
            t = 0.0
        if t < since:
            lo = mid
        else:
            hi = mid
    if lo:
        f.seek(lo)
        f.readline()
        return f.tell()
    return 0


def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0
