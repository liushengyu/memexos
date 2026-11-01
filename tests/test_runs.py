"""The run timeline is rebuilt from events alone."""

import json
import os
import time

from memexos import events, runs


def write(state, evs):
    state.mkdir(parents=True, exist_ok=True)
    t0 = time.time() - 3600
    with open(state / "events.jsonl", "w") as f:
        for i, (run, job, typ, data) in enumerate(evs):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t0 + i * 10))
            f.write(json.dumps({"ts": ts, "run": run, "job": job, "type": typ, "data": data}) + "\n")


def test_batches_steps_and_jobs(tmp_path):
    st = tmp_path / "state"
    me = os.getpid()
    write(st, [
        ("n1", "nightly", "run.start", {"pid": me}),
        ("n1", "intake", "step.start", {"name": "collect"}),
        ("n1", "intake", "step.end", {"name": "collect", "seconds": 10}),
        ("n1", "intake", "model.call", {"cost_usd": 0.5}),
        ("n1", "ingest", "ingest.start", {"files": ["raw/a.md"], "remaining": 1}),
        ("n1", "ingest", "stage.open", {"branch": "staging/1"}),
        ("n1", "ingest", "step.start", {"name": "write"}),
        ("n1", "ingest", "search", {"query": "q"}),
        ("n1", "ingest", "model.call", {"cost_usd": 1.25}),
        ("n1", "ingest", "step.end", {"name": "write", "seconds": 20}),
        ("n1", "ingest", "gate1.result", {"passed": True, "errors": 0, "warnings": 0, "round": 0}),
        ("n1", "ingest", "step.start", {"name": "gate2", "mode": "ingest"}),
        ("n1", "ingest", "step.end", {"name": "gate2", "seconds": 5, "result": "failed"}),
        ("n1", "ingest", "gate2.result", {"passed": False, "code": 1, "mode": "ingest"}),
        ("n1", "ingest", "hold", {"branch": "staging/1", "reason": "still failing", "raws": ["raw/a.md"]}),
        ("n1", "nightly", "run.end", {"seconds": 140, "status": "ok"}),
    ])
    units = runs.timeline(st, 0)
    intake, batch = units
    assert intake["job"] == "intake" and intake["lane"] == "collect" and intake["status"] == "done" and intake["cost"] == 0.5
    assert batch["kind"] == "batch" and batch["status"] == "held" and batch["note"] == "still failing"
    assert batch["files"] == ["raw/a.md"] and batch["branch"] == "staging/1" and batch["cost"] == 1.25
    assert [s["step"] for s in batch["segs"]] == ["write", "gate2"]
    assert batch["segs"][1]["data"]["result"] == "failed"
    assert [m["type"] for m in batch["marks"]] == ["gate1.result", "hold"]
    assert batch["marks"][0]["ok"] is True


def test_dead_process_is_interrupted_live_one_is_running(tmp_path):
    st = tmp_path / "state"
    write(st, [
        ("dead", "ingest", "run.start", {"pid": 999999}),
        ("dead", "ingest", "stage.open", {"branch": "staging/2"}),
        ("dead", "ingest", "step.start", {"name": "write"}),
        ("live", "intake", "run.start", {"pid": os.getpid()}),
        ("live", "intake", "step.start", {"name": "admit", "channel": "rss"}),
    ])
    by = {u["run"]: u for u in runs.timeline(st, 0)}
    assert by["dead"]["status"] == "interrupted"
    assert by["live"]["status"] == "running" and by["live"]["active"] == ["admit"]
    assert runs.active_stages(st) == {"intake": ["intake"]}


def test_step_context_records_result_and_errors(tmp_path):
    st = tmp_path / "state"
    events.new_run("test")
    with events.step(st, "gate2", mode="fix") as info:
        info["result"] = "passed"
    try:
        with events.step(st, "write"):
            raise ValueError("boom")
    except ValueError:
        pass
    evs = list(events.read(st))
    ends = [e["data"] for e in evs if e["type"] == "step.end"]
    assert ends[0]["result"] == "passed" and ends[1]["result"] == "error" and "boom" in ends[1]["why"]


def test_render_picks_pass_and_fail_variants():
    ev = {"type": "gate1.result", "job": "ingest", "data": {"passed": False, "errors": 2, "warnings": 0, "round": 1}}
    assert "2 problem" in events.render(ev)
    ev["data"]["passed"] = True
    assert "passed" in events.render(ev, job=False) and not events.render(ev, job=False).startswith("ingest")
    ev = {"type": "ingest.start", "job": "ingest", "data": {"files": ["a", "b"], "remaining": 5}}
    assert "2 raw" in events.render(ev)
