"""Running everything on its own: `memex schedule install|remove|list`, and `memex nightly`.

Jobs (times in [schedule]):
  reindex   every reindex_minutes            keep the search index current
  ingest    every ingest_minutes, and when raw/ changes (launchd and systemd watch the folder)
  nightly   daily at intake_at: intake, then the steps listed in schedule.nightly
  catchup   at login: run the nightly job if it was missed (machine off) in the last 20 hours
  lint      weekly at lint_at
  console   always on (restarted if it exits)

Backends: launchd (macOS), systemd user units (Linux), cron (anything else). Scheduled jobs start with a minimal
PATH, so the installer records where the tools were found (claude, codex, yt-dlp, ffmpeg, node, qmd, …) and gives
every job that PATH; without it they fail with "command not found" and nobody notices.
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

from memexos import events, i18n
from memexos.util import which

TOOLS = ("claude", "codex", "yt-dlp", "ffmpeg", "ffprobe", "whisper-cli", "pdftotext", "node", "qmd", "git", "twitter", "bili", "uv")
JOBS = ("reindex", "ingest", "nightly", "catchup", "lint", "console")


def backend_name(cfg) -> str:
    want = cfg.get("schedule.backend") or "auto"
    if want != "auto":
        return want
    if platform.system() == "Darwin" and which("launchctl"):
        return "launchd"
    if which("systemctl") and subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                                             stdin=subprocess.DEVNULL).returncode == 0:
        return "systemd"
    return "cron" if which("crontab") else "none"


def job_path(cfg) -> str:
    dirs = [str(Path(p).parent) for p in (which(t) for t in TOOLS) if p]
    dirs += [str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return ":".join(dict.fromkeys(dirs))


def memex_argv() -> list[str]:
    exe = shutil.which("memex")
    return [exe] if exe else [sys.executable, "-m", "memexos"]


def job_env(cfg) -> dict:
    env = {"PATH": job_path(cfg), "MEMEX_VAULT": str(cfg.vault)}
    if os.environ.get("MEMEX_CONFIG"):
        env["MEMEX_CONFIG"] = os.environ["MEMEX_CONFIG"]
    if os.environ.get("PYTHONPATH") and "-m" in memex_argv():
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    return env


def specs(cfg) -> dict[str, dict]:
    hh, mm = (cfg.get("schedule.intake_at") or "03:17").split(":")
    wd, lt = (cfg.get("schedule.lint_at") or "sun 21:07").split()
    lh, lm = lt.split(":")
    days = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
    return {
        "reindex": {"args": ["reindex"], "every": int(cfg.get("schedule.reindex_minutes") or 15)},
        "ingest": {"args": ["ingest"], "every": int(cfg.get("schedule.ingest_minutes") or 60), "watch": str(cfg.paths.raw)},
        "nightly": {"args": ["nightly"], "at": (int(hh), int(mm))},
        "catchup": {"args": ["nightly", "--if-stale", "20"], "at_login": True, "delay": 120},
        "lint": {"args": ["lint"], "weekly": (days.get(wd.lower()[:3], 0), int(lh), int(lm))},
        "console": {"args": ["console", "serve"], "keep_alive": True},
    }


# macOS keeps background jobs out of these folders unless the program has been given access (privacy settings,
# "Files and Folders" or "Full Disk Access"); a scheduled job reading a knowledge base there fails with
# "Operation not permitted" every time, and nobody sees it
PROTECTED = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")


def protected_folder(path: Path) -> str | None:
    """The macOS privacy-protected folder that path lies in (for example "Documents"), or None."""
    if platform.system() != "Darwin":
        return None
    p = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    for name in PROTECTED:
        root = home / name
        if p == root or root in p.parents:
            return "~/" + name
    return None


def label(cfg, job: str) -> str:
    from memexos.config import vault_id
    return f"{cfg.get('schedule.label_prefix') or 'io.memexos'}.{vault_id(cfg.vault)}.{job}"


# ---------------------------------------------------------------- launchd
def launchd_plist(cfg, job: str, spec: dict) -> str:
    argv = memex_argv() + spec["args"]
    if spec.get("delay"):
        argv = ["/bin/sh", "-c", f"sleep {spec['delay']}; exec " + " ".join(shlex.quote(a) for a in argv)]
    logs = cfg.paths.state / "jobs"
    trig = ""
    if spec.get("every"):
        trig += f"  <key>StartInterval</key><integer>{spec['every'] * 60}</integer>\n"
    if spec.get("watch"):
        trig += f"  <key>WatchPaths</key><array><string>{escape(spec['watch'])}</string></array>\n  <key>ThrottleInterval</key><integer>120</integer>\n"
    if spec.get("at"):
        trig += f"  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{spec['at'][0]}</integer><key>Minute</key><integer>{spec['at'][1]}</integer></dict>\n"
    if spec.get("weekly"):
        w, h, m = spec["weekly"]
        trig += f"  <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>{w}</integer><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>\n"
    if spec.get("keep_alive"):
        trig += "  <key>KeepAlive</key><true/>\n  <key>ThrottleInterval</key><integer>10</integer>\n"
    env = "".join(f"<key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in job_env(cfg).items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{escape(label(cfg, job))}</string>
  <key>ProgramArguments</key>
  <array>
{"".join(f"    <string>{escape(a)}</string>{chr(10)}" for a in argv)}  </array>
{trig}  <key>RunAtLoad</key><{"true" if spec.get("at_login") or spec.get("keep_alive") else "false"}/>
  <key>EnvironmentVariables</key><dict>{env}</dict>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>{escape(str(logs / (job + '.out')))}</string>
  <key>StandardErrorPath</key><string>{escape(str(logs / (job + '.err')))}</string>
</dict>
</plist>
"""


def launchd_install(cfg, jobs) -> list[str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    (cfg.paths.state / "jobs").mkdir(parents=True, exist_ok=True)
    uid, out = os.getuid(), []
    for job, spec in jobs.items():
        dest = agents / f"{label(cfg, job)}.plist"
        text = launchd_plist(cfg, job, spec)
        loaded = subprocess.run(["launchctl", "print", f"gui/{uid}/{label(cfg, job)}"], capture_output=True).returncode == 0
        if dest.exists() and dest.read_text() == text and loaded:
            out.append(f"= {label(cfg, job)}")        # unchanged: do not interrupt a running job
            continue
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label(cfg, job)}"], capture_output=True)
        dest.write_text(text)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True, text=True)
        out.append(("✓ " if r.returncode == 0 else "✗ ") + label(cfg, job) + ("" if r.returncode == 0 else f" ({r.stderr.strip()[-120:]})"))
    return out


def launchd_remove(cfg) -> list[str]:
    uid, out = os.getuid(), []
    for job in JOBS:
        dest = Path.home() / "Library" / "LaunchAgents" / f"{label(cfg, job)}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label(cfg, job)}"], capture_output=True)
        if dest.exists():
            dest.unlink()
            out.append(f"- {label(cfg, job)}")
    return out


# ---------------------------------------------------------------- systemd (user units)
def sd_quote(s: str) -> str:
    """systemd unit quoting: double quotes with backslash escapes, and % doubled (it starts a specifier)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def systemd_units(cfg, job: str, spec: dict) -> dict[str, str]:
    name = label(cfg, job)
    argv = " ".join(sd_quote(a) for a in memex_argv() + spec["args"])
    env = "\n".join(f"Environment={sd_quote(f'{k}={v}')}" for k, v in job_env(cfg).items())
    units = {}
    if spec.get("keep_alive"):
        units[f"{name}.service"] = f"[Unit]\nDescription=MemexOS {job}\n\n[Service]\nExecStart={argv}\n{env}\nRestart=always\nRestartSec=10\n\n[Install]\nWantedBy=default.target\n"
        return units
    pre = f"ExecStartPre=/bin/sleep {spec['delay']}\n" if spec.get("delay") else ""
    units[f"{name}.service"] = f"[Unit]\nDescription=MemexOS {job}\n\n[Service]\nType=oneshot\n{pre}ExecStart={argv}\n{env}\nNice=5\n"
    timer = ""
    if spec.get("every"):
        timer += f"OnBootSec=5min\nOnUnitActiveSec={spec['every']}min\n"
    if spec.get("at"):
        timer += f"OnCalendar=*-*-* {spec['at'][0]:02d}:{spec['at'][1]:02d}:00\nPersistent=true\n"
    if spec.get("weekly"):
        w, h, m = spec["weekly"]
        timer += f"OnCalendar={['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][w]} *-*-* {h:02d}:{m:02d}:00\nPersistent=true\n"
    if spec.get("at_login"):
        timer += "OnStartupSec=2min\n"
    units[f"{name}.timer"] = f"[Unit]\nDescription=MemexOS {job} schedule\n\n[Timer]\n{timer}\n[Install]\nWantedBy=timers.target\n"
    if spec.get("watch"):
        units[f"{name}.path"] = f"[Unit]\nDescription=MemexOS {job} on raw changes\n\n[Path]\nPathChanged={spec['watch'].replace('%', '%%')}\nUnit={name}.service\n\n[Install]\nWantedBy=default.target\n"
    return units


def systemd_install(cfg, jobs) -> list[str]:
    d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    out, enable = [], []
    for job, spec in jobs.items():
        for fname, text in systemd_units(cfg, job, spec).items():
            (d / fname).write_text(text)
            if not fname.endswith(".service") or spec.get("keep_alive"):
                enable.append(fname)
        out.append(f"✓ {label(cfg, job)}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", *enable], capture_output=True)
    return out


def systemd_remove(cfg) -> list[str]:
    d = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    out = []
    for p in d.glob(f"{label(cfg, '')}*"):
        subprocess.run(["systemctl", "--user", "disable", "--now", p.name], capture_output=True)
        p.unlink()
        out.append(f"- {p.name}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return out


# ---------------------------------------------------------------- cron
def cron_lines(cfg, jobs) -> list[str]:
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in job_env(cfg).items())
    argv = lambda a: " ".join(shlex.quote(x) for x in memex_argv() + a)  # noqa: E731
    tag = f"# memexos {label(cfg, '')}"
    out = []
    for job, spec in jobs.items():
        cmd = f"{env} {argv(spec['args'])} >> {shlex.quote(str(cfg.paths.state / 'jobs' / (job + '.out')))} 2>&1"
        if spec.get("every"):
            e = int(spec["every"])
            out.append(f"*/{e} * * * * {cmd} {tag}" if e < 60 else f"0 */{max(1, e // 60)} * * * {cmd} {tag}")
        if spec.get("at"):
            out.append(f"{spec['at'][1]} {spec['at'][0]} * * * {cmd} {tag}")
        if spec.get("weekly"):
            w, h, m = spec["weekly"]
            out.append(f"{m} {h} * * {w} {cmd} {tag}")
        if spec.get("at_login") or spec.get("keep_alive"):
            out.append(f"@reboot sleep {spec.get('delay') or 30}; {cmd} {tag}")
    return out


def cron_install(cfg, jobs) -> list[str]:
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    tag = f"# memexos {label(cfg, '')}"
    keep = [l for l in cur.splitlines() if not l.endswith(tag)]
    new = keep + cron_lines(cfg, jobs)
    subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, check=True)
    (cfg.paths.state / "jobs").mkdir(parents=True, exist_ok=True)
    return [f"✓ cron: {len(new) - len(keep)} lines"]


def cron_remove(cfg) -> list[str]:
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    tag = f"# memexos {label(cfg, '')}"
    keep = [l for l in cur.splitlines() if not l.endswith(tag)]
    subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True)
    return ["- cron lines removed"]


# ---------------------------------------------------------------- nightly
STEPS = {
    "visual": lambda cfg: __import__("memexos.capture.visual", fromlist=["x"]).backlog(cfg),
    "classify": lambda cfg: _classify_backlog(cfg),
    "catalog-sync": lambda cfg: __import__("memexos.catalog.classify", fromlist=["x"]).catalog_sync(cfg),
    "eval-cover": lambda cfg: _eval_cover(cfg),
    "usage-judge": lambda cfg: __import__("memexos.usage", fromlist=["x"]).judge_usage(cfg),
    "usage-verify": lambda cfg: __import__("memexos.usage", fromlist=["x"]).verify(cfg),
    "fix": lambda cfg: __import__("memexos.pipeline.compile", fromlist=["x"]).fix(cfg),
    "inbox-triage": lambda cfg: __import__("memexos.inbox", fromlist=["x"]).triage(cfg),
    "inbox-apply": lambda cfg: __import__("memexos.inbox", fromlist=["x"]).apply(cfg),
}


# the job name a nightly step's events carry (the timeline groups and colours work by job)
JOB_OF = {"visual": "visual", "classify": "classify", "catalog-sync": "classify", "eval-cover": "eval", "usage-judge": "usage",
          "usage-verify": "usage", "fix": "fix", "inbox-triage": "inbox", "inbox-apply": "inbox"}


def _classify_backlog(cfg):
    from memexos.catalog import classify as C
    rels = C.todo(cfg, C.all_raws(cfg), C.load(cfg))
    C.classify(cfg, rels)
    return C.check(cfg)


def _eval_cover(cfg):
    from memexos.evals import retrieval
    return retrieval.cover(cfg)


def cmd_nightly(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex nightly")
    ap.add_argument("--if-stale", type=float, default=0)
    ap.add_argument("--skip-intake", action="store_true")
    a = ap.parse_args(argv)
    cfg = loader()
    if a.if_stale:
        last = next((e for e in reversed(list(events.read(cfg.paths.state, since=time.time() - a.if_stale * 3600,
                                                              types=("nightly.done",))))), None)
        if last:
            return 0
    from memexos.pipeline.compile import ingest
    with events.run(cfg.paths.state, "nightly"):
        if not a.skip_intake:
            from memexos.intake import admit
            ns = argparse.Namespace(source="all", since_days=float(cfg.get("intake.since_days") or 30), limit=None, keys=None, if_stale=0)
            try:
                with events.job("intake"):
                    admit.run(cfg, ns)
            except Exception as e:  # noqa: BLE001 — one failing step must not stop the night
                events.emit(cfg.paths.state, "nightly.step_failed", step="intake", why=repr(e)[:300])
        for step in cfg.get("schedule.nightly") or []:
            fn = STEPS.get(step)
            if not fn:
                events.emit(cfg.paths.state, "nightly.step_failed", step=step, why="unknown step")
                continue
            try:
                with events.job(JOB_OF.get(step, step)):
                    fn(cfg)
                events.emit(cfg.paths.state, "nightly.step", step=step)
            except Exception as e:  # noqa: BLE001
                events.emit(cfg.paths.state, "nightly.step_failed", step=step, why=repr(e)[:300])
        try:
            with events.job("ingest"):
                ingest(cfg)              # compile whatever the night collected
        except Exception as e:  # noqa: BLE001
            events.emit(cfg.paths.state, "nightly.step_failed", step="ingest", why=repr(e)[:300])
        events.emit(cfg.paths.state, "nightly.done")
    return 0


def cmd_schedule(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex schedule")
    ap.add_argument("action", nargs="?", default="list", choices=["install", "remove", "list", "show"])
    ap.add_argument("--only", nargs="*", choices=JOBS)
    ap.add_argument("--force", action="store_true", help="install even if macOS will keep the jobs out of the knowledge base")
    a = ap.parse_args(argv)
    cfg = loader()
    be = backend_name(cfg)
    jobs = {k: v for k, v in specs(cfg).items() if not a.only or k in a.only}
    if a.action in ("list", "show"):
        print(i18n.t("schedule.backend", backend=be))
        for job, spec in jobs.items():
            print(f"  {label(cfg, job)}  memex {' '.join(spec['args'])}  {json_brief(spec)}")
        if a.action == "show" and be == "launchd":
            for job, spec in jobs.items():
                print(launchd_plist(cfg, job, spec))
        return 0
    if be == "none":
        print(i18n.t("schedule.none"))
        return 1
    blocked = protected_folder(cfg.vault)
    if a.action == "install" and blocked and not a.force:
        print(i18n.t("schedule.protected", folder=blocked, vault=cfg.vault), file=sys.stderr)
        return 1
    fn = {"launchd": (launchd_install, launchd_remove), "systemd": (systemd_install, systemd_remove), "cron": (cron_install, cron_remove)}[be]
    for line in (fn[0](cfg, jobs) if a.action == "install" else fn[1](cfg)):
        print(line)
    return 0


def json_brief(spec: dict) -> str:
    parts = []
    if spec.get("every"):
        parts.append(f"every {spec['every']} min")
    if spec.get("watch"):
        parts.append("on raw/ changes")
    if spec.get("at"):
        parts.append(f"daily {spec['at'][0]:02d}:{spec['at'][1]:02d}")
    if spec.get("weekly"):
        parts.append(f"weekly day {spec['weekly'][0]} {spec['weekly'][1]:02d}:{spec['weekly'][2]:02d}")
    if spec.get("at_login"):
        parts.append("at login")
    if spec.get("keep_alive"):
        parts.append("always on")
    return ", ".join(parts)
