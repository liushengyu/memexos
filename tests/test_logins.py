"""Site logins for the collectors: logging in once, MemexOS renewing the login by itself (also where no one is at the
screen), and an ended login being noticed and announced instead of failing quietly.

These drive a real Chrome/Chromium/Edge/Brave against a small local site that behaves like a login-protected one.
They are skipped when no such browser is installed, unless MEMEX_REAL_BROWSER=1 (CI sets it).
"""

import http.server
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from memexos import config
from memexos.capture import browser as B
from memexos.capture import cookies
from memexos.cli import main
from memexos.util import script_python


class Site(http.server.BaseHTTPRequestHandler):
    """/login sets the login cookie; /home renews ct0, or ends the session when the server is told to."""
    end_session = False
    visits: list = []

    def do_GET(self):
        Site.visits.append((self.path, self.headers.get("User-Agent", "")))
        self.send_response(200)
        if self.path.startswith("/login"):
            self.send_header("Set-Cookie", "auth_token=tok1; Path=/; Max-Age=864000; HttpOnly")
            self.send_header("Set-Cookie", "ct0=c1; Path=/; Max-Age=864000")
        elif self.path.startswith("/home"):
            if Site.end_session:
                self.send_header("Set-Cookie", "auth_token=; Path=/; Max-Age=0")
            else:
                self.send_header("Set-Cookie", f"ct0=c{int(time.time() * 1000)}; Path=/; Max-Age=864000")
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>ok</body></html>")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="session")
def exe():
    """Looked up once, before any test replaces HOME (the installer puts a browser under the real home)."""
    from conftest import ORIGINAL_ENV
    found = B.find()
    if not found:
        if ORIGINAL_ENV.get("MEMEX_REAL_BROWSER") == "1":
            pytest.fail("a Chromium-family browser is required here (MEMEX_REAL_BROWSER=1) but none was found")
        pytest.skip("no Chrome, Chromium, Edge or Brave installed")
    return found


@pytest.fixture
def site(home, tmp_path, monkeypatch, exe):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    Site.end_session, Site.visits = False, []
    monkeypatch.setitem(cookies.SITES, "x", cookies.Site("X", ("127.0.0.1",), ("auth_token",), f"{base}/home", f"{base}/login"))
    monkeypatch.setenv("MEMEX_BROWSER_PROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("MEMEX_COOKIE_DIR", str(tmp_path / "cookies"))
    notes = []
    monkeypatch.setattr(cookies, "notify", lambda title, text: notes.append(text))
    v = home / "memex"
    from memexos import scaffold
    scaffold.init(v, lang="en", write_user_config=False)
    cfg = config.load(v)
    cfg.data["plugins"]["x"].update(enabled=True, login="memex", proxy="direct")
    cfg.data.setdefault("browser", {})["command"] = exe            # found under the real home, which tests replace
    yield cfg, base, notes
    srv.shutdown()


def test_log_in_once_then_the_login_renews_itself(site):
    cfg, base, notes = site
    assert cookies.needs_login(cfg) == ["x"]
    n = cookies.login(cfg, "x", headless=True, timeout=60, out=lambda *a: None)
    assert n >= 2
    f = cookies.path(cfg, "x")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    rows = {c["name"]: c["value"] for c in cookies.read_file(f)}
    assert rows["auth_token"] == "tok1"
    assert cookies.needs_login(cfg) == [] and "logged in" in cookies.status(cfg, "x")

    before = rows["ct0"]
    os.utime(f, (time.time() - cookies.MAX_AGE - 60,) * 2)       # an old copy is renewed before use
    assert cookies.file_for(cfg, "x") == f
    after = {c["name"]: c["value"] for c in cookies.read_file(f)}
    assert after["auth_token"] == "tok1" and after["ct0"] != before    # the site renewed ct0 during the visit
    assert any(p.startswith("/home") for p, _ in Site.visits)
    assert not notes


def test_the_visit_does_not_announce_itself_as_headless(site):
    cfg, base, notes = site
    cookies.login(cfg, "x", headless=True, timeout=60, out=lambda *a: None)
    cookies.renew(cfg, "x")
    homes = [ua for p, ua in Site.visits if p.startswith("/home")]
    assert homes and all("Headless" not in ua and "Chrome/" in ua for ua in homes)


def test_an_ended_session_is_noticed_and_announced_once(site, monkeypatch, capsys):
    cfg, base, notes = site
    cookies.login(cfg, "x", headless=True, timeout=60, out=lambda *a: None)
    Site.end_session = True
    with pytest.raises(cookies.LoginNeeded):
        cookies.renew(cfg, "x")
    assert cookies.needs_login(cfg) == ["x"] and len(notes) == 1
    from memexos import status
    assert any("memex login x" in a for a in status.snapshot(cfg)["alerts"])
    with pytest.raises(cookies.LoginNeeded):
        cookies.renew(cfg, "x")
    assert len(notes) == 1                                         # not again for the same ended login
    names = [json.loads(line)["type"] for f in (cfg.paths.state / "events").glob("*.jsonl") for line in f.read_text().splitlines()]
    assert "login.expired" in names


def test_agents_can_renew_and_are_told_what_to_do_when_it_ended(site, monkeypatch, capsys):
    cfg, base, notes = site
    cookies.login(cfg, "x", headless=True, timeout=60, out=lambda *a: None)
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    monkeypatch.setenv("MEMEX_VAULT", str(cfg.vault))
    capsys.readouterr()
    assert main(["cookies", "refresh", "x"]) == 0
    assert "renewed" in capsys.readouterr().out
    Site.end_session = True
    assert main(["cookies", "refresh", "x"]) == 1
    err = capsys.readouterr().err
    assert "memex login x" in err


def test_a_cookie_file_from_elsewhere_becomes_a_renewable_login(site, tmp_path):
    """For machines without a screen: cookies exported on another computer are put into MemexOS's profile."""
    cfg, base, notes = site
    exported = tmp_path / "exported.txt"
    exp = int(time.time()) + 86400
    exported.write_text("# Netscape HTTP Cookie File\n"
                        f"127.0.0.1\tFALSE\t/\tFALSE\t{exp}\tauth_token\tfromfile\n127.0.0.1\tFALSE\t/\tFALSE\t{exp}\tct0\tc9\n")
    n = cookies.login(cfg, "x", cookie_rows=cookies.read_file(exported))
    assert n >= 2
    rows = {c["name"]: c["value"] for c in cookies.read_file(cookies.path(cfg, "x"))}
    assert rows["auth_token"] == "fromfile" and rows["ct0"] != "c9"     # renewed by the visit
    with pytest.raises(cookies.LoginNeeded):
        cookies.login(cfg, "x", cookie_rows=[{"domain": "127.0.0.1", "name": "other", "value": "1", "expires": exp}])


def test_a_site_never_logged_in_does_not_start_a_browser(site, monkeypatch):
    cfg, base, notes = site
    monkeypatch.setattr(B, "find", lambda *a: pytest.fail("no browser should start"))
    assert cookies.file_for(cfg, "x") is None
    assert cookies.file_for(cfg, "bilibili") is None


def test_the_profile_is_private(site):
    cfg, base, notes = site
    cookies.login(cfg, "x", headless=True, timeout=60, out=lambda *a: None)
    assert stat.S_IMODE(B.profile_dir().stat().st_mode) == 0o700


def test_no_window_where_there_is_no_screen(site, monkeypatch):
    cfg, base, notes = site
    monkeypatch.setattr(B, "can_show_window", lambda: False)
    with pytest.raises(RuntimeError, match="--cookies"):
        cookies.login(cfg, "x")


# ---------------------------------------------------------------- without a browser
def test_script_python_reads_every_launcher_form(tmp_path, monkeypatch):
    py = tmp_path / "python3"
    py.write_text("")
    py.chmod(0o755)
    a = tmp_path / "a"
    a.write_text("#!/opt/tools/yt/bin/python\nimport sys\n")
    assert script_python(str(a)) == "/opt/tools/yt/bin/python"
    b = tmp_path / "b"
    b.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert script_python(str(b)) == str(py)
    c = tmp_path / "c"
    c.write_text("#!/bin/sh\n'''exec' \"/very/long/path/bin/python\" \"$0\" \"$@\"\n' '''\n")
    assert script_python(str(c)) == "/very/long/path/bin/python"
    d = tmp_path / "d"
    d.write_bytes(b"\x7fELF binary")
    assert script_python(str(d)) is None and script_python(None) is None


FAKE_TWITTER = """#!/usr/bin/env python3
import json
print(json.dumps({"ok": False, "error": {"code": "not_authenticated", "message": "Cookie expired or invalid (HTTP 401)"}}))
raise SystemExit(1)
"""


def test_x_reports_an_ended_login_from_twitter_cli(home, tmp_path, monkeypatch):
    from memexos.capture.fetch import PageUnavailable
    from memexos.plugins import x
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "twitter").write_text(FAKE_TWITTER)
    (bin_dir / "twitter").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("MEMEX_COOKIE_DIR", str(tmp_path / "cookies"))
    notes = []
    monkeypatch.setattr(cookies, "notify", lambda t, s: notes.append(s))
    cfg = config.load(home / "v")
    cfg.data["plugins"]["x"].update(enabled=True, login="memex")
    with pytest.raises(PageUnavailable, match="memex login x"):
        x.twitter(cfg, "whoami")                                   # no login yet: never falls back to other browsers
    f = cookies.path(cfg, "x")
    f.parent.mkdir(parents=True)
    f.write_text("127.0.0.1\tFALSE\t/\tFALSE\t0\tauth_token\ta\n127.0.0.1\tFALSE\t/\tFALSE\t0\tct0\tb\n")
    with pytest.raises(PageUnavailable, match="no longer accepts"):
        x.twitter(cfg, "whoami")
    assert cookies.needs_login(cfg) == ["x"] and notes


def test_x_first_run_reads_only_the_newest(home, monkeypatch):
    from memexos.plugins import x
    asked = []
    monkeypatch.setattr(x, "f_bookmarks", lambda cfg, args, n, as_json: asked.append(n) or "[]")
    monkeypatch.setattr(x, "f_likes", lambda cfg, args, n, as_json: asked.append(n) or "[]")
    cfg = config.load(home / "v")
    state: dict = {}
    x.collect(cfg, state, set(), 30)
    x.collect(cfg, state, set(), 30)
    assert asked == [50, 50, 300, 300]


def test_old_setting_name_still_works(home):
    cfg = config.load(home / "v")
    cfg.data["plugins"]["x"].pop("login", None)
    cfg.data["plugins"]["x"]["browser"] = "firefox"
    assert cookies.source(cfg, "x") == "firefox"


def test_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEX_BROWSER_PROFILE", str(tmp_path / "p"))
    assert B.profile_dir() == Path(tmp_path / "p")
