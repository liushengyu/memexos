"""The write-side jobs: ingest, lint, fix, correct.

ingest   compile pending raw files in small batches: writer → gate 1 (rework up to N rounds) → self-check →
         gate 2 (rework up to N rounds) → publish. Never publishes anything the review did not pass; when the
         review cannot run (quota, network) the batch is discarded and compiling pauses briefly rather than
         publishing unchecked text.
lint     weekly health check of the whole wiki (gate 1 only: it reorganizes, it does not add claims)
fix      repair what reviews found in already-published batches, then re-review the repair
correct  apply raw/feedback/ to the exact sentences it targets, then review

Only one job writes at a time (the "write" lock). Long jobs release the lock between batches so a queued job
(an intake commit, a correction) is not starved.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path

from memexos import backends, events, i18n, lock
from memexos.pipeline import gate1, gate2, report, selfcheck
from memexos.pipeline.stage import Stage, add_to_ledger, git, ledger, next_batch, pending_feedback, pending_raw


def today() -> str:
    return datetime.date.today().isoformat()


def fence(text: str) -> str:
    """Wrap program output that quotes sources, so instructions inside the quotes are read as data."""
    return i18n.t("pipeline.fence_start") + "\n" + text + "\n" + i18n.t("pipeline.fence_end")


def paused(cfg) -> bool:
    return (cfg.paths.state / "PAUSE").exists() and not os.environ.get("MEMEX_IGNORE_PAUSE")


# ---------------------------------------------------------------- the writer + gate 1
class Writer:
    """Runs the writer agent in the staging worktree, then gate 1 with rework rounds."""

    def __init__(self, cfg, stage: Stage, job: str):
        self.cfg, self.stage, self.job = cfg, stage, job
        self.last = ""          # the writer's final summary line
        self.report = ""        # last gate 1 report (JSON text) or failure reason

    def policy(self) -> backends.Policy:
        d = self.stage.dir
        return backends.Policy(write=[d / "wiki", d / "inbox", d / "index.md", d / "log.md"])

    def _agent(self, prompt: str) -> backends.Result:
        wd = self.cfg.paths.state / "runs" / (events.current_run() or "run") / f"writer-{time.strftime('%H%M%S')}"
        return backends.agent(self.cfg, "writer", prompt, cwd=self.stage.dir, workdir=wd, policy=self.policy(),
                              timeout=float(self.cfg.get("pipeline.writer_timeout") or 5400), job=self.job)

    def _step(self, name: str, prompt: str, **data) -> backends.Result:
        with events.step(self.cfg.paths.state, name, **data) as info:
            r = self._agent(prompt)
            info.update(ok=r.ok, model=r.model, cost_usd=r.cost_usd)
        return r

    def write(self, prompt: str, kind: str, step: str = "write", reason: str = "") -> int:
        """0 = written and gate 1 passed; 1 = the writer failed; 2 = gate 1 still fails after the rework rounds.
        `step` names the timeline segment: write for a first draft, rework when answering a check."""
        p, attempt = prompt, 0
        extra = {"reason": reason} if reason else {}
        while True:
            r = self._step(step, p, **extra)
            if r.denials:
                events.emit(self.cfg.paths.state, "writer.denials", count=len(r.denials), example=r.denials[0])
            if r.ok:
                break
            attempt += 1
            if r.transient and attempt <= 3:
                wait = attempt * float(self.cfg.get("pipeline.transient_wait_seconds") or 180)
                events.emit(self.cfg.paths.state, "writer.retry", attempt=attempt, wait_s=wait, why=r.tail[-160:])
                time.sleep(wait)
                p = prompt + "\n\n" + i18n.t("pipeline.resume_note")
                continue
            self.report = i18n.t("pipeline.writer_failed", backend=r.backend, why=r.tail[-300:])
            return 1
        lines = (r.text or "").strip().splitlines()
        self.last = lines[-1][:500] if lines else ""
        rounds = int(self.cfg.get("pipeline.gate1_fix_rounds") or 2)
        for rnd in range(rounds + 1):
            try:
                rep = gate1.check(self.stage.dir, self.stage.base, kind)
            except Exception as e:  # noqa: BLE001 — a broken check must not be blamed on the writer
                self.report = f"gate 1 failed to run: {e!r}"
                return 1
            self.report = json.dumps(rep, ensure_ascii=False, indent=1)
            events.emit(self.cfg.paths.state, "gate1.result", passed=rep["passed"], errors=len(rep["errors"]),
                        warnings=len(rep["warnings"]), round=rnd)
            if rep["passed"]:
                return 0
            if rnd == rounds:
                return 2
            r = self._step("rework", i18n.prompt("rework_gate1", kind=kind, report=fence(self.report)), reason="gate1")
            if not r.ok:
                self.report = i18n.t("pipeline.writer_failed", backend=r.backend, why=r.tail[-300:])
                return 1
            self.last = (r.text or "").strip()[-500:]
        return 2


# ---------------------------------------------------------------- gate 2 plumbing
def g2_dir(cfg, stage: Stage, mode: str) -> Path:
    d = cfg.paths.state / "gate2" / f"{today()}-{stage.branch.replace('/', '_')}"
    return d.with_name(d.name + f"-fix{time.strftime('%H%M%S')}") if mode == "fix" else d


def review(cfg, stage: Stage, raws: list[str], mode: str, job: str) -> tuple[int, dict, Path]:
    if (cfg.get("pipeline.gate2") or "enforce") == "off":
        return 4, {}, Path()
    d = g2_dir(cfg, stage, mode)
    with events.step(cfg.paths.state, "gate2", mode=mode) as info:
        code, rep = gate2.run(cfg, stage.dir, stage.base, raws, d, mode=mode, job=job)
        m = rep.get("metrics") or {}
        info.update(result={0: "passed", 1: "failed"}.get(code, "incomplete"), model=m.get("model"), cost_usd=m.get("cost_usd"),
                    problems=len(report.problems(d)))
    events.emit(cfg.paths.state, "gate2.result", code=code, passed=rep.get("passed") if code in (0, 1) else None, mode=mode,
                problems=len(report.problems(d)),
                backend=m.get("backend"), model=m.get("model"), seconds=m.get("elapsed_s"), cost_usd=m.get("cost_usd"),
                dir=d.name)
    return code, rep, d


def record_review(cfg, stage: Stage, d: Path, status_key: str) -> None:
    f = stage.dir / "inbox" / f"{today()}-review.md"
    head = "" if f.exists() else i18n.t("report.header")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(head + report.render(cfg.vault, d, i18n.t("report.statuses." + status_key)))


def unavailable_until(cfg) -> str | None:
    """While the review service is down, do not even start compiling (it could not be published anyway)."""
    p = cfg.paths.state / "gate2-unavailable.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    until = d.get("until") or (p.stat().st_mtime + float(cfg.get("pipeline.gate2_cooldown_seconds") or 1800))
    if time.time() < until:
        return f"{d.get('reason', '')[:120]} (until {time.strftime('%m-%d %H:%M', time.localtime(until))})"
    return None


def mark_unavailable(cfg, rep: dict) -> None:
    reason = (rep.get("errors") or ["?"])[0][:300]
    m = re.search(r"try again at ([A-Z][a-z]{2}) (\d+)\w*, (\d{4}) (\d+):(\d+) (AM|PM)", json.dumps(rep))
    until = None
    if m:
        mon, day, year, hh, mm, ap = m.groups()
        hh = int(hh) % 12 + (12 if ap == "PM" else 0)
        try:
            until = datetime.datetime.strptime(f"{year} {mon} {day} {hh}:{mm}", "%Y %b %d %H:%M").timestamp()
        except ValueError:
            until = None
    p = cfg.paths.state / "gate2-unavailable.json"
    p.write_text(json.dumps({"ts": time.strftime("%F %T"), "reason": reason, "until": until}, ensure_ascii=False))
    events.emit(cfg.paths.state, "gate2.unavailable", reason=reason, until=until)


def clear_unavailable(cfg) -> None:
    (cfg.paths.state / "gate2-unavailable.json").unlink(missing_ok=True)


def review_loop(cfg, stage: Stage, writer: Writer, raws: list[str], mode: str, first_status: str, job: str,
                fix_prompt_key: str = "rework_gate2", what: str = "") -> tuple[str, list[Path], dict]:
    """Review; on failure send the problems back to the writer and review again (up to gate2_fix_rounds).
    Returns (outcome, review dirs, last report): outcome is passed | failed | unavailable | off | writer-failed."""
    dirs: list[Path] = []
    rounds = int(cfg.get("pipeline.gate2_fix_rounds") or 2)
    code, rep, d = review(cfg, stage, raws, mode, job)
    rnd = 0
    while True:
        if code == 4:
            return "off", dirs, rep
        if code == 2:
            mark_unavailable(cfg, rep)
            return "unavailable", dirs, rep
        clear_unavailable(cfg)
        dirs.append(d)
        if code == 0:
            # Passed, possibly with minor problems; those are left for `memex fix` (status says so when there are any).
            record_review(cfg, stage, d, first_status)
            return "passed", dirs, rep
        rnd += 1
        record_review(cfg, stage, d, "redo" if rnd <= rounds else "redo_failed")
        if rnd > rounds:
            return "failed", dirs, rep
        _, problems = report.for_writer([d])
        prompt = i18n.prompt(fix_prompt_key, problems=fence(problems), what=what)
        c = writer.write(prompt, "ingest" if mode == "ingest" else "lint", step="rework", reason="gate2")
        if c != 0:
            return "writer-failed", dirs, {"errors": [writer.report]}
        code, rep, d = review(cfg, stage, raws, "fix", job)


# ---------------------------------------------------------------- ingest
def classifications(cfg, raws: list[str]) -> str:
    try:
        from memexos import catalog
    except ImportError:
        return "\n".join(f"- {r}: {i18n.t('pipeline.unclassified')}" for r in raws)
    try:
        catalog.ensure(cfg, raws)
        return catalog.lines(cfg, raws)
    except Exception as e:  # noqa: BLE001 — compiling without classification is better than not compiling
        events.emit(cfg.paths.state, "classify.failed", why=repr(e)[:200])
        return "\n".join(f"- {r}: {i18n.t('pipeline.unclassified')}" for r in raws)


def ingest_batch(cfg) -> int:
    """0 published, 1 failed or held, 3 nothing to do."""
    raws = next_batch(cfg.vault, int(cfg.get("pipeline.ingest_batch") or 3))
    if not raws:
        return 3
    job = "ingest"
    events.emit(cfg.paths.state, "ingest.start", files=raws, remaining=len(pending_raw(cfg.vault)))
    prompt = i18n.prompt("compile", files="\n".join(f"- {r}" for r in raws), classifications=classifications(cfg, raws), date=today())
    stage = Stage(cfg).open()
    w = Writer(cfg, stage, job)
    c = w.write(prompt, "ingest")
    if c == 1:
        events.emit(cfg.paths.state, "ingest.failed", why=w.report[-300:])
        stage.discard(w.report)
        return 1
    if c == 2:
        stage.hold(i18n.t("pipeline.gate1_failed", rounds=cfg.get("pipeline.gate1_fix_rounds") or 2), raws, w.report)
        return 1
    events.emit(cfg.paths.state, "writer.done", summary=w.last)
    if (cfg.get("pipeline.selfcheck") or "on") != "off":
        with events.step(cfg.paths.state, "selfcheck") as info:
            sc, flagged, summary = selfcheck.run(cfg, stage.dir, stage.base, raws)
            info["result"] = {0: "passed", 1: "flagged"}.get(sc, "skipped")
        events.emit(cfg.paths.state, "selfcheck.result", code=sc, summary=summary)
        if sc == 1:
            c = w.write(i18n.prompt("selfcheck", flagged=fence(flagged)), "ingest", step="rework", reason="selfcheck")
            if c == 2:
                stage.hold(i18n.t("pipeline.selfcheck_gate1"), raws, w.report)
                return 1
            if c == 1:          # half-done rework: the draft must pass gate 1 again before review
                rep = gate1.check(stage.dir, stage.base, "ingest")
                if not rep["passed"]:
                    stage.hold(i18n.t("pipeline.selfcheck_gate1"), raws, json.dumps(rep, ensure_ascii=False))
                    return 1
    outcome, dirs, rep = review_loop(cfg, stage, w, raws, "ingest", "to_fix", job)
    if outcome == "unavailable":
        stage.discard(i18n.t("pipeline.gate2_unavailable"))
        return 1
    if outcome in ("failed", "writer-failed"):
        stage.hold(i18n.t("pipeline.gate2_failed"), raws, json.dumps(rep.get("errors"), ensure_ascii=False, indent=1))
        return 1
    # The compiled list is written only now: written earlier, gate 1 would see it as a write outside the allowed places.
    add_to_ledger(stage.dir, "ingested.txt", raws)
    # Failed rounds were handled by the rework; the passing review may still list minor problems for `memex fix`.
    add_to_ledger(stage.dir, "fixed-reviews.txt", [d.name for d in dirs[:-1]])
    if stage.publish("auto-ingest: " + " ".join(Path(r).name for r in raws)[:900]):
        return 0
    stage.hold(i18n.t("pipeline.merge_failed"), raws)
    return 1


def ingest(cfg) -> int:
    why = unavailable_until(cfg)
    if why:
        events.emit(cfg.paths.state, "ingest.waiting", why=why)
        print(i18n.t("pipeline.review_down", why=why))
        return 0
    n, max_batches = 0, cfg.get("pipeline.max_batches")
    while True:
        if paused(cfg):
            print(i18n.t("pipeline.paused", path=cfg.paths.state / "PAUSE"))
            return 0
        lock.yield_to_waiters(cfg.paths.locks, "write")
        try:
            with lock.held(cfg.paths.locks, "write"):
                rc = ingest_batch(cfg)
        except lock.Busy:
            print(i18n.t("pipeline.busy"))
            return 0
        if rc == 3:
            if n == 0:
                print(i18n.t("pipeline.nothing"))
            return 0
        if rc != 0:
            return 1
        n += 1
        if max_batches and n >= int(max_batches):
            return 0


# ---------------------------------------------------------------- lint
def usage_summary(cfg) -> str:
    try:
        from memexos import usage
        return usage.report_text(cfg, days=30)[:6000]
    except Exception:  # noqa: BLE001 — usage data is optional context
        return i18n.t("pipeline.no_usage")


def lint(cfg) -> int:
    try:
        with lock.held(cfg.paths.locks, "write", wait=5400):
            stage = Stage(cfg).open()
            w = Writer(cfg, stage, "lint")
            c = w.write(i18n.prompt("lint", usage=usage_summary(cfg), date=today()), "lint")
            if c == 1:
                stage.discard(w.report)
                return 1
            if c == 2:
                stage.hold(i18n.t("pipeline.gate1_failed", rounds=cfg.get("pipeline.gate1_fix_rounds") or 2), [], w.report)
                return 1
            if not stage.publish(f"auto-lint: {today()}"):
                stage.hold(i18n.t("pipeline.merge_failed"), [])
                return 1
    except lock.Busy:
        print(i18n.t("pipeline.busy"))
        return 0
    expire_inbox(cfg)
    return 0


def expire_inbox(cfg) -> None:
    """Reports older than inbox.expire_days are stale; the lifecycle rule deletes them."""
    days = float(cfg.get("inbox.expire_days") or 30)
    old = [p for p in (cfg.vault / "inbox").glob("*.md") if time.time() - p.stat().st_mtime > days * 86400]
    if old:
        for p in old:
            p.unlink()
        git(cfg.vault, "add", "-A", "inbox")
        git(cfg.vault, "commit", "-q", "-m", f"inbox: expire {len(old)} reports older than {int(days)} days", check=False)


# ---------------------------------------------------------------- fix
def unfixed_reviews(cfg) -> list[Path]:
    done = ledger(cfg.vault, "fixed-reviews.txt")
    root = cfg.paths.state / "gate2"
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir()) if (d / "review.json").exists() and d.name not in done]


def fix(cfg) -> int:
    dirs = unfixed_reviews(cfg)[: int(cfg.get("pipeline.fix_max_reviews") or 4)]
    if not dirs:
        print(i18n.t("pipeline.nothing_to_fix"))
        return 0
    n, problems = report.for_writer(dirs)
    raws = sorted({r for d in dirs for r in report.raws_of(d)})
    try:
        with lock.held(cfg.paths.locks, "write", wait=5400):
            stage = Stage(cfg).open()
            if n == 0:
                add_to_ledger(stage.dir, "fixed-reviews.txt", [d.name for d in dirs])
                stage.publish("fix: reviews without problems, recorded")
                return 0
            events.emit(cfg.paths.state, "fix.start", reviews=len(dirs), problems=n)
            for d in dirs:
                record_review(cfg, stage, d, "to_fix")
            w = Writer(cfg, stage, "fix")
            c = w.write(i18n.prompt("fix", n=n, problems=fence(problems), date=today()), "lint")
            if c != 0:
                (stage.hold(i18n.t("pipeline.gate1_failed", rounds=2), [], w.report) if c == 2 else stage.discard(w.report))
                return 1
            outcome, vdirs, rep = review_loop(cfg, stage, w, raws, "fix", "fix_passed", "fix",
                                              fix_prompt_key="rework_again", what=i18n.t("pipeline.what_fix"))
            if outcome == "unavailable":
                stage.discard(i18n.t("pipeline.gate2_unavailable"))
                return 1
            if outcome not in ("passed", "off"):
                stage.hold(i18n.t("pipeline.fix_failed"), [], json.dumps(rep.get("errors"), ensure_ascii=False, indent=1))
                return 1
            add_to_ledger(stage.dir, "fixed-reviews.txt", [d.name for d in dirs + vdirs])
            return 0 if stage.publish(f"fix: {n} problems found by review") else 1
    except lock.Busy:
        print(i18n.t("pipeline.busy"))
        return 0


# ---------------------------------------------------------------- correct
def feedback_brief(cfg, files: list[str]) -> str:
    from memexos.util import split_frontmatter
    out = []
    for i, rel in enumerate(files, 1):
        fm, _ = split_frontmatter((cfg.vault / rel).read_text(encoding="utf-8", errors="replace"))
        pages = fm.get("pages") if isinstance(fm.get("pages"), list) else [fm.get("pages")]
        out.append(i18n.t("pipeline.feedback_item", i=i, path=rel, pages=", ".join(f"[[{p}]]" for p in pages if p),
                          claim=fm.get("claim", ""), source=fm.get("provided_by") or fm.get("fetched_via") or ""))
    return "\n".join(out)


def correct(cfg) -> int:
    fb = pending_feedback(cfg.vault)[: int(cfg.get("pipeline.correct_max") or 6)]
    if not fb:
        print(i18n.t("pipeline.no_feedback"))
        return 0
    try:
        with lock.held(cfg.paths.locks, "write", wait=5400):
            stage = Stage(cfg).open()
            events.emit(cfg.paths.state, "correct.start", files=fb)
            w = Writer(cfg, stage, "correct")
            c = w.write(i18n.prompt("correct", n=len(fb), date=today(), feedback=fence(feedback_brief(cfg, fb))), "lint")
            if c != 0:
                (stage.hold(i18n.t("pipeline.gate1_failed", rounds=2), fb, w.report) if c == 2 else stage.discard(w.report))
                return 1
            results_file = stage.dir / "inbox" / "correct-results.json"
            try:
                results = json.loads(results_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                results = []
            results_file.unlink(missing_ok=True)            # results are for the program, not the vault
            changed = subprocess.run(["git", "-C", str(stage.dir), "diff", "--quiet", stage.base, "--", "wiki", "index.md"],
                                     stdin=subprocess.DEVNULL).returncode != 0
            dirs: list[Path] = []
            if changed:
                outcome, dirs, rep = review_loop(cfg, stage, w, fb + _cited_raws(stage), "fix", "fix_passed", "correct",
                                                 fix_prompt_key="rework_again", what=i18n.t("pipeline.what_correct"))
                if outcome == "unavailable":
                    stage.discard(i18n.t("pipeline.gate2_unavailable"))
                    return 1
                if outcome not in ("passed", "off"):
                    stage.hold(i18n.t("pipeline.correct_failed"), fb, json.dumps(rep.get("errors"), ensure_ascii=False, indent=1))
                    return 1
            add_to_ledger(stage.dir, "corrected.txt", fb)
            add_to_ledger(stage.dir, "fixed-reviews.txt", [d.name for d in dirs])
            if not stage.publish(f"correct: applied {len(fb)} pieces of feedback"):
                stage.hold(i18n.t("pipeline.merge_failed"), fb)
                return 1
            commit = git(cfg.vault, "rev-parse", "--short", "HEAD").strip()
            events.emit(cfg.paths.state, "correct.done", results=results if isinstance(results, list) else [], commit=commit)
            try:
                from memexos import inbox
                inbox.feedback_done(cfg, results, commit)
            except ImportError:
                pass
            return 0
    except lock.Busy:
        print(i18n.t("pipeline.busy"))
        return 0


def _cited_raws(stage: Stage) -> list[str]:
    """Raw files cited by the pages a change touched (what the reviewer must compare against)."""
    from memexos import rawref
    names = rawref.index(stage.dir)
    files = git(stage.dir, "diff", "--name-only", "-z", stage.base, "--", "wiki").split("\0")
    out: list[str] = []
    for f in files:
        if f.endswith(".md") and (stage.dir / f).exists():
            out += [r for r in rawref.find((stage.dir / f).read_text(encoding="utf-8"), names, stage.dir) if not r.startswith("raw/assets/")]
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- CLI
def main(loader, argv: list[str], job: str) -> int:
    cfg = loader()
    if not (cfg.vault / "raw").is_dir():
        print(i18n.t("cli.no_vault", path=cfg.vault))
        return 1
    with events.run(cfg.paths.state, job):
        return {"ingest": ingest, "lint": lint, "fix": fix, "correct": correct}[job](cfg)


def cmd_ingest(loader, argv):
    return main(loader, argv, "ingest")


def cmd_lint(loader, argv):
    return main(loader, argv, "lint")


def cmd_fix(loader, argv):
    return main(loader, argv, "fix")


def cmd_correct(loader, argv):
    return main(loader, argv, "correct")
