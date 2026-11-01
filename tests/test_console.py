"""The console server: pages, every API, and the guards on actions."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from memexos import config, scaffold
from memexos.console.server import Console, handler


@pytest.fixture
def console(home, monkeypatch):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    monkeypatch.setenv("MEMEX_VAULT", str(v))
    monkeypatch.setenv("MEMEX_SEARCH__BACKEND", "fts")
    cfg = config.load(v)
    con = Console(cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler(con))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield con, srv.server_address[1], cfg
    srv.shutdown()


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    return r.status, r.getheader("Content-Type") or "", r.read()


def test_index_embeds_token_language_and_strings(console):
    con, port, _ = console
    st, ctype, body = req(port, "GET", "/")
    html = body.decode()
    assert st == 200 and "text/html" in ctype
    assert con.token in html and '<html lang="en">' in html and "{{" not in html and "__BOOT__" not in html
    assert ">Overview<" in html


def test_every_api_answers_on_an_empty_vault(console):
    _, port, _ = console
    for path in ("/api/live", "/api/overview", "/api/graph", "/api/pages", "/api/page?path=index.md", "/api/resolve?name=x",
                 "/api/search?q=memex", "/api/runs?hours=24", "/api/run?id=x", "/api/pipeline", "/api/inbox",
                 "/api/usage?days=7", "/api/quality", "/api/profile"):
        st, ctype, body = req(port, "GET", path)
        assert st == 200, (path, body[:300])
        json.loads(body)
    assert json.loads(req(port, "GET", "/api/profile")[2]) == {"enabled": False, "items": []}


def test_pages_outside_the_vault_cannot_be_read(console):
    _, port, _ = console
    for p in ("../../etc/passwd", "/etc/passwd", ".memex/config.toml", "wiki/../../x"):
        assert json.loads(req(port, "GET", f"/api/page?path={p}")[2]) == {"error": "not_found"}
    assert req(port, "GET", "/app/../server.py")[0] == 404


def test_foreign_host_is_refused(console):
    _, port, _ = console
    assert req(port, "GET", "/api/live", headers={"Host": "evil.example:80"})[0] == 403


def test_actions_need_the_token_and_a_local_origin(console):
    con, port, cfg = console
    assert req(port, "POST", "/api/action", {"action": "pause"})[0] == 403
    assert req(port, "POST", "/api/action", {"action": "pause"}, {"X-Memex-Token": "wrong"})[0] == 403
    assert req(port, "POST", "/api/action", {"action": "pause"}, {"X-Memex-Token": con.token, "Origin": "https://evil.example"})[0] == 403
    assert not (cfg.paths.state / "PAUSE").exists()
    st, _, body = req(port, "POST", "/api/action", {"action": "pause"}, {"X-Memex-Token": con.token, "Origin": f"http://127.0.0.1:{port}"})
    assert st == 200 and json.loads(body)["ok"] and (cfg.paths.state / "PAUSE").exists()
    st, _, body = req(port, "POST", "/api/action", {"action": "resume"}, {"X-Memex-Token": con.token})
    assert json.loads(body)["ok"] and not (cfg.paths.state / "PAUSE").exists()
    st, _, body = req(port, "POST", "/api/action", {"action": "abandon", "branch": "main; rm -rf /"}, {"X-Memex-Token": con.token})
    assert json.loads(body)["ok"] is False


def test_chinese_strings_cover_the_same_keys():
    from memexos.console.server import ui_strings
    zh = ui_strings("zh")
    assert zh["nav.overview"] == "总览" and zh["job.ingest"] == "编译" and zh["step.gate2"] == "独立审校"
    assert set(ui_strings("en")) == set(zh)
