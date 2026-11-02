"""Plugin: what you save on YouTube as an intake channel: "Liked videos", "Watch later" and any playlists you name.

Needs your YouTube login (`memex login youtube`, see capture/cookies.py) for the private lists; public playlists work
without it. Lists are
read with yt-dlp. YouTube shows them newest first but does not say when a video was added, so each run reads from the
top until it reaches a video it has already seen; the very first run takes at most first_run_max per list instead of
your whole history. Admitted videos are captured like any other video (subtitles, else local transcription).
"""

from __future__ import annotations

import datetime
import json
import re

from memexos import i18n
from memexos.capture import cookies
from memexos.capture.fetch import PageUnavailable, ytdlp_base
from memexos.util import run

NAME = "youtube"
LISTS = {"LL": "liked", "WL": "watch later"}
ADMISSION = {
    "en": "YouTube: \"Watch later\" is an intention to learn: keep by default. \"Liked videos\" mix learning and entertainment: keep "
          "talks, tutorials, explainers, interviews and reviews with something to learn; leave out music, comedy, game playthroughs, "
          "trailers and vlogs that teach nothing. Your own playlists: follow the playlist's apparent purpose. When unsure, keep.",
    "zh": "YouTube：「稍后观看」代表想学，默认收。「赞过的视频」学习和娱乐混在一起：演讲、教程、讲解、访谈、有收获的评测就收；"
          "音乐、搞笑、游戏实况、预告片、没有知识点的 vlog 不收。你自己的播放列表：按列表的用途判断。拿不准就收。",
}


def _url(ref: str) -> str:
    return ref if ref.startswith("http") else f"https://www.youtube.com/playlist?list={ref}"


def list_entries(cfg, ref: str, limit: int) -> list[dict]:
    base, env = ytdlp_base(cfg, "https://www.youtube.com/")
    base = [a for a in base if a != "--no-playlist"]
    r = run(base + ["--flat-playlist", "-J", "--playlist-end", str(limit), _url(ref)], env=env, timeout=600)
    if r.returncode != 0:
        raise PageUnavailable(r.stderr.strip()[-300:] or "yt-dlp failed")
    data = json.loads(r.stdout or "{}")
    return [e for e in data.get("entries") or [] if e and e.get("id")]


def collect(cfg, state: dict, done: set, since_days: float) -> list[list[dict]]:
    refs = cfg.get("plugins.youtube.playlists") or list(LISTS)
    per_run = int(cfg.get("plugins.youtube.max_per_run") or 200)
    first_max = int(cfg.get("plugins.youtube.first_run_max") or 50)
    items, errors = [], []
    for ref in refs:
        seen = set(state.get(ref) or [])
        first = ref not in state
        try:
            entries = list_entries(cfg, ref, first_max if first else per_run)
        except PageUnavailable as e:
            if ref in LISTS and re.search(r"sign in|log in|login|cookies|private|does not exist", str(e), re.I):
                cookies.mark_expired(cfg, "youtube", str(e)[:200])     # the private lists exist for every logged-in account
            errors.append(i18n.t("youtube.list_failed", list=LISTS.get(ref, ref), why=str(e)[:200]))
            continue
        for e in entries:
            if e["id"] in seen:
                break                                  # newest first: everything below was read before
            key = f"youtube:{e['id']}"
            if key in done:
                continue
            items.append({"key": key, "url": f"https://www.youtube.com/watch?v={e['id']}", "list": LISTS.get(ref, ref),
                          "title": e.get("title"), "author": e.get("channel") or e.get("uploader"),
                          "duration_s": e.get("duration"), "last": datetime.date.today().isoformat()})
        state[ref] = ([e["id"] for e in entries] + list(seen))[:1000]
    if errors:
        state["_errors"] = errors
    return [items[i:i + 25] for i in range(0, len(items), 25)]
