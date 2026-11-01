"""`memex status [-w [seconds]]`: what is running, what needs attention, what was done today, what waits for you.

Everything comes from the structured event log, the locks and the ledgers; nothing parses human-readable logs.
The console uses snapshot() for the same data.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from memexos import backends, events, i18n, lock
from memexos.pipeline.stage import ledger, pending_raw

TTY = sys.stdout.isatty()


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


def dur(s: float) -> str:
    s = int(s)
    return i18n.t("status.seconds", n=s) if s < 90 else i18n.t("status.minutes", n=round(s / 60)) if s < 5400 else i18n.t("status.hours", n=f"{s / 3600:.1f}")


def running_runs(cfg) -> list[dict]:
    """Runs that started and have not ended, whose process still holds a lock or started recently."""
    starts, ends = {}, set()
    for e in events.read(cfg.paths.state, since=time.time() - 86400 * 2, types=("run.",)):
        if e["type"] == "run.start":
            starts[e["run"]] = e
        else:
            ends.add(e["run"])
    holders = {name: lock.holder(cfg.paths.locks, name) for name in ("write", "intake", "visual", "classify", "reindex")}
    live = [e for r, e in starts.items() if r not in ends]
    return [{"run": e["run"], "job": e["job"], "since": events._epoch(e["ts"]), "locks": [n for n, p in holders.items() if p]} for e in live
            if any(holders.values()) or time.time() - events._epoch(e["ts"]) < 3600]


def snapshot(cfg) -> dict:
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    today = [e for e in events.read(cfg.paths.state, since=time.mktime(time.strptime(day, "%Y-%m-%d")))]
    doing, alerts, warn, done = [], [], [], []
    for r in running_runs(cfg):
        last = next((e for e in reversed(today) if e.get("run") == r["run"]), None)
        doing.append(i18n.t("status.running", job=r["job"], since=dur(now - r["since"]), last=events.render(last) if last else "-"))
    pend = pending_raw(cfg.vault)
    if pend:
        doing.append(i18n.t("status.pending", n=len(pend), batch=cfg.get("pipeline.ingest_batch") or 3))

    if (cfg.paths.state / "PAUSE").exists():
        alerts.append(i18n.t("status.paused", path=cfg.paths.state / "PAUSE"))
    for name, s in backends.status(cfg).items():
        if s["exhausted"]:
            warn.append(i18n.t("status.quota", backend=name, at=s["exhausted"].get("at_str", "")))
    try:
        g = json.loads((cfg.paths.state / "gate2-unavailable.json").read_text())
        until = g.get("until") or ((cfg.paths.state / "gate2-unavailable.json").stat().st_mtime + 1800)
        if now < until:
            alerts.append(i18n.t("status.review_down", why=g.get("reason", "")[:100], until=time.strftime("%H:%M", time.localtime(until))))
    except (OSError, ValueError):
        pass
    held = ledger(cfg.vault, "held.txt")
    if held:
        warn.append(i18n.t("status.held", n=len(held)))
    from memexos.capture import cookies
    for site in cookies.SITES:
        p = cookies.path(cfg, site)
        if cookies.browser(cfg, site) and p.exists() and now - p.stat().st_mtime > 14 * 86400:
            warn.append(i18n.t("status.cookies_old", site=site, days=int((now - p.stat().st_mtime) / 86400)))
    fails = [e for e in today if e["type"] in ("ingest.failed", "intake.channel_failed", "capture.failed", "classify.failed", "visual.failed")]
    if fails:
        warn.append(i18n.t("status.failures", n=len(fails), last=events.render(fails[-1])[:160]))

    pubs = [e for e in today if e["type"] == "publish"]
    reviews = [e for e in today if e["type"] == "gate2.result"]
    admitted = sum(e["data"].get("admit", 0) for e in today if e["type"] == "intake.batch")
    captured = sum(1 for e in today if e["type"] in ("capture.done", "capture.video"))
    cost = sum(e["data"].get("cost_usd") or 0 for e in today if e["type"] == "model.call")
    done.append(i18n.t("status.published", n=len(pubs)))
    if reviews:
        done.append(i18n.t("status.reviews", n=len(reviews), passed=sum(1 for e in reviews if e["data"].get("passed")),
                           failed=sum(1 for e in reviews if e["data"].get("code") == 1), incomplete=sum(1 for e in reviews if e["data"].get("code") == 2)))
    if admitted or captured:
        done.append(i18n.t("status.collected", admitted=admitted, captured=captured))
    done.append(i18n.t("status.cost", usd=f"{cost:.2f}"))

    try:
        from memexos import inbox
        items = inbox.merged(cfg)
        n = lambda f: sum(1 for x in items if f(x))  # noqa: E731
        box = {"decide": n(lambda x: x["kind"] == "decide" and x["status"] == "open" and not x.get("dup_of")),
               "waiting": n(lambda x: x["status"] in ("decided", "queued")), "agent": n(lambda x: x["kind"] == "agent" and x["status"] == "open"),
               "untriaged": n(lambda x: not x["kind"])}
    except Exception:  # noqa: BLE001
        box = {}
    state = i18n.t("status.state_busy") if doing and running_runs(cfg) else i18n.t("status.state_pending") if pend else i18n.t("status.state_idle")
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "state": state, "doing": doing, "alerts": alerts, "warn": warn, "done": done,
            "inbox": box, "pending": pend, "cost_today": round(cost, 2), "paused": (cfg.paths.state / "PAUSE").exists()}


def render(cfg) -> str:
    s = snapshot(cfg)
    out = [c("1", i18n.t("status.title", time=s["time"])) + f"  {s['state']}", ""]
    out += [c("1;36", "▶ " + i18n.t("status.doing"))] + [f"  {l}" for l in s["doing"] or [i18n.t("status.nothing_running")]] + [""]
    if s["alerts"] or s["warn"]:
        out += [c("1;33", "⚠ " + i18n.t("status.attention"))] + [f"  {c('31', '●')} {a}" for a in s["alerts"]] + [f"  ● {w}" for w in s["warn"]] + [""]
    out += [c("1;32", "✓ " + i18n.t("status.today"))] + [f"  {l}" for l in s["done"]] + [""]
    if s["inbox"]:
        out += [c("1;35", "📥 " + i18n.t("status.inbox")), "  " + i18n.t("status.inbox_line", **s["inbox"])]
    out += ["", c("2", "  " + i18n.t("status.log", path=cfg.paths.log))]
    return "\n".join(out)


def cmd_status(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex status")
    ap.add_argument("-w", "--watch", nargs="?", const=10, type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = loader()
    if a.json:
        print(json.dumps(snapshot(cfg), ensure_ascii=False, indent=1))
        return 0
    if a.watch:
        try:
            while True:
                r = subprocess.run([sys.executable, "-m", "memexos", "status"], capture_output=True, text=True,
                                   env={**os.environ, "MEMEX_VAULT": str(cfg.vault)})
                sys.stdout.write("\033[H\033[2J" + (r.stdout or r.stderr) + "\n")
                sys.stdout.flush()
                time.sleep(a.watch)
        except KeyboardInterrupt:
            return 0
    print(render(cfg))
    return 0
