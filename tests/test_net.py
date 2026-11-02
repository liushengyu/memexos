"""The shared HTTP module, with both clients, against a local server."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from memexos import net


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
        elif self.path == "/final":
            body = "héllo".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.parametrize("prefer", ["curl", "urllib"])
def test_redirects_text_post_and_errors(base, prefer):
    if prefer == "curl" and net.client("curl") != "curl":
        pytest.skip("curl not installed")
    r = net.request(base + "/hop", proxy="", prefer=prefer)
    assert r.status == 200 and r.url.endswith("/final") and r.redirects == [base + "/hop"] and r.text() == "héllo"
    assert net.request(base + "/x", method="POST", data=b'{"a":1}', proxy="", prefer=prefer).data == b'{"a":1}'
    with pytest.raises(net.NetError) as e:
        net.request(base + "/missing", proxy="", prefer=prefer)
    assert e.value.status == 404
    seen = []
    with pytest.raises(ValueError):                      # every hop is checked, including the redirect target
        net.request(base + "/hop", proxy="", prefer=prefer, check=lambda u: seen.append(u) or (u.endswith("/final") and (_ for _ in ()).throw(ValueError("private"))))
    assert seen[-1].endswith("/final")


def test_urllib_refuses_https_proxy_clearly(base):
    with pytest.raises(net.NetError) as e:
        net.request("https://example.com/", proxy="https://proxy.invalid:443", prefer="urllib")
    assert "curl" in str(e.value)


def test_last_header_block_wins():
    raw = "HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 301\r\nlocation: /next\r\n\r\n"
    assert net._last_block(raw) == (301, {"location": "/next"})


def test_platform_matches_whole_host_names():
    from memexos.capture.fetch import platform
    assert platform("https://www.youtube.com/watch?v=x") == "youtube"
    assert platform("https://youtu.be/x") == "youtube"
    assert platform("https://m.bilibili.com/video/BV1") == "bilibili"
    assert platform("https://x.com/a/status/1") == "x"
    assert platform("https://youtube.com.example.net/watch") == "web"
    assert platform("https://notyoutube.com/") == "web"
    assert platform("https://box.com/file") == "web"
    assert platform("https://user@evil.example/?u=bilibili.com") == "web"
