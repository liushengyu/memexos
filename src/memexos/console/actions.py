"""The few things the console can change: pause or resume compiling, start a job now, record inbox decisions,
and discard a held batch. Each one is reversible or only starts work that goes through the normal checks."""

from __future__ import annotations

import os
import re
import subprocess
import time

from memexos import i18n, lock, runs
from memexos.util import self_cmd

JOBS = {"ingest": ["ingest"], "intake": ["intake"], "lint": ["lint"], "fix": ["fix"], "nightly": ["nightly"],
        "inbox-apply": ["inbox", "apply"]}
BRANCH = re.compile(r"staging/\d{8}-\d{6}")


def start(cfg, job: str) -> tuple[bool, str]:
    """Start a job in the background, detached from the console (it keeps running if the console stops)."""
    name = JOBS[job][0]
    if any(u["job"] == name for u in runs.running(cfg.paths.state)):
        return False, i18n.t("console.msg.already_running", job=i18n.t(f"job.{name}"))
    logs = cfg.paths.state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / f"console-{job}.log", "ab") as out:
        subprocess.Popen(self_cmd() + JOBS[job], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, "MEMEX_VAULT": str(cfg.vault), "MEMEX_RUN": ""})
    return True, i18n.t("console.msg.started", job=i18n.t(f"job.{name}"))


def login(cfg, site: str) -> tuple[bool, str]:
    """Open the login window for a collector site on this computer (`memex login <site>`, detached)."""
    from memexos.capture import cookies
    if site not in cookies.SITES:
        return False, i18n.t("console.msg.unknown")
    logs = cfg.paths.state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / f"login-{site}.log", "ab") as out:
        subprocess.Popen(self_cmd() + ["login", site], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, "MEMEX_VAULT": str(cfg.vault)})
    return True, i18n.t("console.msg.login_started", site=cookies.SITES[site].name)


def abandon(cfg, branch: str) -> tuple[bool, str]:
    """Discard a held batch and queue its raw files again."""
    from memexos import events
    from memexos.pipeline.stage import git, ledger
    if not BRANCH.fullmatch(branch):
        return False, i18n.t("console.msg.bad_branch")
    live = re.findall(r"^branch refs/heads/(\S+)$", git(cfg.vault, "worktree", "list", "--porcelain", check=False), re.M)
    if branch in live:
        return False, i18n.t("console.msg.branch_busy", branch=branch)
    raws = next((e["data"].get("raws") or [] for e in reversed(list(events.read(cfg.paths.state, types=("hold",))))
                 if e["data"].get("branch") == branch), [])
    try:
        with lock.held(cfg.paths.locks, "write", wait=30):
            git(cfg.vault, "branch", "-D", branch, check=False)
            held = ledger(cfg.vault, "held.txt")
            keep = sorted(held - set(raws), key=lambda s: s.encode("utf-8"))
            if keep != sorted(held, key=lambda s: s.encode("utf-8")):
                (cfg.vault / ".memex" / "held.txt").write_text("".join(f"{x}\n" for x in keep), encoding="utf-8")
                git(cfg.vault, "add", ".memex/held.txt")
                git(cfg.vault, "commit", "-q", "-m", f"console: discard {branch}, queue its raw files again", check=False)
    except lock.Busy:
        return False, i18n.t("console.msg.busy")
    return True, i18n.t("console.msg.abandoned", branch=branch, n=len(raws))


def set_language(cfg, lang: str) -> tuple[bool, str]:
    """Persist the console's language and apply it to this process.

    Writes where `memex setup` writes (the user config) with the same guarantees:
    mode 0600, atomic replace, and a parse before the replace so a broken
    settings file is never left behind.
    """
    from memexos import config as config_mod

    lang = (lang or "").strip()
    if lang not in i18n.available():
        return False, i18n.t("console.msg.unknown")
    ucfg = config_mod.user_config_path()
    try:
        text = ucfg.read_text(encoding="utf-8") if ucfg.exists() else config_mod.default_config_text()
    except OSError:
        return False, i18n.t("console.msg.cannot_save")
    if re.search(r"(?m)^language\s*=", text):
        text = re.sub(r'(?m)^language\s*=.*$', f'language = "{lang}"', text, count=1)
    else:
        text = f'language = "{lang}"\n' + text
    ucfg.parent.mkdir(parents=True, exist_ok=True)
    tmp = ucfg.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    import tomllib
    tomllib.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(ucfg)
    i18n.set_language(lang)
    return True, i18n.t("console.msg.language_set", lang=lang)


def act(cfg, body: dict) -> tuple[bool, str]:
    a = body.get("action")
    pause = cfg.paths.state / "PAUSE"
    if a == "set-language":
        return set_language(cfg, str(body.get("lang", "")))
    if a == "pause":
        pause.parent.mkdir(parents=True, exist_ok=True)
        pause.write_text(f"paused from the console at {time.strftime('%F %T')}\n", encoding="utf-8")
        return True, i18n.t("console.msg.paused")
    if a == "resume":
        pause.unlink(missing_ok=True)
        return True, i18n.t("console.msg.resumed")
    if a in JOBS:
        return start(cfg, a)
    if a in ("inbox-decide", "inbox-undo", "inbox-dismiss"):
        from memexos import inbox
        iid = str(body.get("id", ""))
        if a == "inbox-undo":
            return inbox.undo(cfg, iid)
        ch = body.get("choice")
        return inbox.decide(cfg, iid, None if ch is None else int(ch), str(body.get("text") or "")[:4000], a == "inbox-dismiss")
    if a == "inbox-accept":                 # a whole group as recommended (the owner clicked that group)
        from memexos import inbox
        want, n = set(body.get("ids") or []), 0
        for it in inbox.merged(cfg):
            t = it["triage"] or {}
            if it["id"] in want and it["status"] == "open" and 0 <= t.get("recommend", -1) < len(t.get("options") or []):
                ok, _ = inbox.decide(cfg, it["id"], t["recommend"])
                n += ok
        return True, i18n.t("console.msg.accepted", n=n)
    if a == "abandon":
        return abandon(cfg, str(body.get("branch", "")))
    if a == "login":
        return login(cfg, str(body.get("site", "")))
    return False, i18n.t("console.msg.unknown")
