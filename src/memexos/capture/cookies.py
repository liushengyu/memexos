"""Logins for the sites the optional plugins collect from (X, Bilibili, YouTube), and the cookie files their tools get.

[plugins.<site>] login says where a site's login comes from:

  "memex"   (the default) MemexOS's own browser profile (capture/browser.py). `memex login <site>` opens a window
            once; after that MemexOS renews the login by itself, from scheduled jobs too, and agents can run
            `memex cookies refresh` whenever they need to.
  "chrome", "firefox", "edge", "brave", "safari", …
            copy the login from that everyday browser (needs yt-dlp). macOS does not let scheduled jobs read a
            browser's data, so copying works from a terminal only; scheduled jobs use the last copy.
  ""        no login.

Only the site's own cookies are written, to ~/.config/memexos/cookies/<site>.txt (mode 600). When a site has ended
the session, MemexOS says so loudly: an event, an alert in `memex status` and the console, and one desktop
notification. `memex login <site>` (an agent may run it) then opens the window for the person to log in again.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from memexos import i18n
from memexos.capture.redact import cookie_dir_of
from memexos.util import atomic_write, script_python, which


@dataclass(frozen=True)
class Site:
    name: str
    domains: tuple[str, ...]
    login: tuple[str, ...]          # any of these cookies present = logged in
    home: str                       # visited to let the site renew its cookies
    login_url: str


SITES = {
    "x": Site("X", ("x.com", "twitter.com"), ("auth_token",), "https://x.com/home", "https://x.com/i/flow/login"),
    "bilibili": Site("Bilibili", ("bilibili.com",), ("SESSDATA",), "https://www.bilibili.com/", "https://passport.bilibili.com/login"),
    "youtube": Site("YouTube", ("youtube.com",), ("__Secure-3PSID", "LOGIN_INFO"), "https://www.youtube.com/",
                    "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F"),
}
MAX_AGE = 6 * 3600          # a copy older than this is renewed before use
LOGIN_TIMEOUT = 900         # seconds the login window waits for the person


class LoginNeeded(RuntimeError):
    """The site has no valid login in the profile: someone has to log in (`memex login <site>`)."""


# ---------------------------------------------------------------- settings and state
def source(cfg, site: str) -> str:
    v = cfg.get(f"plugins.{site}.login")
    if v is None:                                   # the setting's earlier name
        v = cfg.get(f"plugins.{site}.browser")
    return str(v or "").strip()


def browser(cfg, site: str) -> str:
    """Kept for callers of the earlier API: the configured login source."""
    return source(cfg, site)


def path(cfg, site: str) -> Path:
    return cookie_dir_of(cfg) / f"{site}.txt"


def _state_file(cfg) -> Path:
    return cfg.paths.state / "logins.json"


def states(cfg) -> dict:
    try:
        return json.loads(_state_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _set_state(cfg, site: str, ok: bool, why: str = "", expires: float | None = None) -> None:
    from memexos import events
    st = states(cfg)
    old = st.get(site) or {}
    new = {"ok": ok, "at": time.time(), "why": why[:300], "expires": expires,
           "notified": old.get("notified") if not ok and not old.get("ok", True) else False}
    if not ok and not new["notified"]:
        notify(i18n.t("cookies.notify_title"), i18n.t("cookies.notify_text", site=SITES[site].name, cmd=f"memex login {site}"))
        new["notified"] = True
    st[site] = new
    try:
        _state_file(cfg).parent.mkdir(parents=True, exist_ok=True)
        atomic_write(_state_file(cfg), json.dumps(st, indent=1))
        if ok != old.get("ok"):
            events.emit(cfg.paths.state, "login.ok" if ok else "login.expired", site=site, why=why[:200])
    except OSError:
        pass                                        # a read-only sandbox: the next writable run records it


def mark_expired(cfg, site: str, why: str) -> None:
    """For collectors: the site rejected the login (for example an authentication error from its API)."""
    _set_state(cfg, site, False, why)


def notify(title: str, text: str) -> None:
    """One desktop notification, best effort (macOS Notification Center, notify-send on Linux)."""
    if os.environ.get("MEMEX_NO_NOTIFY"):
        return
    try:
        if sys.platform == "darwin":
            script = f"display notification {json.dumps(text)} with title {json.dumps(title)}"
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
        elif which("notify-send"):
            subprocess.run(["notify-send", title, text], capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- cookie files
def _mine(site: str, rows: list[dict]) -> list[dict]:
    doms = SITES[site].domains
    return [c for c in rows if any(c["domain"].lstrip(".") == d or c["domain"].lstrip(".").endswith("." + d) for d in doms)]


def login_expiry(site: str, rows: list[dict]) -> float | None:
    """When the login in these cookies ends (seconds since the epoch; 0 for a cookie that lasts the session), or None
    when there is no valid login."""
    now = time.time()
    found = [float(c.get("expires") or 0) for c in rows if c["name"] in SITES[site].login and c.get("value")]
    valid = [e for e in found if e <= 0 or e > now]
    return min(valid) if valid else None


def _write(site: str, rows: list[dict], dest: Path, origin: str) -> int:
    lines = ["# Netscape HTTP Cookie File", f"# memexos: {site} login cookies from {origin}; renewed automatically"]
    for c in rows:
        exp = float(c.get("expires") or 0)
        lines.append("\t".join([c["domain"], "TRUE" if c["domain"].startswith(".") else "FALSE", c.get("path") or "/",
                                "TRUE" if c.get("secure") else "FALSE", str(int(exp)) if exp > 0 else "0", c["name"], c.get("value") or ""]))
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(dest, "\n".join(lines) + "\n", mode=0o600)
    return len(rows)


def read_file(p: Path) -> list[dict]:
    """A Netscape cookie file as rows (domain, path, secure, expires, name, value). "#HttpOnly_" prefixes are kept off."""
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        if len(f) >= 7:
            out.append({"domain": f[0], "path": f[2], "secure": f[3].upper() == "TRUE", "expires": float(f[4] or 0),
                        "name": f[5], "value": f[6]})
    return out


# ---------------------------------------------------------------- MemexOS's browser profile
def _proxy(cfg, site: str) -> str | None:
    """The route the browser takes to the site: the plugin's proxy, else [fetch] proxy, else the environment's."""
    v = cfg.get(f"plugins.{site}.proxy")
    v = str((cfg.get("fetch.proxy") if v in (None, "") else v) or "").strip()
    p = None if not v else "" if v == "direct" else v
    if p is None:
        p = next((os.environ[k] for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy") if os.environ.get(k)), None)
    if p and urllib.parse.urlsplit(p).password:
        raise RuntimeError(i18n.t("cookies.proxy_password", site=site))
    return p


def _profile_lock(wait: float):
    from memexos import lock
    from memexos.capture import browser as B
    return lock.held(B.profile_dir().parent, "browser-profile", wait=wait, poll=2)


def _exe(cfg) -> str:
    from memexos.capture import browser as B
    exe = B.find(cfg)
    if not exe:
        raise RuntimeError(i18n.t("cookies.no_browser"))
    return exe


def renew(cfg, site: str, visit: bool = True) -> int:
    """Open the site in MemexOS's profile without a window (so it renews its cookies), check the login and write the
    cookie file. Raises LoginNeeded when the site is no longer logged in."""
    from memexos.capture import browser as B
    exe = _exe(cfg)
    with _profile_lock(wait=180):
        ua = B.user_agent(exe)
        with B.Browser(exe, headless=True, proxy=_proxy(cfg, site), user_agent=ua) as b:
            if visit:
                b.visit(SITES[site].home)
            rows = _mine(site, b.cookies())
    exp = login_expiry(site, rows)
    if exp is None:
        why = i18n.t("cookies.session_ended", site=SITES[site].name)
        _set_state(cfg, site, False, why)
        raise LoginNeeded(why)
    n = _write(site, rows, path(cfg, site), "the MemexOS browser profile")
    _set_state(cfg, site, True, expires=exp or None)
    return n


def login(cfg, site: str, timeout: float = LOGIN_TIMEOUT, headless: bool = False, cookie_rows: list[dict] | None = None,
          out=print) -> int:
    """Log in to a site in MemexOS's profile. With a window (the default) the person logs in; given cookie rows (from
    --cookies or --from-browser) they are put into the profile instead. Either way the login then renews itself."""
    from memexos.capture import browser as B
    exe = _exe(cfg)
    s = SITES[site]
    if cookie_rows is None and not headless and not B.can_show_window():
        raise RuntimeError(i18n.t("cookies.no_window", site=site))
    with _profile_lock(wait=30):
        if cookie_rows is not None:
            mine = _mine(site, cookie_rows)
            if login_expiry(site, mine) is None:
                raise LoginNeeded(i18n.t("cookies.no_login_in_copy", site=s.name))
            with B.Browser(exe, headless=True, proxy=_proxy(cfg, site)) as b:
                b.call("Storage.setCookies", cookies=[_cdp_cookie(c) for c in mine])
        else:
            out(i18n.t("cookies.window_opened", site=s.name, minutes=int(timeout / 60)))
            with B.Browser(exe, headless=headless, url=s.login_url, proxy=_proxy(cfg, site)) as b:
                deadline = time.time() + timeout
                while True:
                    if not b.alive():
                        raise LoginNeeded(i18n.t("cookies.window_closed", site=s.name))
                    try:
                        rows = _mine(site, b.cookies())
                    except B.BrowserError:
                        raise LoginNeeded(i18n.t("cookies.window_closed", site=s.name)) from None
                    if login_expiry(site, rows) is not None:
                        time.sleep(3)                       # the site sets its other cookies right after the login one
                        break
                    if time.time() > deadline:
                        raise LoginNeeded(i18n.t("cookies.login_timeout", site=s.name, minutes=int(timeout / 60)))
                    time.sleep(2)
    return renew(cfg, site, visit=cookie_rows is not None)


def _cdp_cookie(c: dict) -> dict:
    d = {"name": c["name"], "value": c.get("value") or "", "domain": c["domain"], "path": c.get("path") or "/",
         "secure": bool(c.get("secure"))}
    if float(c.get("expires") or 0) > 0:
        d["expires"] = float(c["expires"])
    return d


# ---------------------------------------------------------------- an everyday browser (yt-dlp)
def copy_from_browser(site: str, browser_name: str) -> list[dict]:
    """The site's cookies from an everyday browser, read with yt-dlp's browser-cookie support."""
    try:
        from yt_dlp.cookies import extract_cookies_from_browser  # noqa: F401
        return _extract(site, browser_name)
    except ImportError:
        pass
    py = script_python(which("yt-dlp"))
    if not py:
        raise RuntimeError(i18n.t("cookies.no_ytdlp"))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")]))}
    r = subprocess.run([py, "-m", "memexos.capture.cookies", "_extract", site, browser_name], capture_output=True, text=True,
                       env=env, stdin=subprocess.DEVNULL, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "yt-dlp could not read the browser")
    return json.loads(r.stdout or "[]")


def _extract(site: str, browser_name: str) -> list[dict]:
    from yt_dlp.cookies import extract_cookies_from_browser

    class Quiet:
        def debug(self, *a, **k):
            pass
        info = warning = debug

        def error(self, msg, *a, **k):
            print(msg, file=sys.stderr)
    jar = extract_cookies_from_browser(browser_name, logger=Quiet())
    rows = [{"domain": c.domain, "path": c.path or "/", "secure": bool(c.secure), "expires": float(c.expires or 0),
             "name": c.name, "value": c.value or ""} for c in jar]
    return _mine(site, rows)


def export(cfg, site: str) -> int:
    """Write the site's cookie file now from wherever its login comes from. Returns the number of cookies."""
    src = source(cfg, site)
    if not src:
        raise RuntimeError(i18n.t("cookies.no_source", site=site))
    if src == "memex":
        return renew(cfg, site)
    rows = copy_from_browser(site, src)
    if login_expiry(site, rows) is None:
        why = i18n.t("cookies.not_logged_in", site=SITES[site].name, browser=src)
        _set_state(cfg, site, False, why)
        raise LoginNeeded(why)
    n = _write(site, rows, path(cfg, site), src)
    _set_state(cfg, site, True, expires=login_expiry(site, rows) or None)
    return n


# ---------------------------------------------------------------- for collectors and tools
def file_for(cfg, site: str) -> Path | None:
    """The cookie file to give a tool, renewed first when older than MAX_AGE. None when the site has no login set up.
    Never opens a window and never fails: a problem is recorded (and shown) and the last file is used."""
    if site not in SITES or not source(cfg, site):
        return None
    p = path(cfg, site)
    if not p.exists():
        return None                 # never logged in: nothing to renew (status shows enabled plugins without a login)
    if time.time() - p.stat().st_mtime >= MAX_AGE:
        try:
            export(cfg, site)
        except LoginNeeded:
            pass                    # recorded and announced by export / renew
        except Exception:  # noqa: BLE001 — e.g. no access to an everyday browser from a scheduled job
            pass
    return p if p.exists() else None


def needs_login(cfg) -> list[str]:
    """Sites whose plugin is on but whose login is missing or has ended."""
    out = []
    st = states(cfg)
    for site in SITES:
        if not cfg.get(f"plugins.{site}.enabled") or not source(cfg, site):
            continue
        if (st.get(site) or {}).get("ok") is False or not path(cfg, site).exists():
            out.append(site)
    return out


def status(cfg, site: str) -> str:
    src = source(cfg, site) or "-"
    p = path(cfg, site)
    st = states(cfg).get(site) or {}
    if not p.exists():
        return i18n.t("cookies.none", site=site, source=src)
    rows = read_file(p)
    exp = login_expiry(site, rows)
    if st.get("ok") is False or exp is None:
        return i18n.t("cookies.status_ended", site=site, source=src, why=st.get("why") or i18n.t("cookies.no_login_cookie"))
    days = "" if not exp else i18n.t("cookies.days_left", days=max(0, int((exp - time.time()) / 86400)))
    return i18n.t("cookies.status", site=site, source=src, n=len(rows), minutes=int((time.time() - p.stat().st_mtime) / 60), days=days)


# ---------------------------------------------------------------- commands
def cmd_cookies(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex cookies", description=i18n.t("cookies.help"))
    ap.add_argument("action", nargs="?", default="status", choices=["status", "refresh"])
    ap.add_argument("site", nargs="?", choices=list(SITES))
    a = ap.parse_args(argv)
    cfg = loader()
    rc = 0
    for site in [a.site] if a.site else list(SITES):
        if not source(cfg, site):
            if a.site:
                print(i18n.t("cookies.no_source", site=site))
                rc = 1
            continue
        if a.action == "refresh" and (path(cfg, site).exists() or a.site):
            try:
                print(i18n.t("cookies.exported", site=site, n=export(cfg, site)))
            except LoginNeeded as e:
                print(i18n.t("cookies.login_needed", why=str(e), site=site), file=sys.stderr)
                rc = 1
            except Exception as e:  # noqa: BLE001
                print(i18n.t("cookies.failed", site=site, why=str(e)[-300:]), file=sys.stderr)
                rc = 1
        print(status(cfg, site))
    return rc


def cmd_login(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex login", description=i18n.t("cookies.login_help"))
    ap.add_argument("site", choices=list(SITES))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--from-browser", metavar="NAME", help=i18n.t("cookies.opt_from_browser"))
    g.add_argument("--cookies", metavar="FILE", help=i18n.t("cookies.opt_cookies"))
    ap.add_argument("--timeout", type=int, default=LOGIN_TIMEOUT, help=i18n.t("cookies.opt_timeout"))
    a = ap.parse_args(argv)
    cfg = loader()
    if source(cfg, a.site) not in ("memex", ""):
        print(i18n.t("cookies.login_other_source", site=a.site, source=source(cfg, a.site)))
    try:
        rows = None
        if a.from_browser:
            rows = copy_from_browser(a.site, a.from_browser)
        elif a.cookies:
            rows = read_file(Path(a.cookies).expanduser())
        n = login(cfg, a.site, timeout=a.timeout, cookie_rows=rows)
    except LoginNeeded as e:
        print(i18n.t("cookies.login_failed", why=str(e)), file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(i18n.t("cookies.failed", site=a.site, why=str(e)[-300:]), file=sys.stderr)
        return 1
    print(i18n.t("cookies.logged_in", site=SITES[a.site].name, n=n))
    if source(cfg, a.site) == "":
        print(i18n.t("cookies.login_source_off", site=a.site))
    return 0


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "_extract":
    print(json.dumps(_extract(sys.argv[2], sys.argv[3])))
