"""Naming and header (frontmatter) rules for files in raw/. Every program that writes to raw/ goes through here.

File name: <capture date>-<type>-<source>-<short title>.md
  capture date  the day it entered raw/ (the author's own date goes into `published`)
  type          video / web / tweet / podcast / session / doc / feedback
  source        video: bilibili, youtube, …; web: the site name (docs.python.org → python); tweet: x;
                podcast: platform or site; session: claude, codex; doc: the project folder it came from (else "local");
                feedback: local-check / human / claude / codex
  short title   letters and digits of the original title in any script, everything else becomes "-", at most 40
                characters; when there is no usable title (empty, "untitled", a bot-check page, a URL) the source's own
                id is used instead (video id, tweet id, session id, …)
  duplicates    a different body gets -2, -3; an identical body is not written again (callers compare first)

Header, required (nothing is written without them): source_type, platform, title, captured, fetched_via, and url or
original (or sessions / provided_by for feedback). When known: author, published, coverage (which part was captured
when only part was), via (the channel it came through), plus type-specific fields.

Feedback (raw/feedback/) is feedback on existing knowledge, not a new source: it targets one sentence on one page
(`pages` and `claim` are required) and is used only to correct that sentence (`memex correct`). The body holds the
facts and the evidence, never "how the page should be rewritten".

Why one module: when every collector guessed its own names, dates in names meant different things, titles kept
brackets and question marks so readers disagreed about where a name ended, and header fields differed by type.
Existing files are never renamed (raw/ is append-only); readers accept the older field names too.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import urllib.parse
from pathlib import Path

from memexos.util import atomic_write, render_frontmatter, split_frontmatter

TYPES = ("video", "web", "tweet", "podcast", "session", "doc", "feedback")
REQUIRED = ("source_type", "platform", "title", "captured", "fetched_via")
ORDER = ("source_type", "platform", "title", "author", "url", "original", "published", "captured", "fetched_via", "coverage", "via")
BAD_TITLE = re.compile(r"^\s*(untitled|无标题|-+|just a moment|checking your browser|human verification|attention required|"
                       r"access denied|url source|title:|https?://|www\.)", re.I)
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac"}
WORD_CHARS = re.compile(r"[^\w]+|_+", re.U)


def short_title(title: str, fallback: str, n: int = 40) -> str:
    t = (title or "").strip()
    if not t or BAD_TITLE.search(t):
        t = fallback or ""
    s = WORD_CHARS.sub("-", t).strip("-")[:n].strip("-")
    if not s:
        s = re.sub(r"[^0-9A-Za-z]+", "-", fallback or "").strip("-")[:n] or hashlib.sha1(t.encode()).hexdigest()[:10]
    return s


def site_name(url: str) -> str:
    """URL → site name: docs.python.org → python, www.bbc.co.uk → bbc."""
    host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL:
        name = parts[-3]
    elif len(parts) >= 2:
        name = parts[-2]
    else:
        name = parts[0] if parts else "web"
    return re.sub(r"[^0-9a-z]+", "-", name).strip("-") or "web"


def url_id(url: str) -> str:
    """Fallback id for a URL without a usable title: the last path segment, else a short hash."""
    path = urllib.parse.urlparse(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1] if path else ""
    return last or hashlib.sha1(url.encode()).hexdigest()[:10]


def path_for(raw_dir: str | os.PathLike, kind: str, platform: str, title: str, fallback: str,
             date: str | None = None, suffix: str = "", ext: str = ".md") -> Path:
    if kind not in TYPES:
        raise ValueError(f"raw type must be one of {'/'.join(TYPES)}, got {kind}")
    raw_dir = Path(raw_dir)
    if kind == "feedback" and raw_dir.name != "feedback":
        raw_dir = raw_dir / "feedback"
    raw_dir.mkdir(parents=True, exist_ok=True)
    date = date or datetime.date.today().isoformat()
    plat = re.sub(r"[^0-9a-z]+", "-", (platform or "").lower()).strip("-") or "unknown"
    stem = f"{date}-{kind}-{plat}-{short_title(title, fallback)}{('-' + suffix) if suffix else ''}"
    path, n = raw_dir / (stem + ext), 2
    while path.exists():
        path = raw_dir / f"{stem}-{n}{ext}"
        n += 1
    return path


def check(fields: dict) -> None:
    missing = [k for k in REQUIRED if not str(fields.get(k) or "").strip()]
    if not (fields.get("url") or fields.get("original") or fields.get("sessions") or fields.get("provided_by")):
        missing.append("url or original")
    if fields.get("source_type") == "feedback":
        if not fields.get("pages"):
            missing.append("pages (which pages it targets)")
        if not str(fields.get("claim") or "").strip():
            missing.append("claim (the sentence it targets)")
    if fields.get("source_type") not in TYPES:
        missing.append(f"source_type ({fields.get('source_type')} is not one of {'/'.join(TYPES)})")
    if missing:
        raise ValueError("raw header is missing: " + ", ".join(missing))


def render(fields: dict) -> str:
    check(fields)
    return render_frontmatter(fields, ORDER)


def body_hash(text: str) -> str:
    """Hash of the body without the header: the same content captured twice compares equal."""
    return hashlib.sha256(split_frontmatter(text)[1].strip().encode("utf-8")).hexdigest()


def write(raw_dir: str | os.PathLike, kind: str, platform: str, title: str, fields: dict, body: str, fallback: str,
          date: str | None = None, suffix: str = "") -> Path:
    """Write one raw file by the rules above; returns its path. Hidden temp file + rename, so readers never see half a file."""
    fields = {"source_type": kind, "platform": platform, "title": title,
              "captured": date or datetime.date.today().isoformat(), **{k: v for k, v in fields.items() if v not in (None, "")}}
    head = render(fields)
    path = path_for(raw_dir, kind, platform, title, fallback, date=date, suffix=suffix)
    atomic_write(path, head + body.strip() + "\n")
    return path
