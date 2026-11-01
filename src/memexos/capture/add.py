"""`memex add-url <link>`: put a link into raw/ according to its kind.

  video sites (anything yt-dlp handles)   → memex add-video (subtitles, else local transcription)
  plugin links (e.g. X posts)             → the plugin's capture (only with the plugin installed)
  podcast episode pages / audio files     → downloaded and transcribed locally; audio never goes to a cloud service
  anything else                           → the page's main text (PDFs are converted to text)
Before writing, secrets known to this machine and common key formats are replaced.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

from memexos import events, i18n, plugins, rawfile
from memexos.capture import asr, fetch
from memexos.capture.redact import known_secrets, scrub

VIDEO_HOSTS = re.compile(r"(?:youtube\.com|youtu\.be|bilibili\.com|b23\.tv|vimeo\.com|twitch\.tv|ted\.com/talks|nicovideo\.jp|"
                         r"dailymotion\.com|douyin\.com|ixigua\.com)", re.I)
AUDIO = re.compile(r"\.(m4a|mp3|aac|wav|ogg|opus)(\?|$)", re.I)


def writer(cfg):
    secrets = known_secrets(cfg)

    def write(kind, platform, title, fallback, fields, body):
        body, hits = scrub(body, secrets)
        if hits:
            events.emit(cfg.paths.state, "capture.redacted", count=hits)
        return rawfile.write(cfg.paths.raw, kind, platform, title, fields, body, fallback)
    return write


def moved(url: str, final: str) -> str:
    """The final address when it is a different page. A redirect to a site's home page would otherwise be stored as if
    it were the requested article."""
    if not final:
        return ""
    a, b = urllib.parse.urlparse(url), urllib.parse.urlparse(final)
    same = a.netloc.removeprefix("www.") == b.netloc.removeprefix("www.") and a.path.rstrip("/") == b.path.rstrip("/")
    return "" if same else final


def redirected_to(cfg, url: str) -> str:
    """Where the server redirects this address (asked separately when a reader service fetched the page)."""
    try:
        return moved(url, fetch.get(cfg, fetch.fetch_target(url), timeout=20).url)
    except Exception:  # noqa: BLE001 — unknown is fine: nothing is flagged
        return ""


def add_web(cfg, url: str, title: str, via: str) -> Path:
    text, how, final = fetch.web_page(cfg, url)
    m = re.match(r"Title:\s*(.+)", text)
    t = title or (m.group(1).strip() if m else urllib.parse.urlparse(url).netloc)
    pub = re.search(r"^Published Time:\s*(.+)$", text, re.M)
    body = re.sub(r"^(Title|URL Source|Published Time):.*\n", "", text, flags=re.M).replace("Markdown Content:\n", "", 1)
    moved_to = moved(url, final) if final else redirected_to(cfg, url)
    warn = i18n.t("capture.redirected", url=moved_to) + "\n\n" if moved_to else ""
    return writer(cfg)("web", rawfile.site_name(url), t, rawfile.url_id(url),
                       {"url": url, "fetched_url": moved_to, "published": pub.group(1).strip() if pub else "", "fetched_via": how, "via": via},
                       f"# {t}\n\n{i18n.t('capture.web_note', how=how, url=url)}\n\n{warn}{body.strip()}")


def podcast_audio(cfg, url: str) -> tuple[str, str, str]:
    """Audio URL, title and description from an episode page (Open Graph tags), or the URL itself for audio files."""
    if AUDIO.search(url):
        return url, "", ""
    page = fetch.get(cfg, url).text()
    audio = re.search(r'<meta[^>]+property="og:audio"[^>]+content="([^"]+)"', page) or re.search(r'"(https://[^"]+?\.(?:m4a|mp3)[^"]*)"', page)
    if not audio:
        raise fetch.PageUnavailable(i18n.t("capture.no_audio"))
    t = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page)
    desc = re.search(r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]+)"', page)
    return html.unescape(audio.group(1)), html.unescape(t.group(1)) if t else "", html.unescape(desc.group(1)) if desc else ""


def add_podcast(cfg, url: str, title: str, via: str) -> Path:
    asr.check(cfg)
    audio, t, desc = podcast_audio(cfg, url)
    t = title or t or Path(urllib.parse.urlparse(audio).path).name
    with tempfile.TemporaryDirectory(prefix="memex-podcast-") as d:
        src = Path(d) / "audio.bin"
        fetch.get(cfg, audio, timeout=900, to_file=src)
        cues, info = asr.transcribe(cfg, src, Path(d) / "asr")
    from memexos.capture.video import paragraphs
    pairs = [(a, x) for a, _, x in cues]
    sep = "" if re.search(r"[぀-ヿ㐀-鿿]", "".join(x for _, x in pairs[:50])) else " "
    gaps = i18n.t("video.note_gaps", gaps=info["gaps_text"]) + "\n\n" if info.get("gaps_text") else ""
    body = (f"# {t}\n\n{i18n.t('capture.podcast_note')}\n\n{gaps}" + (f"## {i18n.t('capture.episode')}\n\n{desc}\n\n" if desc else "")
            + f"## {i18n.t('video.transcript')}\n\n" + "\n\n".join(f"**[{asr.mmss(s)}]** " + sep.join(xs) for s, xs in paragraphs(pairs)))
    return writer(cfg)("podcast", rawfile.site_name(url), t, rawfile.url_id(url),
                       {"url": url, "audio_url": audio, "duration": asr.mmss(info["duration"]),
                        "fetched_via": f"asr:whisper-{info.get('model', 'local')}", "asr_gaps": info.get("gaps_text", ""), "via": via}, body)


def add(cfg, url: str, title: str = "", via: str = "") -> Path:
    fetch.public_url(cfg, url)
    mod = plugins.capture_handler(cfg, url)
    if mod:
        return mod.capture(cfg, url, title, via, writer(cfg))
    if VIDEO_HOSTS.search(url):
        from memexos.capture.video import add_video
        return add_video(cfg, url)
    if AUDIO.search(url) or re.search(r"/episode/|/episodes/", url):
        try:
            return add_podcast(cfg, url, title, via)
        except fetch.PageUnavailable:
            if AUDIO.search(url):
                raise
    return add_web(cfg, url, title, via)


def cmd_add_url(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex add-url")
    ap.add_argument("url")
    ap.add_argument("--title", default="")
    ap.add_argument("--via", default="", help="the channel it came through, e.g. rss:<feed>; recorded in the header")
    a = ap.parse_args(argv)
    cfg = loader()
    if not (cfg.vault / "raw").is_dir():
        print(i18n.t("cli.no_vault", path=cfg.vault), file=sys.stderr)
        return 1
    with events.run(cfg.paths.state, "add-url"):
        try:
            with events.step(cfg.paths.state, "capture", url=a.url):
                p = add(cfg, a.url, a.title, a.via)
        except (fetch.PageUnavailable, asr.ASRUnavailable, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            return 1
        events.emit(cfg.paths.state, "capture.done", path=p.relative_to(cfg.vault).as_posix(), url=a.url)
    print(p)
    print(i18n.t("capture.next"))
    return 0
