"""End-to-end compile pipeline with scripted stand-in models: no network, no cost."""

import re
import subprocess
from pathlib import Path

import pytest

from memexos import events, backends, config, scaffold
from memexos.backends.base import Backend, Result
from memexos.pipeline import compile as C
from memexos.pipeline import fixqueue
from memexos.pipeline.stage import ledger


def git(v, *a):
    return subprocess.run(["git", "-C", str(v), *a], capture_output=True, text=True).stdout


class FakeWriter(Backend):
    """Writes a source page for every raw file named in the prompt; `mode` scripts misbehaviour."""
    name = "fake-writer"
    mode = "good"
    calls = []

    def available(self):
        return True, ""

    def agent(self, prompt, *, cwd, workdir, policy, **kw):
        cwd = Path(cwd)
        FakeWriter.calls.append(prompt[:60])
        if FakeWriter.mode == "touch-raw":
            (cwd / "raw" / "evil.md").write_text("x")
        for raw in re.findall(r"^- (raw/\S+\.md)", prompt, re.M):
            name = Path(raw).stem.split("-", 4)[-1]
            page = cwd / "wiki" / "sources" / f"{name}.md"
            page.write_text(f"# {name}\n\nA summary of the source that is long enough to count as a page body for gate one.\n\n"
                            f"## Sources\n\n- `{raw}`\n")
            idx = cwd / "index.md"
            idx.write_text(idx.read_text() + f"\n- [[{name}]] — summary\n")
            with open(cwd / "log.md", "a") as f:
                f.write(f"\n## [2026-01-01] ingest | {name}\n")
        if "fix" in prompt[:400].lower() or "problems below" in prompt or "were found" in prompt:
            with open(cwd / "log.md", "a") as f:
                f.write("\n## [2026-01-01] lint | fixed\n")
            for p in (cwd / "wiki" / "sources").glob("*.md"):
                p.write_text(p.read_text().replace("long enough", "long enough (fixed)"))
        return Result(ok=True, backend=self.name, text="done")


class FakeReviewer(Backend):
    name = "fake-reviewer"
    script: list = []

    def available(self):
        return True, ""

    def structured(self, prompt, schema, **kw):
        outcome = FakeReviewer.script.pop(0) if FakeReviewer.script else "pass"
        if outcome == "down":
            return Result(ok=False, backend=self.name, limited=True, tail="usage limit reached")
        claim = {"page": "wiki/sources/x.md", "claim": "c", "critical": True, "evidence": "e", "raw_location": "raw/x.md:1",
                 "reason": "r", "verdict": "SUPPORTED" if outcome == "pass" else "UNSUPPORTED"}
        return Result(ok=True, backend=self.name, result={"key_points": [], "claims": [claim], "overwrites": [], "injections": [],
                                                          "integration_notes": [], "notes_on_eval": "none"})


@pytest.fixture
def vault(home, monkeypatch):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    monkeypatch.setenv("MEMEX_VAULT", str(v))
    monkeypatch.setattr(backends, "REGISTRY", {"fake-writer": FakeWriter, "fake-reviewer": FakeReviewer})
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    monkeypatch.setattr("memexos.pipeline.stage.reindex_in_background", lambda cfg: None)
    FakeWriter.mode, FakeWriter.calls, FakeReviewer.script = "good", [], []
    for i in (1, 2):
        (v / "raw" / f"2026-01-0{i}-web-example-source{i}.md").write_text(
            f"---\nsource_type: web\ntitle: Source {i}\n---\n# Source {i}\n\nFacts.\n")
    return v


def cfg_for(v):
    cfg = config.load(v)
    cfg.data["models"].update(writer=["fake-writer"], reviewer=["fake-reviewer"])
    cfg.data["judge"]["backend"] = "none"
    return cfg


def test_ingest_publishes_reviewed_batch(vault):
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    assert (vault / "wiki" / "sources" / "example-source1.md").exists()          # merged into the vault itself
    assert ledger(vault, "ingested.txt") == {"raw/2026-01-01-web-example-source1.md", "raw/2026-01-02-web-example-source2.md"}
    assert not cfg.paths.staging.exists()                                 # worktree cleaned up
    log = git(vault, "log", "--format=%s")
    assert "auto-ingest:" in log and "capture:" in log
    assert "staging/" not in git(vault, "branch")
    review = next((vault / "inbox").glob("*-review.md")).read_text()
    assert "No problems found" in review
    from memexos.search import fts                                       # searchable the moment it is published
    assert "wiki/sources/example-source1.md" in [h["path"] for h in fts.search(vault, cfg.paths.search_index, ["example"], 20)]


def test_review_failure_goes_back_to_writer_then_publishes(vault):
    FakeReviewer.script = ["fail", "pass"]
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    assert any("independent reviewer" in c for c in FakeWriter.calls)     # the problems went back to the writer
    assert ledger(vault, "ingested.txt")
    # the failed round's flagged sentence is not on any page the rework changed, so it stays for `memex fix`
    assert not ledger(vault, "fixed-reviews.txt") and len(C.unfixed_reviews(cfg)) == 2


def test_writing_into_raw_is_held_not_published(vault):
    FakeWriter.mode = "touch-raw"
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 1
    assert not (vault / "raw" / "evil.md").exists()
    assert len(ledger(vault, "held.txt")) == 2
    blocked = next((vault / "inbox").glob("*-blocked.md")).read_text()
    assert "staging/" in blocked and "staging/" in git(vault, "branch")   # branch kept for a person to inspect


def test_review_unavailable_discards_and_waits(vault):
    FakeReviewer.script = ["down"]
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 1
    assert not ledger(vault, "ingested.txt") and not ledger(vault, "held.txt")   # still queued
    assert (cfg.paths.state / "gate2-unavailable.json").exists()
    assert C.ingest(cfg) == 0 and any(e["type"] == "ingest.waiting" for e in events.read(cfg.paths.state))


def test_pause(vault):
    cfg = cfg_for(vault)
    (cfg.paths.state).mkdir(parents=True, exist_ok=True)
    (cfg.paths.state / "PAUSE").write_text("x")
    assert C.ingest(cfg) == 0 and not ledger(vault, "ingested.txt")


def test_fix_repairs_minor_problems_of_published_batches(vault, monkeypatch):
    cfg = cfg_for(vault)
    # a passing review that still lists a minor (PARTIAL) problem
    def partial(self, prompt, schema, **kw):
        claim = {"page": "wiki/sources/example-source1.md", "claim": "c", "critical": False, "evidence": "e", "raw_location": "raw/x.md:1",
                 "reason": "rounded a number", "verdict": "PARTIAL"}
        return Result(ok=True, backend=self.name, result={"key_points": [], "claims": [claim], "overwrites": [], "injections": [],
                                                          "integration_notes": [], "notes_on_eval": ""})
    monkeypatch.setattr(FakeReviewer, "structured", partial)
    assert C.ingest(cfg) == 0
    pending = C.unfixed_reviews(cfg)
    assert len(pending) == 1
    monkeypatch.setattr(FakeReviewer, "structured", lambda self, p, s, **kw: Result(ok=True, backend=self.name, result={
        "key_points": [], "claims": [], "overwrites": [], "injections": [], "integration_notes": [], "notes_on_eval": ""}))
    assert C.fix(cfg) == 0
    assert not C.unfixed_reviews(cfg)
    assert "(fixed)" in (vault / "wiki" / "sources" / "example-source1.md").read_text()
    assert "fix:" in git(vault, "log", "-1", "--format=%s")


def test_timeline_records_each_step_of_a_batch(vault):
    from memexos import events, runs
    FakeReviewer.script = ["fail", "pass"]
    cfg = cfg_for(vault)
    with events.run(cfg.paths.state, "ingest"):
        assert C.ingest(cfg) == 0
    units = [u for u in runs.timeline(cfg.paths.state, 0) if u["kind"] == "batch"]
    assert len(units) == 1
    u = units[0]
    assert u["status"] == "published" and u["job"] == "ingest" and u["branch"].startswith("staging/")
    steps = [s["step"] for s in u["segs"]]
    assert steps[:2] == ["classify", "write"] and steps.count("gate2") == 2 and "rework" in steps
    assert [g["passed"] for g in u["gate2"]] == [False, True]
    assert all(s["t1"] is not None for s in u["segs"])
    assert any(m["type"] == "publish" for m in u["marks"])


def _lint_writer(monkeypatch):
    """A writer whose maintenance pass edits a published page and logs a lint entry."""
    def agent(self, prompt, *, cwd, workdir, policy, **kw):
        cwd = Path(cwd)
        FakeWriter.calls.append(prompt[:60])
        for p in (cwd / "wiki" / "sources").glob("*.md"):
            p.write_text(p.read_text().replace("A summary", "A tidied summary"))
        with open(cwd / "log.md", "a") as f:
            f.write("\n## [2026-01-02] lint | tidied\n")
        return Result(ok=True, backend=self.name, text="done")
    monkeypatch.setattr(FakeWriter, "agent", agent)


def test_lint_goes_through_gate2_before_publishing(vault, monkeypatch):
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    seen = []
    orig = FakeReviewer.structured

    def spy(self, prompt, schema, **kw):
        seen.append(prompt)
        return orig(self, prompt, schema, **kw)
    monkeypatch.setattr(FakeReviewer, "structured", spy)
    _lint_writer(monkeypatch)
    assert C.lint(cfg) == 0
    assert seen and "maintenance pass" in seen[-1]                         # reviewed with the lint note
    assert "raw/2026-01-01-web-example-source1.md" in seen[-1]             # against the sources the changed pages cite
    assert "auto-lint:" in git(vault, "log", "--format=%s")


def test_lint_that_fails_review_is_held(vault, monkeypatch):
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    FakeReviewer.script = ["fail", "fail", "fail", "fail"]
    _lint_writer(monkeypatch)
    before = (vault / "wiki" / "sources" / "example-source1.md").read_text()
    assert C.lint(cfg) == 1
    assert (vault / "wiki" / "sources" / "example-source1.md").read_text() == before   # nothing unreviewed reached the vault
    assert "auto-lint:" not in git(vault, "log", "--format=%s")


# ---------------------------------------------------------------- the fix queue: nothing dropped, nothing wrongly held
PAGE = "wiki/sources/example-source1.md"
REWRITTEN = "A summary of the source that is long enough to count as a page body for gate one"   # the fake rework edits it
UNTOUCHED = "raw/2026-01-01-web-example-source1.md"                                           # the rework leaves it


def reviews(monkeypatch, *rounds):
    """Script the reviewer: each round is a list of (verdict, critical, claim) on PAGE; a round with a bad claim fails."""
    script = list(rounds)

    def structured(self, prompt, schema, **kw):
        claims = [{"page": PAGE, "claim": c, "critical": crit, "evidence": "e", "raw_location": "raw/x.md:1", "reason": "r",
                   "verdict": v} for v, crit, c in (script.pop(0) if script else [])]
        return Result(ok=True, backend=self.name, result={"key_points": [], "claims": claims, "overwrites": [], "injections": [],
                                                          "integration_notes": [], "notes_on_eval": ""})
    monkeypatch.setattr(FakeReviewer, "structured", structured)


def test_a_rewritten_flagged_sentence_settles_the_failed_round(vault, monkeypatch):
    reviews(monkeypatch, [("UNSUPPORTED", True, REWRITTEN)], [])
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    first = [d for d in sorted((cfg.paths.state / "gate2").iterdir()) if "-fix" not in d.name][0]
    assert (first / "pages.tgz").exists()
    assert first.name in ledger(vault, "fixed-reviews.txt")              # the re-review checked the rewritten sentence


def test_an_untouched_flagged_sentence_stays_for_fix(vault, monkeypatch):
    reviews(monkeypatch, [("UNSUPPORTED", True, REWRITTEN), ("CONTRADICTED", True, UNTOUCHED)], [])
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    first = [d for d in sorted((cfg.paths.state / "gate2").iterdir()) if "-fix" not in d.name][0]
    assert first.name not in ledger(vault, "fixed-reviews.txt")
    left = fixqueue.live(first)
    assert [p["wiki"] for p in left] == [UNTOUCHED]                       # only the problem nobody looked at again


def test_a_usage_limit_during_rework_requeues_instead_of_holding(vault, monkeypatch):
    reviews(monkeypatch, [("UNSUPPORTED", True, REWRITTEN)])
    real = FakeWriter.agent

    def limited_on_rework(self, prompt, **kw):
        if "independent reviewer" in prompt:
            return Result(ok=False, backend=self.name, limited=True, tail="usage limit reached")
        return real(self, prompt, **kw)
    monkeypatch.setattr(FakeWriter, "agent", limited_on_rework)
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 1
    assert not ledger(vault, "held.txt") and not ledger(vault, "ingested.txt")    # still queued
    assert "staging/" not in git(vault, "branch")                                # the attempt was thrown away
    assert not list((vault / "inbox").glob("*-blocked.md"))


def test_other_writer_failures_hold_after_three_in_a_row(vault, monkeypatch):
    monkeypatch.setattr(FakeWriter, "agent", lambda self, prompt, **kw: Result(ok=False, backend=self.name, tail="crashed"))
    cfg = cfg_for(vault)
    for i in range(1, 4):
        assert C.ingest(cfg) == 1
        assert bool(ledger(vault, "held.txt")) == (i == 3), i


def test_fix_keeps_the_last_rounds_minor_problems_and_records_the_rest(vault, monkeypatch):
    reviews(monkeypatch, [("PARTIAL", False, REWRITTEN)])                 # passes, with one minor problem
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 0
    queued = C.unfixed_reviews(cfg)
    assert len(queued) == 1
    reviews(monkeypatch, [("PARTIAL", False, "A summary of the source (fixed)")])   # the fix passes with another minor one
    assert C.fix(cfg) == 0
    done = ledger(vault, "fixed-reviews.txt")
    assert queued[0].name in done                                        # what the fix worked on is recorded
    left = C.unfixed_reviews(cfg)
    assert len(left) == 1 and left[0].name not in done and "-fix" in left[0].name   # the new minor problem waits


def test_fix_records_reviews_of_compiles_that_were_never_published(vault, monkeypatch):
    FakeWriter.mode = "touch-raw"                                        # gate 1 holds the compile...
    cfg = cfg_for(vault)
    assert C.ingest(cfg) == 1
    # ...a review of that branch (as if it had been reviewed before being held) must not send anyone to fix it
    branch = [b for b in git(vault, "branch", "--list", "staging/*", "--format=%(refname:short)").split() if b][0]
    d = cfg.paths.state / "gate2" / f"2026-01-01-{branch.replace('/', '_')}"
    d.mkdir(parents=True)
    (d / "review.json").write_text('{"claims": [{"page": "wiki/sources/a.md", "claim": "c", "critical": true, "evidence": "",'
                                   ' "raw_location": "", "reason": "r", "verdict": "CONTRADICTED"}], "overwrites": [],'
                                   ' "injections": [], "key_points": [], "integration_notes": []}')
    settle, todo = fixqueue.queue(cfg)
    assert (d.name, "not published") in settle and not todo


def test_fix_takes_the_worst_problems_first(vault, monkeypatch):
    cfg = cfg_for(vault)
    root = cfg.paths.state / "gate2"
    for name, verdict in (("2026-01-01-backfill_a", "PARTIAL"), ("2026-01-02-backfill_b", "CONTRADICTED")):
        (root / name).mkdir(parents=True)
        (root / name / "review.json").write_text(
            '{"claims": [{"page": "wiki/sources/a.md", "claim": "c", "critical": true, "evidence": "", "raw_location": "",'
            f' "reason": "r", "verdict": "{verdict}"}}], "overwrites": [], "injections": [], "key_points": [], "integration_notes": []}}')
    cfg.data["pipeline"]["fix_max_problems"] = 1
    _, picked, n = fixqueue.pick(cfg)
    assert [d.name for d in picked] == ["2026-01-02-backfill_b"] and n == 1       # the newer but worse one first
