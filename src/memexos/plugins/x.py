"""Plugin: X (Twitter). Bookmarks and likes as an intake channel; tweets (with the author's thread and long-form
articles) for `memex add-url`.

Needs twitter-cli (https://github.com/jackwener/twitter-cli, Apache-2.0) and a logged-in browser
(plugins.x.browser). Bookmarks are a strong "keep this" signal; likes are weaker and are screened harder.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from memexos import i18n, rawfile
from memexos.capture import cookies
from memexos.capture.fetch import PageUnavailable, proxy_for
from memexos.util import proxy_env, run, which

NAME = "x"
ADMISSION = {
    "en": "X bookmarks: keep by default (bookmarking is itself a 'worth keeping' signal); leave out pure entertainment, memes and "
          "pure emotion. A tweet is often one sentence plus a link: open the link first (memex fetch web) and decide what to keep "
          "(the tweet, the linked article / video / paper, or both) in capture_urls. Likes are weaker (often just 'seen'): keep "
          "knowledge, methods and tools; leave arguments, jokes and old likes.",
    "zh": "X 书签：默认收（加书签本身就是“值得留”的信号），排除纯娱乐、梗图、纯情绪。推文常常只是“一句话 + 链接”：先用 memex fetch web 打开链接，"
          "再决定收什么（推文本身、它指向的文章 / 视频 / 论文，或两者都收），写进 capture_urls。点赞比书签弱（常常只是“看到了”）："
          "知识、方法、工具类收，观点争吵、玩笑、多年前的旧赞不收。",
}


def _env(cfg) -> dict:
    env = proxy_env(None, proxy_for(cfg, "https://x.com"))
    cf = cookies.file_for(cfg, "x")
    if cf:
        jar = {l.split("\t")[5]: l.split("\t")[6] for l in cf.read_text(errors="ignore").splitlines()
               if not l.startswith("#") and l.count("\t") >= 6}
        if jar.get("auth_token") and jar.get("ct0"):
            env.update(TWITTER_AUTH_TOKEN=jar["auth_token"], TWITTER_CT0=jar["ct0"])
    return env


def twitter(cfg, *args: str):
    exe = which("twitter")
    if not exe:
        raise PageUnavailable(i18n.t("x.no_cli"))
    r = run([exe, *args, "--json"], env=_env(cfg), timeout=180)
    if r.returncode != 0:
        raise PageUnavailable(f"twitter {' '.join(args[:2])}: {(r.stderr.strip().splitlines() or ['?'])[-1][-200:]}")
    d = json.loads(r.stdout or "{}")
    if isinstance(d, dict) and not d.get("ok", True):
        raise PageUnavailable(f"twitter {' '.join(args[:2])}: {d.get('error')}")
    return d.get("data") if isinstance(d, dict) and "data" in d else d


def fmt(t: dict) -> str:
    a = t.get("author") or {}
    urls = [u.get("expanded") or u.get("expandedUrl") or u.get("url") if isinstance(u, dict) else u for u in t.get("urls") or []]
    media = [m.get("url") or m.get("mediaUrl") if isinstance(m, dict) else m for m in t.get("media") or []]
    return "\n".join(x for x in [
        f"@{a.get('screenName', '')} ({a.get('name', '')}) · {t.get('createdAtISO') or t.get('createdAt', '')}",
        f"https://x.com/{a.get('screenName', 'i')}/status/{t.get('id', '')}", (t.get("text") or "").strip(),
        ("links: " + " ".join(u for u in urls if u)) if urls else "", ("media: " + " ".join(m for m in media if m)) if media else ""] if x)


def _cli_python() -> str | None:
    exe = which("twitter")
    if not exe:
        return None
    try:
        first = open(exe, "rb").readline().decode(errors="ignore")
    except OSError:
        return None
    return first[2:].strip().split()[0] if first.startswith("#!") and "python" in first else None


def article(cfg, tid: str) -> tuple[str, str] | None:
    """Long-form X article attached to a tweet: (title, text). The patched reader keeps code blocks and dividers that
    twitter-cli's own converter drops; when it cannot run, twitter-cli's output is used as is."""
    d = None
    py = _cli_python()
    if py:
        r = run([py, str(Path(__file__).with_name("x_article.py")), str(tid)], env=_env(cfg), timeout=180)
        try:
            j = json.loads(r.stdout or "{}")
            d = j.get("data") if j.get("ok", True) else None
        except ValueError:
            d = None
    try:
        d = d or twitter(cfg, "article", str(tid))
    except PageUnavailable:
        return None
    return (d.get("articleTitle") or "", d.get("articleText") or "") if isinstance(d, dict) and d.get("articleText") else None


def has_article(t: dict) -> bool:
    return any("/i/article/" in str(u.get("expanded") or u.get("expandedUrl") or u.get("url") if isinstance(u, dict) else u)
               for u in t.get("urls") or [])


def f_tweet(cfg, args, n, as_json):
    tid = re.search(r"(\d{8,})", args[0] if args else "")
    if not tid:
        raise PageUnavailable(i18n.t("x.need_id"))
    d = twitter(cfg, "tweet", tid.group(1))
    items = [d["tweet"]] + list(d.get("replies") or [])[:5] if isinstance(d, dict) and d.get("tweet") else \
        (d if isinstance(d, list) else [d])
    out = "\n\n---\n\n".join(fmt(t) for t in items[:6] if isinstance(t, dict))
    if items and isinstance(items[0], dict) and has_article(items[0]):
        art = article(cfg, tid.group(1))
        if art:
            out += f"\n\n=== {art[0]} ===\n\n{art[1]}"
    return out


def f_bookmarks(cfg, args, n, as_json):
    items = twitter(cfg, "bookmarks", "-n", str(n)) or []
    return json.dumps(items, ensure_ascii=False) if as_json else "\n\n---\n\n".join(fmt(t) for t in items)


def f_likes(cfg, args, n, as_json):
    me = twitter(cfg, "whoami") or {}
    name = me.get("screenName") or me.get("username") or (me.get("user") or {}).get("screenName")
    if not name:
        raise PageUnavailable(i18n.t("x.no_account"))
    items = twitter(cfg, "likes", name, "-n", str(n)) or []
    return json.dumps(items, ensure_ascii=False) if as_json else "\n\n---\n\n".join(fmt(t) for t in items)


FETCH = {"tweet": f_tweet, "x-bookmarks": f_bookmarks, "x-likes": f_likes}


def handles(url: str) -> bool:
    return bool(re.search(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/\d+", url))


def capture(cfg, url: str, title: str, via: str, write) -> Path:
    """Write the tweet, the author's own continuation in the replies, and an attached long-form article."""
    tid = re.search(r"(\d{8,})", url).group(1)
    d = twitter(cfg, "tweet", tid)
    main = d.get("tweet") if isinstance(d, dict) and d.get("tweet") else (d[0] if isinstance(d, list) and d else d)
    replies = (d.get("replies") or []) if isinstance(d, dict) else (d[1:] if isinstance(d, list) else [])
    author = (main.get("author") or {}).get("screenName", "")
    thread = [r for r in replies if (r.get("author") or {}).get("screenName") == author]
    parts = [fmt(main)] + [fmt(r) for r in thread]
    first = (main.get("text") or "").strip().splitlines()[0][:60] if main.get("text") else tid
    art = article(cfg, tid) if has_article(main) else None
    if art:
        parts.append(f"## {art[0]}\n\n{art[1].strip()}")
        first = art[0] or first
    t = title or f"@{author}: {first}"
    note = i18n.t("x.capture_note", thread=len(thread), article=i18n.t("x.with_article") if art else "")
    return write("tweet", "x", t, f"{author}-{tid}",
                 {"url": f"https://x.com/{author}/status/{tid}", "author": f"@{author}",
                  "published": main.get("createdAtISO") or main.get("createdAt", ""), "fetched_via": "twitter-cli", "via": via},
                 f"# {t}\n\n> {note}\n\n" + "\n\n---\n\n".join(parts))


def collect(cfg, state: dict, done: set, since_days: float) -> list[list[dict]]:
    """Bookmarks (and likes, when plugins.x.likes) not seen before, in batches of 20."""
    tweets, kinds = [], {}
    sources = [("bookmarks", f_bookmarks)] + ([("likes", f_likes)] if cfg.get("plugins.x.likes", True) else [])
    errors = []
    for label, fn in sources:
        try:
            got = json.loads(fn(cfg, [], 300, True) or "[]")
        except (PageUnavailable, ValueError) as e:
            errors.append(f"{label}: {e}")
            continue
        for t in got:
            if t.get("id") not in kinds:
                tweets.append(t)
            kinds.setdefault(t.get("id"), []).append(label)
    items = []
    for t in tweets:
        key = f"x:{t.get('id')}"
        if key in done:
            continue
        a = t.get("author") or {}
        urls = [u.get("expanded") or u.get("expandedUrl") or u.get("url") if isinstance(u, dict) else u for u in t.get("urls") or []]
        text = (t.get("text") or "").strip()
        items.append({"key": key, "url": f"https://x.com/{a.get('screenName', 'i')}/status/{t.get('id')}", "author": f"@{a.get('screenName', '')}",
                      "title": text.splitlines()[0][:80] if text else "", "text": text[:2000], "links": [u for u in urls if u],
                      "media": len(t.get("media") or []), "signal": ", ".join(kinds.get(t.get("id"), [])),
                      "last": (t.get("createdAtISO") or "")[:10]})
    state["_errors"] = errors
    return [items[i:i + 20] for i in range(0, len(items), 20)]
