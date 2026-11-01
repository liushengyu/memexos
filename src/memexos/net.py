"""HTTP for capture, feeds, plugins and online judges.

`curl` is used when it is installed: it decompresses, speaks HTTP/2, and supports https:// and socks proxies, which
Python's own client does not. Without curl, urllib is used and an https:// proxy is reported as such instead of
failing with a reset connection.

Redirects are followed here, one hop at a time, so every address can be checked (`check`), for example to refuse
redirects into a private network.

Proxy values everywhere: None = inherit the environment (HTTPS_PROXY …), "" = direct, otherwise a proxy URL.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from memexos.util import which

REDIRECTS = (301, 302, 303, 307, 308)


class NetError(Exception):
    """A request that produced no usable response. `status` is set for HTTP errors (4xx/5xx)."""

    def __init__(self, message: str, status: int | None = None, url: str = "", body: bytes = b""):
        super().__init__(message)
        self.status, self.url, self.body = status, url, body


@dataclass
class Response:
    url: str                                   # after redirects
    status: int
    headers: dict[str, str] = field(default_factory=dict)      # lowercase names
    data: bytes = b""
    redirects: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").lower()

    @property
    def charset(self) -> str:
        ct = self.headers.get("content-type", "")
        for part in ct.split(";")[1:]:
            k, _, v = part.strip().partition("=")
            if k.lower() == "charset" and v:
                return v.strip('"')
        return "utf-8"

    def text(self) -> str:
        return self.data.decode(self.charset, errors="ignore")


def client(prefer: str = "auto") -> str:
    return "curl" if prefer in ("auto", "curl") and which("curl") else "urllib"


def request(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None,
            proxy: str | None = None, proxy_cacert: str | None = None, timeout: float = 60, max_redirects: int = 8,
            check: Callable[[str], object] | None = None, to_file: Path | None = None, follow: bool = True,
            prefer: str = "auto") -> Response:
    """One request with redirects followed by hand. Raises NetError for network failures and HTTP errors >= 400.
    With to_file the body is written there instead of kept in memory."""
    seen: list[str] = []
    cur, meth, body = url, method, data
    for _ in range(max_redirects + 1):
        if check:
            check(cur)
        r = (_curl if client(prefer) == "curl" else _urllib)(cur, meth, body, headers or {}, proxy, proxy_cacert, timeout, to_file)
        loc = r.headers.get("location")
        if follow and r.status in REDIRECTS and loc:
            seen.append(cur)
            cur = urllib.parse.urljoin(cur, loc)
            if r.status == 303 or (r.status in (301, 302) and meth == "POST"):
                meth, body = "GET", None
            continue
        r.redirects = seen
        if r.status >= 400:
            raise NetError(f"HTTP {r.status}", status=r.status, url=cur, body=r.data[:2000])
        return r
    raise NetError(f"more than {max_redirects} redirects", url=cur)


def _curl(url, method, data, headers, proxy, cacert, timeout, to_file) -> Response:
    with tempfile.TemporaryDirectory(prefix="memex-http-") as d:
        hdr, out = Path(d) / "headers", (to_file or Path(d) / "body")
        cmd = [which("curl") or "curl", "-sS", "--compressed", "--proto", "=http,https", "-m", str(int(timeout)),
               "-X", method, "-D", str(hdr), "-o", str(out)]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if proxy == "":
            cmd += ["--noproxy", "*"]
        elif proxy:
            cmd += ["--proxy", proxy]
        if cacert:
            cmd += ["--proxy-cacert", os.path.expanduser(cacert)]
        if data is not None:
            cmd += ["--data-binary", "@-"]
        cmd += ["--url", url]
        env = dict(os.environ)
        if proxy is None and "https_proxy" not in env and env.get("HTTPS_PROXY"):
            env["https_proxy"] = env["HTTPS_PROXY"]       # curl reads the lowercase name for https
        r = subprocess.run(cmd, input=data, capture_output=True, env=env, timeout=timeout + 15)
        if r.returncode != 0:
            raise NetError((r.stderr or b"").decode(errors="ignore").strip()[:300] or f"curl exit {r.returncode}", url=url)
        status, hs = _last_block(hdr.read_text(encoding="latin-1") if hdr.exists() else "")
        body = b"" if to_file else (out.read_bytes() if out.exists() else b"")
        return Response(url=url, status=status, headers=hs, data=body)


def _last_block(text: str) -> tuple[int, dict[str, str]]:
    """curl -D writes one header block per response, including a proxy's "200 Connection established": keep the last."""
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n") if b.strip().startswith("HTTP/")]
    if not blocks:
        return 0, {}
    lines = blocks[-1].strip().split("\n")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        status = 0
    hs = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        if k.strip():
            hs[k.strip().lower()] = v.strip()
    return status, hs


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _urllib(url, method, data, headers, proxy, cacert, timeout, to_file) -> Response:
    if proxy is None:
        env_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
        if url.startswith("https:") and env_proxy.startswith("https://"):
            raise NetError("the proxy in HTTPS_PROXY uses https://, which needs curl (install curl, or set an http:// proxy)", url=url)
        handlers = [_NoRedirect()]
    else:
        if proxy.startswith("https://"):
            raise NetError("an https:// proxy needs curl (install curl, or use an http:// proxy)", url=url)
        handlers = [_NoRedirect(), urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    op = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = op.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:            # 3xx (not followed here) and 4xx/5xx
        return Response(url=url, status=e.code, headers={k.lower(): v for k, v in e.headers.items()}, data=e.read() or b"")
    except Exception as e:  # noqa: BLE001
        raise NetError(str(e)[:300], url=url) from e
    with resp:
        hs = {k.lower(): v for k, v in resp.headers.items()}
        if to_file:
            with open(to_file, "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
            return Response(url=url, status=resp.status, headers=hs)
        return Response(url=url, status=resp.status, headers=hs, data=resp.read())
