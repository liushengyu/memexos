"""Export one site's login cookies from your browser, for tools that need a logged-in session (optional plugins).

Only the cookies of that site's domains are written, to ~/.config/memexos/cookies/<site>.txt with mode 600;
other sites' sessions never touch the disk. The browser is configured per site (plugins.<site>.browser).
Decryption uses yt-dlp's browser-cookie support.

On macOS, background jobs are not allowed to read the browser's data, so exporting only works from a terminal
(`memex cookies refresh`). Background jobs keep using the last exported file, which usually stays valid for weeks.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from memexos import i18n
from memexos.capture.redact import cookie_dir_of
from memexos.util import atomic_write, which

SITES = {"bilibili": (("bilibili.com",), ("SESSDATA",)),
         "youtube": (("youtube.com",), ("__Secure-3PSID", "LOGIN_INFO")),
         "x": (("x.com", "twitter.com"), ("auth_token",))}
MAX_AGE = 6 * 3600


def path(cfg, site: str) -> Path:
    return cookie_dir_of(cfg) / f"{site}.txt"


def browser(cfg, site: str) -> str:
    return (cfg.get(f"plugins.{site}.browser") or "").strip()


def file_for(cfg, site: str) -> Path | None:
    """The exported cookie file to pass to tools, refreshing it first when it is old and a browser is configured."""
    if site not in SITES or not browser(cfg, site):
        return None
    ensure(cfg, site)
    p = path(cfg, site)
    return p if p.exists() else None


def _ytdlp_python() -> str | None:
    """yt_dlp may only be installed in yt-dlp's own environment; find its interpreter from the script's shebang."""
    exe = which("yt-dlp")
    if exe:
        try:
            first = open(exe, "rb").readline().decode(errors="ignore")
        except OSError:
            return None
        if first.startswith("#!") and "python" in first:
            return first[2:].strip().split()[0]
    return None


def export(cfg, site: str) -> int:
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
    except ImportError:
        py = _ytdlp_python()
        if not py:
            raise RuntimeError(i18n.t("cookies.no_ytdlp")) from None
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH")]))}
        r = subprocess.run([py, "-m", "memexos.capture.cookies", "_export", site, browser(cfg, site), str(path(cfg, site))],
                           capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-300:]) from None
        return int(r.stdout.strip() or 0)
    return _export(site, browser(cfg, site), path(cfg, site), extract_cookies_from_browser)


def _export(site: str, browser_name: str, dest: Path, extract=None) -> int:
    if extract is None:
        from yt_dlp.cookies import extract_cookies_from_browser as extract

    class Quiet:
        def debug(self, *a, **k):
            pass
        info = warning = debug

        def error(self, msg, *a, **k):
            print(msg, file=sys.stderr)

    domains, login = SITES[site]
    jar = extract(browser_name, logger=Quiet())
    rows = [c for c in jar if any(c.domain.lstrip(".") == d or c.domain.lstrip(".").endswith("." + d) for d in domains)]
    if not any(c.name in login for c in rows):
        raise RuntimeError(i18n.t("cookies.not_logged_in", site=site, browser=browser_name))
    lines = ["# Netscape HTTP Cookie File", f"# memexos: {site} login cookies exported from {browser_name}; refreshed automatically"]
    for c in rows:
        lines.append("\t".join([c.domain, "TRUE" if c.domain.startswith(".") else "FALSE", c.path or "/", "TRUE" if c.secure else "FALSE",
                                str(int(c.expires or 0)), c.name, c.value or ""]))
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(dest, "\n".join(lines) + "\n", mode=0o600)
    return len(rows)


def ensure(cfg, site: str) -> bool:
    """Re-export when the file is older than MAX_AGE; on failure keep the old file. True when a file exists."""
    p = path(cfg, site)
    if p.exists() and time.time() - p.stat().st_mtime < MAX_AGE:
        return True
    try:
        export(cfg, site)
    except Exception:  # noqa: BLE001 — typically a background job without access to the browser
        pass
    return p.exists()


def status(cfg, site: str) -> str:
    p = path(cfg, site)
    if not p.exists():
        return i18n.t("cookies.none", site=site)
    names = [l.split("\t")[5] for l in p.read_text(errors="ignore").splitlines() if not l.startswith("#") and l.count("\t") >= 6]
    ok = any(n in SITES[site][1] for n in names)
    return i18n.t("cookies.status", site=site, n=len(names), login=i18n.t("cookies.yes" if ok else "cookies.no"),
                  minutes=int((time.time() - p.stat().st_mtime) / 60))


def cmd_cookies(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex cookies")
    ap.add_argument("action", nargs="?", default="status", choices=["status", "refresh"])
    a = ap.parse_args(argv)
    cfg = loader()
    rc = 0
    for site in SITES:
        if not browser(cfg, site):
            continue
        if a.action == "refresh":
            try:
                print(i18n.t("cookies.exported", site=site, n=export(cfg, site)))
            except Exception as e:  # noqa: BLE001
                print(i18n.t("cookies.failed", site=site, why=str(e)[-200:]), file=sys.stderr)
                rc = 1
        print(status(cfg, site))
    return rc


if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "_export":
    print(_export(sys.argv[2], sys.argv[3], Path(sys.argv[4])))
