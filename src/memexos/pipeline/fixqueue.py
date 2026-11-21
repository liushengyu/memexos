"""Which review findings still need fixing, which first, and which can simply be recorded as done.

Three rules keep the fix queue honest:

* A review round that a later round superseded within the same compile is settled only for the problems whose sentence
  the rework actually changed: a re-review checks rewritten sentences, so an untouched flagged sentence was never
  looked at again. Problems that cannot be checked this way (missed points, integration notes, overwrites) stay queued.
  To tell, every review keeps a snapshot of the pages it read (`pages.tgz`).
* The last review of a fix, inbox or correct run is recorded as done only when it found nothing: its minor problems
  were never shown to anyone, so they wait for `memex fix`.
* `memex fix` takes the worst problems first, about `pipeline.fix_max_problems` per run, and records reviews of
  compiles that never reached the main branch (held or discarded) without fixing anything.
"""
from __future__ import annotations

import json
import re
import subprocess
import tarfile
from pathlib import Path

from memexos import events
from memexos.pipeline import report
from memexos.pipeline.stage import ledger

CLAIM_KINDS = {"CONTRADICTED", "UNSUPPORTED_C", "UNSUPPORTED", "PARTIAL"}     # problems with a sentence one can find again


def key(p: dict) -> str:
    return f"{p['kind']}|{p['page']}|{(p['wiki'] or p['source'] or p['why'])[:120]}"


def covered(d: Path) -> set[str]:
    try:
        return set(json.loads((d / "covered.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def live(d: Path) -> list[dict]:
    """The review's problems minus those a later round already checked."""
    cov = covered(d)
    return [p for p in report.problems(d) if key(p) not in cov]


def pages_of(field: str) -> list[str]:
    return re.findall(r"(wiki/[^:;,\n`]+?\.md|index\.md)", field or "")


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _sentences(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        for s in re.split(r"(?<=[.!?。！？])\s*", line):
            s = s.strip()
            if len(s) >= 6:
                out.append(s)
    return out


def best_sentence(text: str, claim: str, floor: float = 0.5) -> str | None:
    """The page sentence closest to the reviewer's wording of a claim (reviewers paraphrase)."""
    cb = _bigrams(claim)
    if not cb:
        return None
    best, score = None, 0.0
    for s in _sentences(text):
        sc = len(cb & _bigrams(s)) / len(cb)
        if sc > score:
            best, score = s, sc
    return best if score >= floor else None


# ---------------------------------------------------------------- snapshots and settling
def snapshot(stage_dir: Path, base: str, out: Path) -> int:
    """Keep the pages a review is about to read, so a later round can tell which flagged sentences were rewritten."""
    def names(*args: str) -> list[str]:
        r = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(stage_dir), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return [n for n in r.stdout.split("\n") if n]
    files = sorted({f for f in names("diff", "--name-only", base, "--", "wiki", "index.md") +
                    names("ls-files", "--others", "--exclude-standard", "wiki") if (stage_dir / f).is_file()})
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out / "pages.tgz", "w:gz") as t:
        for f in files:
            t.add(stage_dir / f, arcname=f)
    return len(files)


def _snapshot_pages(d: Path) -> dict[str, str] | None:
    try:
        with tarfile.open(d / "pages.tgz") as t:
            return {m.name: t.extractfile(m).read().decode("utf-8", "replace") for m in t.getmembers() if m.isfile()}
    except (OSError, tarfile.TarError):
        return None


def settle(stage_dir: Path, dirs: list[Path]) -> list[str]:
    """For reviews superseded within one compile: mark the problems whose sentence the final draft no longer contains
    (covered.json) and return the names of reviews with every problem covered."""
    done = []
    for d in dirs:
        snap = _snapshot_pages(d)
        if snap is None:
            continue
        ps, cov = report.problems(d), []
        for p in ps:
            if p["kind"] not in CLAIM_KINDS:
                continue
            for path in pages_of(p["page"]):
                s = best_sentence(snap.get(path, ""), p["wiki"])
                if not s:
                    continue
                try:
                    now = (stage_dir / path).read_text(encoding="utf-8")
                except OSError:
                    now = ""
                if s not in now:
                    cov.append(key(p))
                    break
        (d / "covered.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
        if len(cov) == len(ps):
            done.append(d.name)
    return done


def verified(dirs: list[Path]) -> list[str]:
    """Reviews of one fix/inbox/correct run that can be recorded as done: every earlier round (its problems went back
    to the writer) and the last one only if it found nothing."""
    return [d.name for i, d in enumerate(dirs) if i < len(dirs) - 1 or not report.problems(d)]


# ---------------------------------------------------------------- the queue
def _branch(name: str) -> str | None:
    m = re.match(r"\d{4}-\d{2}-\d{2}-staging_(\d{8}-\d{6})", name)
    return f"staging/{m.group(1)}" if m else None


def _outcomes(cfg) -> dict[str, str]:
    out: dict[str, str] = {}
    for ev in events.read(cfg.paths.state, types=("publish", "hold", "stage.discard")):
        b = ev.get("branch")
        if b:
            out[b] = "published" if ev["type"] == "publish" else "dropped"
    return out


def _branches(vault: Path, *flags: str) -> set[str]:
    r = subprocess.run(["git", "-C", str(vault), "branch", "--list", "staging/*", *flags, "--format=%(refname:short)"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return set(r.stdout.split())


def queue(cfg) -> tuple[list[tuple[str, str]], list[dict]]:
    """(reviews to record without fixing, with why) and (reviews to fix, worst first)."""
    root = cfg.paths.state / "gate2"
    if not root.is_dir():
        return [], []
    done = ledger(cfg.vault, "fixed-reviews.txt")
    names = [d.name for d in sorted(root.iterdir()) if (d / "review.json").exists()]
    outcome = _outcomes(cfg)
    kept, unmerged = _branches(cfg.vault), _branches(cfg.vault, "--no-merged", "HEAD")
    reworked = {_branch(n) for n in names if "-fix" in n and _branch(n)}
    settle_now, todo = [], []
    for n in names:
        if n in done:
            continue
        b = _branch(n)
        # a kept branch (held for a person) counts as published only once merged; a deleted one by how its run ended
        if b and ((b in unmerged) if b in kept else outcome.get(b) == "dropped"):
            settle_now.append((n, "not published"))
            continue
        ps = live(root / n)
        if not ps:
            settle_now.append((n, "no problems"))
            continue
        worst = min(report.ORDER[report.SEVERITY[p["kind"]]] for p in ps)
        todo.append({"dir": root / n, "n": len(ps), "worst": worst, "superseded": bool(b and "-fix" not in n and b in reworked)})
    # worst first; at equal severity, reviews nobody reworked before the ones a rework probably fixed; then oldest first
    todo.sort(key=lambda t: (t["worst"], t["superseded"], t["dir"].name))
    return settle_now, todo


def pick(cfg) -> tuple[list[tuple[str, str]], list[Path], int]:
    settle_now, todo = queue(cfg)
    cap = int(cfg.get("pipeline.fix_max_problems") or 40)
    max_dirs = int(cfg.get("pipeline.fix_max_reviews") or 8)
    out, n = [], 0
    for t in todo:
        if out and (n + t["n"] > cap or len(out) >= max_dirs):
            break
        out.append(t["dir"])
        n += t["n"]
    return settle_now, out, n


def backlog(cfg) -> dict:
    settle_now, todo = queue(cfg)
    return {"reviews": len(todo), "problems": sum(t["n"] for t in todo), "record_now": len(settle_now),
            "oldest": min((t["dir"].name[:10] for t in todo), default=None)}
