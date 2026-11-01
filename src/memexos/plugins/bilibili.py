"""Plugin: Bilibili favorites as an intake channel (new videos in your favorite folders since the last run).

Needs a logged-in browser (plugins.bilibili.browser). Admitted videos are captured like any other video
(`memex add-video`: subtitles, else local transcription). Bilibili rejects some overseas proxies, so the
default network route for it is direct (plugins.bilibili.proxy = "direct").
"""

from __future__ import annotations

import datetime
import json
import time
import urllib.request

from memexos import i18n
from memexos.capture import cookies
from memexos.capture.fetch import UA, PageUnavailable, opener, proxy_for
from memexos.util import proxy_env, run, which

NAME = "bilibili"
ADMISSION = {
    "en": "Bilibili favorites: keep by default (favorites are mostly skills and knowledge). Leave out only pure entertainment "
          "(music videos, comedy, game playthroughs, film edits, life vlogs) that is not meant to teach anything. When unsure, keep.",
    "zh": "B 站收藏：默认收（收藏的基本都是技能、知识类）。只把纯娱乐（音乐 MV、搞笑、游戏实况、影视剪辑、生活 vlog 等不以传授知识或技能为目的的内容）排除。拿不准就收。",
}


def _get(cfg, url: str, cookie: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com", "Cookie": cookie})
    return json.load(opener(proxy_for(cfg, "https://www.bilibili.com")).open(req, timeout=20))


def _jar(cfg) -> dict:
    cf = cookies.file_for(cfg, "bilibili")
    if not cf:
        return {}
    return {p[5]: p[6] for p in (l.split("\t") for l in cf.read_text(errors="ignore").splitlines() if not l.startswith("#"))
            if len(p) >= 7 and "bilibili" in p[0]}


def collect(cfg, state: dict, done: set, since_days: float) -> list[list[dict]]:
    jar = _jar(cfg)
    mid = jar.get("DedeUserID")
    if not mid or not jar.get("SESSDATA"):
        state["_errors"] = [i18n.t("bilibili.not_logged_in")]
        return []
    cookie = "; ".join(f"{k}={v}" for k, v in jar.items() if (k + v).isascii())
    folders = (_get(cfg, f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={mid}", cookie).get("data") or {}).get("list") or []
    # The look-back floor is fixed on the first run and never moves earlier, so a folder without new items
    # is not re-read from the start next time.
    floor = state.setdefault("_floor", time.time() - since_days * 86400)
    items = []
    for f in folders:
        last = max(state.get(str(f["id"]), 0), floor)
        newest = last
        for pn in range(1, 50):
            d = _get(cfg, f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={f['id']}&pn={pn}&ps=20&order=mtime&platform=web", cookie)
            medias = (d.get("data") or {}).get("medias") or []
            stop = False
            for m in medias:
                if m.get("fav_time", 0) <= last:
                    stop = True
                    break
                newest = max(newest, m.get("fav_time", 0))
                if m.get("type") != 2 or m.get("attr", 0) & 1:       # videos only; skip removed ones
                    continue
                key = f"bilibili:{m['bvid']}"
                if key in done:
                    continue
                items.append({"key": key, "url": f"https://www.bilibili.com/video/{m['bvid']}", "folder": f["title"],
                              "title": m.get("title"), "intro": (m.get("intro") or "")[:600], "author": (m.get("upper") or {}).get("name"),
                              "duration_s": m.get("duration"),
                              "last": datetime.datetime.fromtimestamp(m.get("fav_time", 0)).date().isoformat()})
            if stop or not (d.get("data") or {}).get("has_more"):
                break
        state[str(f["id"])] = newest
    return [items[i:i + 25] for i in range(0, len(items), 25)]


def f_search(cfg, args, n, as_json):
    exe = which("bili")
    if not exe:
        raise PageUnavailable(i18n.t("bilibili.no_cli"))
    r = run([exe, "search", " ".join(args), "--type", "video", "-n", str(n)], env=proxy_env(None, proxy_for(cfg, "https://www.bilibili.com")),
            timeout=180)
    if r.returncode != 0:
        raise PageUnavailable(r.stderr.strip()[-200:])
    return r.stdout


FETCH = {"search-bilibili": f_search}
