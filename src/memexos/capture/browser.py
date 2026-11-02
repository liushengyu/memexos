"""A browser profile that belongs to MemexOS, for sites it collects from with your login (the optional plugins).

`memex login <site>` opens a window on this profile at the site's login page and you log in once. After that
MemexOS keeps the login alive by itself: before a collector needs it, it opens the site in the same profile without
a window, lets the site renew its cookies the way it does for any returning visitor, and copies that site's cookies
to a file for the tools that need them (capture/cookies.py). Your everyday browser is never read, so this works in
scheduled jobs (macOS does not let those read the everyday browser's data) and for agents.

Chrome, Chromium, Edge or Brave is driven through its DevTools protocol over a pipe (--remote-debugging-pipe): no
network port is opened, so no other program can attach to the profile. The profile lives in
~/.local/share/memexos/browser (mode 700), which agents working for MemexOS are not allowed to read.
"""

from __future__ import annotations

import fcntl
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from memexos.util import atomic_write

MAC_APPS = ["Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Google Chrome for Testing"]
LINUX_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]


class BrowserError(RuntimeError):
    pass


def profile_dir() -> Path:
    if os.environ.get("MEMEX_BROWSER_PROFILE"):
        return Path(os.environ["MEMEX_BROWSER_PROFILE"])
    return data_dir() / "browser"


def find(cfg=None) -> str | None:
    """The browser to use: [browser] command, else the first Chromium-family browser installed."""
    want = (cfg.get("browser.command") if cfg is not None else "") or os.environ.get("MEMEX_BROWSER") or ""
    if want:
        return shutil.which(os.path.expanduser(want)) or (want if os.access(os.path.expanduser(want), os.X_OK) else None)
    if sys.platform == "darwin":
        for root in ("/Applications", os.path.expanduser("~/Applications")):
            for app in MAC_APPS:
                exe = Path(root) / f"{app}.app" / "Contents" / "MacOS" / app
                if os.access(exe, os.X_OK):
                    return str(exe)
    for name in LINUX_NAMES:
        exe = shutil.which(name)
        if exe:
            return exe
    for root in (data_dir() / "browsers", Path.home() / "Library" / "Caches" / "ms-playwright", Path.home() / ".cache" / "ms-playwright"):
        # Chrome for Testing, as the installer downloads it (with Playwright's installer, which also brings the system
        # libraries it needs on Linux); newest build first
        pats = ["chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium", "chromium-*/chrome-linux*/chrome"]
        hits = sorted((p for pat in pats for p in root.glob(pat) if os.access(p, os.X_OK)), key=lambda p: str(p), reverse=True)
        if hits:
            return str(hits[0])
    return None


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "memexos"


def can_show_window() -> bool:
    """Whether a browser window can appear here (a desktop session), as opposed to a server over SSH."""
    if sys.platform == "darwin":
        return not os.environ.get("SSH_CONNECTION") or bool(os.environ.get("MEMEX_BROWSER_WINDOW"))
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class Browser:
    """One browser process on the MemexOS profile, controlled over the DevTools pipe.

        with Browser(exe, headless=True) as b:
            b.visit("https://example.com/")
            cookies = b.cookies()
    """

    def __init__(self, exe: str, profile: Path | None = None, headless: bool = True, url: str = "about:blank",
                 proxy: str | None = None, user_agent: str = ""):
        self.exe, self.headless, self.url, self.proxy, self.user_agent = exe, headless, url, proxy, user_agent
        self.sandbox = not (hasattr(os, "geteuid") and os.geteuid() == 0)     # Chrome refuses to start as root otherwise
        self.err_text = ""
        self.profile = profile or profile_dir()
        self.proc: subprocess.Popen | None = None
        self._id, self._buf = 0, b""
        self._events: list[dict] = []

    # -------------------------------------------------------- process
    def args(self) -> list[str]:
        # no traffic of its own (updates, safe-browsing lists, metrics): it only opens the sites it is sent to, through
        # the route MemexOS gives it
        a = [self.exe, f"--user-data-dir={self.profile}", "--remote-debugging-pipe", "--no-first-run",
             "--no-default-browser-check", "--disable-sync", "--use-mock-keychain", "--password-store=basic",
             "--disable-background-networking", "--disable-component-update", "--no-pings", "--metrics-recording-only"]
        if self.headless:
            a += ["--headless=new", "--mute-audio"]
        if self.user_agent:
            a.append(f"--user-agent={self.user_agent}")
        if self.proxy:
            a.append(f"--proxy-server={self.proxy}")
        elif self.proxy == "":
            a.append("--no-proxy-server")
        if not self.sandbox:
            a.append("--no-sandbox")
        if sys.platform.startswith("linux"):
            a.append("--disable-dev-shm-usage")   # containers give /dev/shm 64 MB, too little for some pages
        return a + [self.url]

    def __enter__(self) -> Browser:
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.profile, 0o700)
        try:
            return self._start()
        except BrowserError as e:
            # Chrome builds without a system AppArmor profile (Chrome for Testing, Chromium from a download) cannot use
            # their sandbox on Ubuntu 23.10+ or in containers. The sites visited are the few the owner logs in to.
            if self.sandbox and "No usable sandbox" in self.err_text:
                self.sandbox = False
                return self._start()
            if "no answer" in str(e):          # a first start on a slow or busy machine: once more, with more patience
                return self._start(wait=120)
            raise

    def _start(self, wait: float = 45) -> Browser:
        self._id, self._buf, self.err_text = 0, b"", ""
        to_r, self._to = os.pipe()
        self._from, from_w = os.pipe()

        def remap():                              # the protocol pipe is fds 3 (commands in) and 4 (replies out)
            a = fcntl.fcntl(to_r, fcntl.F_DUPFD, 10)
            b = fcntl.fcntl(from_w, fcntl.F_DUPFD, 10)
            os.dup2(a, 3)
            os.dup2(b, 4)
        # its log goes to a file: a pipe nobody reads would fill up and stall the browser during a long login
        self._errf = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(self.args(), close_fds=False, preexec_fn=remap, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=self._errf, start_new_session=True)
        finally:
            os.close(to_r)
            os.close(from_w)
        try:
            self.call("Browser.getVersion", timeout=wait)
        except BrowserError:
            self._kill()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            self._cleanup()
            return
        try:
            self.call("Browser.close", timeout=10)
            self.proc.wait(timeout=15)
        except (BrowserError, subprocess.TimeoutExpired, OSError):
            self._kill()
        self._cleanup()

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._cleanup()

    def _cleanup(self) -> None:
        if getattr(self, "_errf", None) is not None and self.proc and self.proc.poll() is not None:
            self._why_dead()
            self._errf.close()
            self._errf = None
        for fd in ("_to", "_from"):
            if getattr(self, fd, None) is not None:
                try:
                    os.close(getattr(self, fd))
                except OSError:
                    pass
                setattr(self, fd, None)

    def alive(self) -> bool:
        return bool(self.proc) and self.proc.poll() is None

    def _why_dead(self) -> str:
        if self.proc and getattr(self, "_errf", None) and not self.err_text:
            try:
                if self.proc.poll() is None:
                    self.proc.wait(timeout=5)
                self._errf.seek(0, os.SEEK_END)
                self._errf.seek(max(0, self._errf.tell() - 20000))
                self.err_text = self._errf.read().decode(errors="replace")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        lines = [l for l in self.err_text.splitlines() if l.strip()]
        fatal = [l for l in lines if "FATAL" in l or "error while loading shared libraries" in l]
        why = (fatal or lines[-1:] or [""])[0]
        return why[-400:] or f"the browser exited ({self.proc.returncode if self.proc else '?'})"

    # -------------------------------------------------------- protocol
    def call(self, method: str, timeout: float = 30, session: str | None = None, **params) -> dict:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        try:
            os.write(self._to, json.dumps(msg).encode() + b"\0")
        except (OSError, TypeError):
            raise BrowserError(self._why_dead()) from None
        deadline = time.time() + timeout
        while True:
            d = self._next(deadline)
            if d is None:
                raise BrowserError(f"{method}: no answer from the browser within {timeout:.0f}s")
            if d.get("id") == self._id:
                if "error" in d:
                    raise BrowserError(f"{method}: {d['error'].get('message', d['error'])}")
                return d.get("result") or {}
            if "method" in d:
                self._events.append(d)

    def _next(self, deadline: float) -> dict | None:
        while b"\0" not in self._buf:
            left = deadline - time.time()
            if left <= 0:
                return None
            ready, _, _ = select.select([self._from], [], [], left)
            if not ready:
                return None
            chunk = os.read(self._from, 1 << 16)
            if not chunk:
                raise BrowserError(self._why_dead())
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\0", 1)
        return json.loads(raw)

    def wait_event(self, method: str, timeout: float, session: str | None = None) -> bool:
        deadline = time.time() + timeout
        while True:
            for i, e in enumerate(self._events):
                if e.get("method") == method and (session is None or e.get("sessionId") == session):
                    del self._events[: i + 1]
                    return True
            self._events.clear()
            d = self._next(deadline)
            if d is None:
                return False
            if "method" in d:
                self._events.append(d)

    # -------------------------------------------------------- what MemexOS needs
    def cookies(self) -> list[dict]:
        """Every cookie in the profile (browser-wide; no page is attached for this)."""
        return self.call("Storage.getCookies").get("cookies") or []

    def visit(self, url: str, settle: float = 4.0, timeout: float = 45.0) -> None:
        """Open url in a new tab, wait for it to load and for its scripts to run a little (sites renew cookies from
        script), then close the tab."""
        tid = self.call("Target.createTarget", url="about:blank")["targetId"]
        try:
            sid = self.call("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"]
            self.call("Page.enable", session=sid)
            self.call("Page.navigate", session=sid, url=url, timeout=timeout)
            self.wait_event("Page.loadEventFired", timeout, session=sid)
            end = time.time() + settle
            while time.time() < end:                    # keep reading so the pipe never fills up
                self._next(min(end, time.time() + 0.5))
        finally:
            try:
                self.call("Target.closeTarget", targetId=tid, timeout=10)
            except BrowserError:
                pass


def user_agent(exe: str, profile: Path | None = None) -> str:
    """The user agent a normal window of this browser sends. Without a window the browser calls itself
    "HeadlessChrome", which some sites treat as a robot; the rest of its identity (client hints) is already the same.
    Cached per browser build, so the extra start-up happens once per browser update."""
    cache = (profile or profile_dir()).parent / "browser-ua.json"
    try:
        st = os.stat(exe)
        key = f"{exe}:{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        key = exe
    try:
        c = json.loads(cache.read_text())
        if c.get("key") == key and c.get("ua"):
            return c["ua"]
    except (OSError, ValueError):
        pass
    with Browser(exe, profile=profile, headless=True) as b:
        ua = b.call("Browser.getVersion").get("userAgent", "")
    ua = ua.replace("HeadlessChrome/", "Chrome/")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(cache, json.dumps({"key": key, "ua": ua}))
    except OSError:
        pass
    return ua
