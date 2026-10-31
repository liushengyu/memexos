"""Locks between jobs (POSIX advisory locks on files in <state>/locks/).

The kernel releases an fcntl lock when its process dies, so a crash or a
machine going to sleep mid-job never leaves a stale lock behind.

The write lock serializes everything that commits to the vault (compile, lint,
fix, intake commits). Long jobs work in batches and call `yield_to_waiters()`
between batches, so a short job waiting for the lock is not starved by a long
compile backlog.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    pass


@contextmanager
def held(locks_dir: Path, name: str, wait: float = 0.0, poll: float = 15.0):
    """Hold lock `name`. wait=0: raise Busy immediately when taken; otherwise wait up to `wait` seconds."""
    locks_dir.mkdir(parents=True, exist_ok=True)
    path = locks_dir / f"{name}.lock"
    want = locks_dir / f"{name}.wanted.{os.getpid()}"
    f = open(path, "a+")
    deadline = time.time() + wait
    try:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise Busy(name)
                want.touch()
                time.sleep(min(poll, max(0.05, deadline - time.time())))
        want.unlink(missing_ok=True)
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        yield
    finally:
        want.unlink(missing_ok=True)
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def holder(locks_dir: Path, name: str) -> int | None:
    """PID holding the lock, or None when free."""
    path = locks_dir / f"{name}.lock"
    if not path.exists():
        return None
    with open(path, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return None
        except BlockingIOError:
            f.seek(0)
            try:
                return int(f.read().strip() or 0) or -1
            except ValueError:
                return -1


def waiters(locks_dir: Path, name: str) -> list[int]:
    out = []
    for p in locks_dir.glob(f"{name}.wanted.*"):
        try:
            pid = int(p.name.rsplit(".", 1)[-1])
            os.kill(pid, 0)
            out.append(pid)
        except (ValueError, ProcessLookupError):
            p.unlink(missing_ok=True)
        except PermissionError:
            out.append(pid)
    return out


def yield_to_waiters(locks_dir: Path, name: str, poll: float = 15.0, max_wait: float = 5400.0) -> None:
    """Called while NOT holding the lock between batches: let queued jobs go first."""
    deadline = time.time() + max_wait
    while waiters(locks_dir, name) and time.time() < deadline:
        time.sleep(poll)
