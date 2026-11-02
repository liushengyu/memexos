"""`memex add-video <url>`: a video becomes a transcript in raw/.

Human subtitles first, then the platform's automatic captions, else the audio is downloaded and transcribed
locally (memexos.capture.asr). The transcript is grouped into about one paragraph per minute with timestamps so
claims can be checked against the video. Partial captures are stated in the file: some platforms serve only a
preview of paid or members-only videos without an error, and that must never pass for the whole video.
When the transcript is too thin (no narration; the information is on screen) a visual companion is made.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from memexos import events, i18n, rawfile
from memexos.capture import asr, fetch
from memexos.capture.redact import known_secrets, scrub
from memexos.util import run


def _cues_from_srt(path: Path) -> list[tuple[float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        tl = next((l for l in lines if "-->" in l), None)
        if not tl:
            continue
        text = re.sub(r"<[^>]+>", "", " ".join(lines[lines.index(tl) + 1:])).strip()
        if text:
            cues.append((asr._ts(tl.split("-->")[0]), text))
    dedup: list[tuple[float, str]] = []          # rolling auto-captions repeat lines; keep the longest version
    for t, x in cues:
        if dedup and (x == dedup[-1][1] or x in dedup[-1][1]):
            continue
        if dedup and dedup[-1][1] in x:
            dedup[-1] = (dedup[-1][0], x)
            continue
        dedup.append((t, x))
    return dedup


def paragraphs(cues: list[tuple[float, str]], every: float = 60) -> list[tuple[float, list[str]]]:
    paras, cur, start = [], [], None
    for t, x in cues:
        start = t if start is None else start
        cur.append(x)
        if t - start >= every:
            paras.append((start, cur))
            cur, start = [], None
    if cur:
        paras.append((start, cur))
    return paras


def add_video(cfg, url: str) -> Path:
    fetch.public_url(cfg, url)
    args, env = fetch.ytdlp_base(cfg, url)
    with tempfile.TemporaryDirectory(prefix="memex-video-") as d:
        tmp = Path(d)
        r = run(args + ["--dump-single-json", "--skip-download", url], env=env, timeout=300)
        if r.returncode != 0:
            raise fetch.PageUnavailable(i18n.t("video.meta_failed", why=fetch._err(r)))
        meta = json.loads(r.stdout)
        run(args + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", fetch.SUB_LANGS, "--convert-subs", "srt",
                    "-o", str(tmp / "sub"), url], env=env, timeout=300)
        manual = set((meta.get("subtitles") or {}).keys())
        subs = sorted(tmp.glob("sub*.srt"), key=lambda f: (0 if f.name.rsplit(".", 2)[-2] in manual else 1))
        info: dict = {}
        if subs:
            lang = subs[0].name.rsplit(".", 2)[-2]
            how = ("subtitles-human:" if lang in manual else "subtitles-auto:") + lang
            cues = _cues_from_srt(subs[0])
        else:
            asr.check(cfg)
            r = run(args + ["-f", "bestaudio/best", "-o", str(tmp / "audio.%(ext)s"), url], env=env, timeout=7200)
            audio = next(iter(tmp.glob("audio.*")), None)
            if r.returncode != 0 or not audio:
                raise fetch.PageUnavailable(i18n.t("video.audio_failed", why=fetch._err(r)))
            acues, info = asr.transcribe(cfg, audio, tmp / "asr")
            cues = [(a, t) for a, _, t in acues]
            how = f"asr:whisper-{info.get('model', 'local')}"
        path = write_transcript(cfg, meta, url, cues, how, info)
    events.emit(cfg.paths.state, "capture.video", path=path.relative_to(cfg.vault).as_posix(), how=how)
    try:
        from memexos.capture import visual
        if visual.is_thin_file(path) and not cfg.get("capture.no_visual"):
            visual.process(cfg, path)
    except Exception as e:  # noqa: BLE001 — the transcript is kept; the visual pass is retried by `memex add-visual --backlog`
        events.emit(cfg.paths.state, "visual.failed", path=path.name, why=str(e)[-200:])
    return path


def write_transcript(cfg, meta: dict, url: str, cues: list[tuple[float, str]], how: str, info: dict) -> Path:
    title = meta.get("title") or meta.get("id") or "untitled"
    plat = (meta.get("extractor_key") or meta.get("extractor") or "video").lower()
    plat = re.sub(r"[^a-z0-9]+", "", plat) or "video"
    up = meta.get("upload_date") or ""
    up = f"{up[:4]}-{up[4:6]}-{up[6:]}" if len(up) == 8 else up
    dur = int(meta.get("duration") or 0)
    media = float(info.get("duration") or 0)
    coverage, notes = "", []
    if media and dur and media < dur * 0.9:
        coverage = i18n.t("video.coverage_partial", got=asr.mmss(media), total=asr.mmss(dur))
        notes.append(i18n.t("video.note_partial", got=asr.mmss(media), total=asr.mmss(dur)))
    elif dur and cues and cues[-1][0] < dur - 180 and cues[-1][0] < dur * 0.8:
        coverage = i18n.t("video.coverage_subs", got=asr.mmss(cues[-1][0]), total=asr.mmss(dur))
        notes.append(i18n.t("video.note_subs", got=asr.mmss(cues[-1][0]), total=asr.mmss(dur)))
    if info.get("gaps_text"):
        notes.append(i18n.t("video.note_gaps", gaps=info["gaps_text"]))
    sep = "" if re.search(r"[぀-ヿ㐀-鿿]", "".join(x for _, x in cues[:50])) else " "
    body = [f"# {title}\n", i18n.t("video.note_transcript"), *notes, ""]
    desc = (meta.get("description") or "").strip()
    if desc:
        body += [f"## {i18n.t('video.description')}\n", desc[:3000], ""]
    if meta.get("chapters"):
        body += [f"## {i18n.t('video.chapters')}\n"] + [f"- `{asr.mmss(c.get('start_time', 0))}` {c.get('title', '')}" for c in meta["chapters"]] + [""]
    body += [f"## {i18n.t('video.transcript')}\n"] + [f"**[{asr.mmss(s)}]** " + sep.join(xs) + "\n" for s, xs in paragraphs(cues)]
    text, _ = scrub("\n".join(body), known_secrets(cfg))
    return rawfile.write(cfg.paths.raw, "video", plat, title,
                         {"author": meta.get("uploader") or meta.get("channel") or "", "url": meta.get("webpage_url") or url,
                          "published": up, "fetched_via": how, "coverage": coverage, "duration": asr.mmss(dur),
                          "media_duration": asr.mmss(media) if coverage and media else "", "asr_gaps": info.get("gaps_text", "")},
                         text, meta.get("id") or rawfile.url_id(url))


def cmd_add_video(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex add-video")
    ap.add_argument("url")
    a = ap.parse_args(argv)
    cfg = loader()
    with events.run(cfg.paths.state, "add-video"):
        try:
            with events.step(cfg.paths.state, "capture", url=a.url):
                p = add_video(cfg, a.url)
        except (fetch.PageUnavailable, asr.ASRUnavailable, RuntimeError) as e:
            print(str(e), file=sys.stderr)
            return 1
    print(p)
    print(i18n.t("capture.next"))
    return 0
