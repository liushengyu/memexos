"""`memex console`: a local web console for the vault.

    memex console              start it in the background if it is not running, then open the browser
    memex console serve        run in the foreground (what the scheduler's console job runs)
    memex console stop | restart | status

It listens on 127.0.0.1 only (console.host / console.port). Pages are read-only; the few actions (pause, start a
job, inbox decisions, discard a held batch) need the token embedded in the page, so another web page open in the same
browser cannot trigger them, and requests whose Host is not this machine are refused (DNS rebinding).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from memexos import i18n
from memexos.console import actions
from memexos.console.data import Data
from memexos.util import atomic_write, self_cmd

STATIC = Path(__file__).parent / "static"
CTYPES = {".woff2": "font/woff2", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".html": "text/html; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}
UI_PREFIXES = ("console.", "job.", "step.", "report.kind.", "report.sev.")


def ui_strings(lang: str) -> dict[str, str]:
    """The language pack's console strings (plus job, step and review labels), English underneath as fallback."""
    out = {}
    for lg in (i18n.FALLBACK, lang):
        for k, v in i18n._pack(lg).items():
            if k.startswith(UI_PREFIXES):
                out[k.removeprefix("console.")] = v
    return out


class Console:
    def __init__(self, cfg):
        self.cfg = cfg
        self.data = Data(cfg)
        self.token = secrets.token_urlsafe(24)
        self.host = str(cfg.get("console.host") or "127.0.0.1")
        self.port = int(cfg.get("console.port") or 8765)
        self.lang = i18n.language()
        self.strings = ui_strings(self.lang)
        self.routes = {"/api/live": self.data.live, "/api/overview": self.data.overview, "/api/graph": self.data.graph,
                       "/api/pages": self.data.page_list, "/api/page": self.data.page, "/api/resolve": self.data.resolve,
                       "/api/search": self.data.search, "/api/runs": self.data.run_list, "/api/run": self.data.run_detail,
                       "/api/pipeline": self.data.pipeline, "/api/inbox": self.data.inbox, "/api/usage": self.data.usage,
                       "/api/quality": self.data.quality, "/api/profile": self.data.profile}

    def index(self, nonce: str) -> bytes:
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        boot = json.dumps({"lang": self.lang, "s": self.strings, "token": self.token,
                           "profile": bool(self.cfg.get("console.profile_page"))}, ensure_ascii=False).replace("</", "<\\/")
        page = page.replace("__BOOT__", boot).replace("__NONCE__", nonce).replace("__LANG__", self.lang)
        return _fill(page, self.strings).encode("utf-8")


def _fill(page: str, strings: dict[str, str]) -> str:
    """{{key}} in the static HTML → the escaped string from the language pack."""
    import re
    return re.sub(r"\{\{([\w.-]+)\}\}", lambda m: html.escape(str(strings.get(m.group(1), m.group(1)))), page)


def handler(con: Console):
    allowed = {"127.0.0.1", "localhost", "[::1]", con.host}

    class Handler(BaseHTTPRequestHandler):
        server_version = "memexos"

        def log_message(self, *a):
            pass

        def _host_ok(self) -> bool:
            h = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return h in allowed

        def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8", extra: dict | None = None) -> None:
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "host"})
            u = urllib.parse.urlparse(self.path)
            if u.path in ("/", "/index.html"):
                nonce = secrets.token_urlsafe(12)
                csp = (f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                return self._send(200, con.index(nonce), CTYPES[".html"], {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"})
            if u.path.startswith(("/app/", "/fonts/")):
                p = (STATIC / u.path.lstrip("/")).resolve()
                if not str(p).startswith(str(STATIC.resolve()) + os.sep) or not p.is_file():
                    return self._send(404, {"error": "not_found"})
                return self._send(200, p.read_bytes(), CTYPES.get(p.suffix, "application/octet-stream"))
            fn = con.routes.get(u.path)
            if not fn:
                return self._send(404, {"error": "not_found"})
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
            try:
                return self._send(200, fn(q))
            except Exception as e:  # noqa: BLE001 — one broken panel must not take the console down
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._host_ok() or self.headers.get("X-Memex-Token") != con.token or \
                    (origin and urllib.parse.urlparse(origin).hostname not in {h.strip("[]") for h in allowed}):
                return self._send(403, {"error": "token"})
            if urllib.parse.urlparse(self.path).path != "/api/action":
                return self._send(404, {"error": "not_found"})
            n = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "json"})
            try:
                ok, msg = actions.act(con.cfg, body if isinstance(body, dict) else {})
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"
            return self._send(200, {"ok": ok, "message": msg})

    return Handler


# ---------------------------------------------------------------- process management
def pidfile(cfg) -> Path:
    return cfg.paths.state / "console.json"


def running(cfg) -> dict | None:
    try:
        info = json.loads(pidfile(cfg).read_text())
        os.kill(int(info["pid"]), 0)
        return info
    except (OSError, ValueError, KeyError):
        return None


def url_of(info: dict) -> str:
    host = info["host"] if info["host"] not in ("0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{info['port']}/"


def _interrupt(*_):
    raise KeyboardInterrupt


class Server(ThreadingHTTPServer):
    # A page load opens a dozen connections at once (scripts, fonts, API calls); the default backlog of 5 makes the
    # system refuse or reset some of them while a slow request is being answered.
    request_queue_size = 128
    daemon_threads = True


def serve(cfg) -> int:
    con = Console(cfg)
    try:
        srv = Server((con.host, con.port), handler(con))
    except OSError as e:
        print(i18n.t("console.cli.port_busy", port=con.port, why=e.strerror), file=sys.stderr)
        return 1
    info = {"pid": os.getpid(), "host": con.host, "port": con.port, "started": time.strftime("%F %T")}
    cfg.paths.state.mkdir(parents=True, exist_ok=True)
    atomic_write(pidfile(cfg), json.dumps(info))
    print(i18n.t("console.cli.serving", url=url_of(info)), flush=True)
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        try:
            if json.loads(pidfile(cfg).read_text()).get("pid") == os.getpid():
                pidfile(cfg).unlink()
        except (OSError, ValueError):
            pass
    return 0


def stop(cfg) -> bool:
    info = running(cfg)
    if not info:
        return False
    os.kill(int(info["pid"]), signal.SIGTERM)
    for _ in range(50):
        if not running(cfg):
            break
        time.sleep(0.1)
    return True


def start_background(cfg) -> dict | None:
    logs = cfg.paths.state / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "console.log", "ab") as out:
        subprocess.Popen(self_cmd() + ["console", "serve"], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, env={**os.environ, "MEMEX_VAULT": str(cfg.vault)})
    for _ in range(80):
        info = running(cfg)
        if info:
            try:
                urllib.request.urlopen(url_of(info) + "api/resolve?name=", timeout=1).read()
                return info
            except OSError:
                pass
        time.sleep(0.1)
    return running(cfg)


def cmd_console(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex console")
    ap.add_argument("action", nargs="?", default="open", choices=["open", "serve", "stop", "restart", "status"])
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.port:
        os.environ["MEMEX_CONSOLE__PORT"] = str(a.port)
    cfg = loader()
    if a.action == "serve":
        return serve(cfg)
    if a.action == "stop":
        print(i18n.t("console.cli.stopped" if stop(cfg) else "console.cli.not_running"))
        return 0
    if a.action == "status":
        info = running(cfg)
        print(i18n.t("console.cli.status", url=url_of(info), pid=info["pid"], since=info["started"]) if info else i18n.t("console.cli.not_running"))
        return 0
    if a.action == "restart":
        stop(cfg)
    info = running(cfg) or start_background(cfg)
    if not info:
        print(i18n.t("console.cli.failed", log=cfg.paths.state / "logs" / "console.log"), file=sys.stderr)
        return 1
    print(i18n.t("console.cli.at", url=url_of(info)))
    if a.action == "open" and not a.no_browser:
        webbrowser.open(url_of(info))
    return 0
