"""The staging worktree, and the ledgers that say what has been compiled.

Compiling never happens in the vault's own working tree. New raw files are committed on the main branch, a
worktree is checked out from that commit into the state directory, the writer works there, the gates check it,
and only a passing batch is merged back. Agents reading the vault therefore never see a half-compiled state, and
every published change is a commit that can be reverted.

Ledgers (in .memex/, versioned with the vault):
  ingested.txt   raw files already compiled (written only at the moment of publishing)
  held.txt       raw files whose compile failed its checks; not retried automatically
  corrected.txt  feedback files already applied
  fixed-reviews.txt  review results already handled by `memex fix`
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import time
from pathlib import Path

from memexos import events, i18n
from memexos.util import split_frontmatter


class GitError(Exception):
    pass


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(cwd), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def ledger(vault: Path, name: str) -> set[str]:
    p = vault / ".memex" / name
    try:
        return {l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}
    except OSError:
        return set()


def add_to_ledger(root: Path, name: str, items) -> None:
    """Merge items into a ledger kept sorted by byte order (stable across machines and locales)."""
    p = root / ".memex" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = ledger(root, name) | {i for i in items if i}
    p.write_text("".join(f"{x}\n" for x in sorted(cur, key=lambda s: s.encode("utf-8"))), encoding="utf-8")


def pending_raw(vault: Path) -> list[str]:
    """Raw files waiting to be compiled: everything under raw/ except attachments and feedback, minus ledgers."""
    seen = ledger(vault, "ingested.txt") | ledger(vault, "held.txt")
    out = []
    root = vault / "raw"
    for d, dirs, files in os.walk(root):
        rel = Path(d).relative_to(vault).as_posix()
        dirs[:] = [x for x in dirs if not x.startswith(".") and f"{rel}/{x}" not in ("raw/assets", "raw/feedback")]
        for n in files:
            p = f"{rel}/{n}"
            if not n.startswith(".") and p not in seen:
                out.append(p)
    return sorted(out)


def pending_feedback(vault: Path) -> list[str]:
    seen = ledger(vault, "corrected.txt") | ledger(vault, "held.txt")
    out = []
    for p in sorted((vault / "raw" / "feedback").glob("*.md")):
        rel = p.relative_to(vault).as_posix()
        if p.name.startswith(".") or rel in seen:
            continue
        fm, _ = split_frontmatter(p.read_text(encoding="utf-8", errors="replace")[:3000])
        if fm.get("route") == "inbox-apply":       # used directly by the inbox step that created it
            continue
        out.append(rel)
    return out


def companion_parent(vault: Path, rel: str) -> str | None:
    """For a visual companion file, the transcript it belongs to."""
    try:
        fm, _ = split_frontmatter((vault / rel).read_text(encoding="utf-8", errors="ignore")[:3000])
    except OSError:
        return None
    if fm.get("visual_source") and fm.get("companion_to"):
        return f"raw/{fm['companion_to']}"
    return None


def next_batch(vault: Path, n: int) -> list[str]:
    """Up to n pending raw files. A video transcript and its visual companion are the same video and must be
    compiled together (one source page), even though their names sort far apart."""
    pend = pending_raw(vault)
    parent = {r: companion_parent(vault, r) for r in pend}
    kids: dict[str, list[str]] = {}
    for c, par in parent.items():
        if par:
            kids.setdefault(par, []).append(c)
    out: list[str] = []
    for r in pend:
        if len(out) >= n:
            break
        group = ([parent[r]] if parent.get(r) in pend else []) + [r] + kids.get(r, [])
        for x in group:
            if x not in out:
                out.append(x)
    return out


def main_branch(vault: Path) -> str:
    return git(vault, "rev-parse", "--abbrev-ref", "HEAD").strip() or "main"


def ensure_identity(vault: Path) -> None:
    """Automatic commits need an author; give the vault its own when the machine has none."""
    if not git(vault, "config", "user.email", check=False).strip():
        git(vault, "config", "user.name", "MemexOS")
        git(vault, "config", "user.email", "memexos@localhost")


class Stage:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vault = cfg.vault
        self.dir = cfg.paths.staging
        self.base = ""
        self.branch = ""

    def open(self) -> "Stage":
        ensure_identity(self.vault)
        git(self.vault, "add", "-A", "raw")
        if subprocess.run(["git", "-C", str(self.vault), "diff", "--cached", "--quiet"], stdin=subprocess.DEVNULL).returncode:
            names = git(self.vault, "diff", "--cached", "--name-only").split("\n")
            git(self.vault, "commit", "-q", "-m", "capture: " + " ".join(Path(n).name for n in names if n)[:900])
        self.base = git(self.vault, "rev-parse", "HEAD").strip()
        self.branch = f"staging/{time.strftime('%Y%m%d-%H%M%S')}"
        self.discard_worktree()
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        git(self.vault, "worktree", "add", "-q", "-b", self.branch, str(self.dir), self.base)
        (self.dir / "inbox").mkdir(exist_ok=True)
        (self.dir / ".memex").mkdir(exist_ok=True)
        events.emit(self.cfg.paths.state, "stage.open", branch=self.branch, base=self.base[:7])
        return self

    def discard_worktree(self) -> None:
        git(self.vault, "worktree", "remove", "--force", str(self.dir), check=False)
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        git(self.vault, "worktree", "prune", check=False)

    def discard(self, reason: str = "") -> None:
        """Throw the attempt away (raw stays queued and is retried next time)."""
        self.discard_worktree()
        if self.branch:
            git(self.vault, "branch", "-q", "-D", self.branch, check=False)
        events.emit(self.cfg.paths.state, "stage.discard", branch=self.branch, reason=reason[:300])

    def commit(self, message: str) -> None:
        git(self.dir, "add", "-A")
        if subprocess.run(["git", "-C", str(self.dir), "diff", "--cached", "--quiet"], stdin=subprocess.DEVNULL).returncode:
            git(self.dir, "commit", "-q", "-m", message)

    def publish(self, message: str) -> bool:
        self.commit(message)
        r = subprocess.run(["git", "-C", str(self.vault), "merge", "-q", "--no-edit", self.branch], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            git(self.vault, "merge", "--abort", check=False)
            events.emit(self.cfg.paths.state, "stage.merge_failed", branch=self.branch, why=(r.stderr or r.stdout)[-300:])
            return False
        self.discard_worktree()
        git(self.vault, "branch", "-q", "-d", self.branch, check=False)
        events.emit(self.cfg.paths.state, "publish", branch=self.branch, message=message[:300])
        # the full-text index right away (it takes well under a second), so a search just after publishing finds the
        # new pages; vectors follow in the background
        try:
            from memexos.search import fts
            fts.update(self.vault, self.cfg.paths.search_index)
        except Exception:  # noqa: BLE001 — the background reindex retries
            pass
        reindex_in_background(self.cfg)
        return True

    def hold(self, reason: str, raws: list[str], report: str = "") -> None:
        """Do not publish: keep the branch for a person to look at, stop retrying these raw files, note it in the inbox."""
        self.commit(f"held: {reason}")
        self.discard_worktree()
        if raws:
            add_to_ledger(self.vault, "held.txt", raws)
        today = datetime.date.today().isoformat()
        f = self.vault / "inbox" / f"{today}-blocked.md"
        f.parent.mkdir(exist_ok=True)
        head = "" if f.exists() else i18n.t("hold.title", date=today) + "\n\n"
        files = " ".join(Path(r).name for r in raws) or "-"
        body = i18n.t("hold.entry", time=time.strftime("%H:%M:%S"), branch=self.branch, reason=reason, files=files,
                      base=self.base[:7], vault=self.vault) + f"\n\n```\n{report[-4000:]}\n```\n\n"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(head + body)
        git(self.vault, "add", "inbox", ".memex")
        git(self.vault, "commit", "-q", "-m", f"inbox: compile held ({self.branch})", check=False)
        events.emit(self.cfg.paths.state, "hold", branch=self.branch, reason=reason, raws=raws)


def reindex_in_background(cfg) -> None:
    import sys
    subprocess.Popen([sys.executable, "-m", "memexos", "reindex"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, "MEMEX_VAULT": str(cfg.vault), "MEMEX_RUN": ""})
