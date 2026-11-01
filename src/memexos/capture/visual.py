"""`memex add-visual`: a visual companion for videos whose knowledge is on screen (silent demos, slides, code).

The program does the mechanical part: download the video, take candidate frames at a fixed interval plus at
sharp scene changes, and build numbered contact sheets. A vision model (role "vision") decides which frames
matter and reads them. Screenshots are the real frames; "on-screen text" and "frame notes" are the model's
reading and are labeled as such in the file, so compiling never presents them as the author's words.

  memex add-visual <transcript>        make a companion for this transcript
  memex add-visual --backlog           every thin transcript without one (daily cap visual.daily_max, 3 tries each)
  memex add-visual --check <transcript>  print whether it is thin (exit 0 = thin)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from memexos import backends, events, i18n, rawfile
from memexos.capture import fetch
from memexos.util import atomic_write, run, split_frontmatter, which

THIN_TOTAL, THIN_PER_MIN = 80, 40      # fewer than 80 characters, or under 40 per minute: the information is not in the audio
MAX_CANDIDATES = 160
MAX_TRIES = 3
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["has_visual_info", "overview", "coverage", "frames"],
          "properties": {"has_visual_info": {"type": "boolean"}, "overview": {"type": "string"}, "coverage": {"type": "string"},
                         "frames": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                               "required": ["file", "title", "visible_text", "observation"],
                                                               "properties": {k: {"type": "string"} for k in
                                                                              ("file", "title", "visible_text", "observation")}}}}}
TRANSCRIPT_HEADINGS = ("## Transcript", "## 转写")


def secs(s) -> float:
    total = 0.0
    for p in [float(x) for x in str(s).strip().strip('"').split(":") if x != ""] or [0]:
        total = total * 60 + p
    return total


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def parse(path: Path) -> tuple[dict, list[tuple[float, str]], str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    tr = next((body.split(h, 1)[1] for h in TRANSCRIPT_HEADINGS if h in body), "")
    paras = [(secs(t), x.strip()) for t, x in re.findall(r"\*\*\[([\d:]+)\]\*\*\s*(.+)", tr)]
    desc = next((body.split(h, 1)[1].split("\n## ", 1)[0].strip() for h in ("## Description", "## 视频简介") if h in body), "")
    chapters = next((body.split(h, 1)[1].split("\n## ", 1)[0].strip() for h in ("## Chapters", "## 章节") if h in body), "")
    return fm, paras, desc, chapters


def thinness(fm: dict, paras) -> tuple[int, float]:
    chars = sum(len(re.sub(r"\s", "", x)) for _, x in paras)
    minutes = max(secs(fm.get("media_duration") or fm.get("duration") or 0) / 60, 0.5)
    return chars, chars / minutes


def is_thin_file(path: Path) -> bool:
    fm, paras, _, _ = parse(path)
    if fm.get("source_type") != "video" or fm.get("visual_source"):
        return False
    chars, per_min = thinness(fm, paras)
    return chars < THIN_TOTAL or per_min < THIN_PER_MIN


def companion_of(cfg, raw: Path) -> Path | None:
    base = raw.name
    for p in cfg.paths.raw.glob("*.md"):
        head = p.read_text(encoding="utf-8", errors="ignore")[:3000]
        if "visual_source:" in head and re.search(r"^companion_to:\s*\"?" + re.escape(base) + r"\"?\s*$", head, re.M):
            return p
    return None


def video_id(url: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]{10})", url) or re.search(r"[?&]v=([\w-]{11})", url) or re.search(r"youtu\.be/([\w-]{11})", url)
    return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:10]


def at_time(url: str, plat: str, t: float) -> str:
    return f"{url}{'&' if '?' in url else '?'}t={int(t)}{'s' if plat == 'youtube' else ''}"


def download(cfg, url: str, work: Path) -> Path:
    args, env = fetch.ytdlp_base(cfg, url)
    r = run(args + ["-f", "bv*[height<=1080]/b[height<=1080]/bv*/b", "-o", str(work / "video.%(ext)s"), url], env=env, timeout=3600)
    files = [f for f in work.glob("video.*") if not f.name.endswith((".part", ".ytdl"))]
    if r.returncode != 0 or not files:
        raise RuntimeError(i18n.t("visual.download_failed", why=fetch._err(r)))
    return files[0]


def probe(video: Path) -> dict:
    r = run([which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(video)])
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"width": st.get("width"), "height": st.get("height"), "duration_seconds": float((d.get("format") or {}).get("duration") or 0)}


def candidates(video: Path, dur: float, work: Path) -> tuple[list[dict], float]:
    """A frame every `iv` seconds as a floor, plus sharp scene changes; the interval keeps candidates around 40–120."""
    iv = min(max(dur / 40, 3), 60)
    cdir = work / "frames"
    cdir.mkdir()
    vf = f"fps=2,select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{iv:.2f})+gt(scene\\,0.25)',showinfo"
    r = run([which("ffmpeg") or "ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vf", vf, "-fps_mode", "vfr", "-q:v", "3",
             str(cdir / "%05d.jpg")], timeout=3600)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    files = sorted(cdir.glob("*.jpg"))
    if not files:
        raise RuntimeError(i18n.t("visual.frames_failed", why=r.stderr.strip()[-200:]))
    pairs = list(zip(files, times + [None] * (len(files) - len(times))))
    if len(pairs) > MAX_CANDIDATES:               # constant motion (games, animation) creates many changes: thin out evenly
        step = len(pairs) / MAX_CANDIDATES
        keep = {int(i * step) for i in range(MAX_CANDIDATES)}
        for i, (f, _) in enumerate(pairs):
            if i not in keep:
                f.unlink()
        pairs = [p for i, p in enumerate(pairs) if i in keep]
    out = []
    for i, (f, t) in enumerate(pairs, 1):
        t = t or 0.0
        name = f"c{i:03d}_t{int(t // 60):02d}m{t % 60:04.1f}s.jpg"
        f.rename(cdir / name)
        out.append({"file": f"frames/{name}", "t": round(t, 2)})
    return out, iv


def contact_sheets(cands: list[dict], work: Path) -> list[str]:
    """Numbered thumbnail grids so the model can scan before opening frames. Needs Pillow; without it there are none."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return []
    (work / "sheets").mkdir()
    font = None
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            font = ImageFont.truetype(p, 22)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    W, COLS, ROWS = 420, 4, 3
    out = []
    for s in range(0, len(cands), COLS * ROWS):
        group = cands[s:s + COLS * ROWS]
        tiles = []
        for c in group:
            im = Image.open(work / c["file"]).convert("RGB")
            tiles.append((c, im.resize((W, int(im.height * W / im.width)))))
        H = max(im.height for _, im in tiles) + 30
        sheet = Image.new("RGB", (W * COLS, H * ROWS), "white")
        d = ImageDraw.Draw(sheet)
        for k, (c, im) in enumerate(tiles):
            x, y = (k % COLS) * W, (k // COLS) * H
            sheet.paste(im, (x, y + 30))
            d.rectangle([x, y, x + W - 1, y + 29], fill="black")
            d.text((x + 6, y + 3), c["file"].split("/")[-1].split("_")[0] + "  " + mmss(c["t"]), fill="yellow", font=font)
        name = f"sheets/sheet{s // (COLS * ROWS) + 1:02d}.jpg"
        sheet.save(work / name, quality=80)
        out.append(name)
    return out


def process(cfg, raw: Path) -> Path:
    raw = raw.resolve()
    fm, paras, desc, chapters = parse(raw)
    if fm.get("source_type") != "video" or not fm.get("url"):
        raise RuntimeError(i18n.t("visual.not_video"))
    have = companion_of(cfg, raw)
    if have:
        return have
    if used_today(cfg) >= int(cfg.get("visual.daily_max") or 20):
        raise RuntimeError(i18n.t("visual.daily_cap", n=cfg.get("visual.daily_max") or 20))
    chars, per_min = thinness(fm, paras)
    with tempfile.TemporaryDirectory(prefix="memex-visual-") as d, events.step(cfg.paths.state, "visual", path=raw.name):
        work = Path(d)
        events.emit(cfg.paths.state, "visual.start", path=raw.name, title=fm.get("title", ""))
        video = download(cfg, fm["url"], work)
        info = probe(video)
        cands, iv = candidates(video, info["duration_seconds"] or secs(fm.get("duration", 0)), work)
        sheets = contact_sheets(cands, work)
        (work / "transcript.md").write_text("\n\n".join(f"[{mmss(t)}] {x}" for t, x in paras) or "-", encoding="utf-8")
        prompt = i18n.prompt("visual", why=i18n.t("visual.why_thin" if chars < THIN_TOTAL else "visual.why_audio"),
                             title=fm.get("title", ""), author=fm.get("author") or "", duration=fm.get("duration", ""),
                             platform=fm.get("platform", ""), description=desc[:1500] or "-",
                             chapters=("- chapters: " + chapters[:1500]) if chapters else "", n_cands=len(cands), interval=f"{iv:.0f}",
                             sheets=i18n.t("visual.sheets_yes") if sheets else i18n.t("visual.sheets_no"))
        r = backends.structured(cfg, "vision", prompt, SCHEMA, cwd=work, workdir=work / ".run", effort=cfg.get("visual.effort") or "xhigh",
                                timeout=3600, images=[work / s for s in sheets[:20]], job="visual")
        if not r.ok:
            raise RuntimeError(i18n.t("visual.model_failed", backend=r.backend, why=r.tail[-200:]))
        path, n = write_companion(cfg, raw, fm, paras, info, video, cands, iv, r.result, r.model or r.backend, work)
    events.emit(cfg.paths.state, "visual.done", path=path.name, frames=n, has_info=r.result.get("has_visual_info"))
    return path


def write_companion(cfg, raw: Path, fm, paras, info, video, cands, iv, res, model, work) -> tuple[Path, int]:
    plat, url = fm.get("platform") or "video", fm.get("url", "")
    vid, today = video_id(url), datetime.date.today().isoformat()
    by_file = {c["file"]: c for c in cands}
    frames = [f for f in res["frames"] if f["file"] in by_file]
    rel_assets = f"assets/{plat}-{vid}/visual-{today}"
    n = 2
    while (cfg.paths.raw / rel_assets).exists():
        rel_assets = f"assets/{plat}-{vid}/visual-{today}-{n}"
        n += 1
    adir = cfg.paths.raw / rel_assets
    adir.mkdir(parents=True)
    manifest = {"source_url": url, "video_id": vid, "captured_at": datetime.datetime.now().astimezone().isoformat(),
                "transcript_file": raw.name, "video_track": {**info, "sha256": hashlib.sha256(video.read_bytes()).hexdigest()},
                "candidate_selection": {"interval_seconds": round(iv, 2), "scene_threshold": 0.25, "candidates": len(cands)},
                "model": model, "overview": res["overview"], "coverage": res["coverage"], "has_visual_info": res["has_visual_info"],
                "frames": []}
    for f in frames:
        c = by_file[f["file"]]
        name = f"frame-{int(c['t'] * 1000):07d}ms.jpg"
        shutil.copy2(work / f["file"], adir / name)
        f.update(t=c["t"], asset=f"{rel_assets}/{name}")
        manifest["frames"].append({"file": name, "seconds": c["t"], "title": f["title"], "visible_text": f["visible_text"],
                                   "observation": f["observation"], "method": "model visual reading; not author text; verify against the video"})
    atomic_write(adir / "visual-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    title = fm.get("title") or vid
    L = [f"# {title}: {i18n.t('visual.suffix_title')}", "", f"[{i18n.t('visual.transcript_link')}]({urllib.parse.quote(raw.name)}) · [{i18n.t('visual.video_link')}]({url})", "",
         i18n.t("visual.warning"), "", f"## {i18n.t('visual.overview')}", "", res["overview"].strip(), "", f"## {i18n.t('visual.scope')}", "",
         i18n.t("visual.scope_text", iv=f"{iv:.0f}", n=len(cands), k=len(frames)), "", i18n.t("visual.self_report", text=res["coverage"].strip()), "",
         f"[{i18n.t('visual.manifest')}]({urllib.parse.quote(rel_assets)}/visual-manifest.json)", "", f"## {i18n.t('visual.frames')}", ""]
    for i, f in enumerate(frames, 1):
        L += [f"### {i}. [{mmss(f['t'])}] {f['title'].strip()}", "", f"![{mmss(f['t'])}]({urllib.parse.quote(f['asset'])})", ""]
        if f["visible_text"].strip():
            L.append(i18n.t("visual.text_line", text=f["visible_text"].strip().replace("\n", " / ")))
        L.append(i18n.t("visual.note_line", text=f["observation"].strip()))
        L.append(i18n.t("visual.at_line", url=at_time(url, plat, f["t"])))
        para = next((p for p in reversed(paras) if p[0] <= f["t"]), None)
        if para and len(re.sub(r"\s", "", para[1])) >= 15:
            L.append(i18n.t("visual.same_minute", t=mmss(para[0]), text=para[1][:300]))
        L.append("")
    path = rawfile.write(cfg.paths.raw, "video", plat, f"{title}: {i18n.t('visual.suffix_title')}",
                         {"author": fm.get("author") or "", "url": url, "published": fm.get("published", ""),
                          "fetched_via": f"visual:frames+{model}", "duration": fm.get("duration", ""), "companion_to": raw.name,
                          "visual_source": "extracted_video_frames", "visual_model": model, "selected_frame_count": str(len(frames)),
                          "has_visual_info": "true" if res["has_visual_info"] else "false"},
                         "\n".join(L), vid, suffix=i18n.t("visual.suffix"))
    return path, len(frames)


def used_today(cfg) -> int:
    today = time.strftime("%Y-%m-%d")
    from memexos import events as ev
    return sum(1 for e in ev.read(cfg.paths.state, types=("visual.done",)) if e.get("ts", "").startswith(today))


def backlog(cfg, limit: int | None = None) -> int:
    from memexos import lock
    state_file = cfg.paths.ledger / "visual-state.json"
    try:
        st = json.loads(state_file.read_text())
    except (OSError, ValueError):
        st = {}
    try:
        with lock.held(cfg.paths.locks, "visual"):
            todo = [p for p in sorted(cfg.paths.raw.glob("*.md")) if is_thin_file(p) and not companion_of(cfg, p)
                    and st.get(p.name, {}).get("n", 0) < MAX_TRIES][:limit]
            ok = 0
            for p in todo:
                try:
                    process(cfg, p)
                    st.pop(p.name, None)
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    if "daily" in str(e) or "上限" in str(e):
                        break
                    rec = st.setdefault(p.name, {"n": 0})
                    rec.update(n=rec["n"] + 1, last=str(e)[-240:], ts=time.strftime("%F %T"))
                    events.emit(cfg.paths.state, "visual.failed", path=p.name, attempt=rec["n"], why=str(e)[-200:])
                atomic_write(state_file, json.dumps(st, ensure_ascii=False, indent=1))
            return ok
    except lock.Busy:
        return 0


def cmd_add_visual(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex add-visual")
    ap.add_argument("raw", nargs="?")
    ap.add_argument("--backlog", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    cfg = loader()
    if a.backlog:
        with events.run(cfg.paths.state, "visual"):
            print(i18n.t("visual.backlog_done", n=backlog(cfg, a.limit)))
        return 0
    if not a.raw:
        ap.error("a transcript path is required")
    p = Path(a.raw) if Path(a.raw).is_absolute() else (cfg.vault / a.raw if (cfg.vault / a.raw).exists() else Path(a.raw).resolve())
    if a.check:
        fm, paras, _, _ = parse(p)
        chars, per_min = thinness(fm, paras)
        print(i18n.t("visual.check", chars=chars, per_min=f"{per_min:.0f}", thin=i18n.t("visual.thin" if is_thin_file(p) else "visual.not_thin")))
        return 0 if is_thin_file(p) else 1
    with events.run(cfg.paths.state, "visual"):
        try:
            print(process(cfg, p))
            return 0
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            return 1
