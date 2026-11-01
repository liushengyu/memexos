"""Local speech-to-text with whisper.cpp, for videos without subtitles and for podcasts. Audio never leaves the machine.

Why the audio is cut into chunks: whisper decides the language from the first 30 seconds and conditions every
sentence on the previous one. A talk that opens in one language and continues in another then gets transcribed
in the wrong language, and a repetition loop, once started, never stops. Each chunk (5 minutes by default)
detects its own language and starts without previous context, so neither problem spreads past a chunk.
Afterwards, stretches longer than 2 minutes with no text are recorded as gaps (music, silence, or missed speech),
so whoever compiles the transcript knows what is missing.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

from memexos import i18n
from memexos.util import run, which

GAP_S = 120


class ASRUnavailable(Exception):
    pass


def model_path(cfg) -> Path:
    m = (cfg.get("asr.model") or "").strip()
    if not m:
        raise ASRUnavailable(i18n.t("asr.no_model"))
    p = Path(m).expanduser()
    if not p.is_file():
        raise ASRUnavailable(i18n.t("asr.model_missing", path=p))
    return p


def check(cfg) -> None:
    if (cfg.get("asr.backend") or "whisper-cpp") == "none":
        raise ASRUnavailable(i18n.t("asr.disabled"))
    for tool in ("ffmpeg", "ffprobe", cfg.get("asr.command") or "whisper-cli"):
        if not which(tool):
            raise ASRUnavailable(i18n.t("asr.missing_tool", tool=tool))
    model_path(cfg)


def duration(path: Path) -> float:
    out = run([which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _ts(s: str) -> float:
    h, m, rest = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))


def fmt_ts(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round((t - int(t)) * 1000)) % 1000:03d}"


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


def read_srt(path: Path) -> list[tuple[float, float, str]]:
    cues = []
    if not path.exists():
        return cues
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore").strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        tl = next((l for l in lines if "-->" in l), None)
        if not tl:
            continue
        text = " ".join(lines[lines.index(tl) + 1:]).strip()
        if text:
            a, b = tl.split("-->")
            cues.append((_ts(a), _ts(b), text))
    return cues


def collapse_loops(cues, keep: int = 2):
    """The same line many times in a row is a hallucination loop: keep the first two."""
    out, n = [], 0
    for c in cues:
        n = n + 1 if out and c[2] == out[-1][2] else 1
        if n <= keep:
            out.append(c)
    return out


def transcribe(cfg, src: Path, outdir: Path) -> tuple[list, dict]:
    """Returns (cues [(start, end, text)], info {duration, langs, gaps, gaps_text, model}); also writes asr.srt and asr.json."""
    check(cfg)
    model = model_path(cfg)
    chunk = int(cfg.get("asr.chunk_seconds") or 300)
    whisper = which(cfg.get("asr.command") or "whisper-cli")
    ffmpeg = which("ffmpeg")
    outdir.mkdir(parents=True, exist_ok=True)
    wav = outdir / "audio.wav"
    r = run([ffmpeg, "-loglevel", "error", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", str(wav)], timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(i18n.t("asr.ffmpeg_failed", why=r.stderr.strip()[-200:]))
    total = duration(wav)
    cues, langs, start = [], collections.Counter(), 0.0
    while start < total - 1:
        part = outdir / "part.wav"
        run([ffmpeg, "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", str(chunk), "-i", str(wav), str(part)], timeout=600)
        base = outdir / "part"
        r = run([whisper, "-m", str(model), "-f", str(part), "-l", "auto", "-mc", "0", "-osrt", "-of", str(base)], timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(i18n.t("asr.failed", at=mmss(start), why=(r.stderr.strip().splitlines() or ["?"])[-1][-200:]))
        m = re.search(r"auto-detected language: (\w+)", r.stderr)
        if m:
            langs[m.group(1)] += 1
        cues += [(a + start, b + start, t) for a, b, t in collapse_loops(read_srt(Path(str(base) + ".srt")))]
        start += chunk
    gaps, last = [], 0.0
    for a, b, _ in cues:
        if a - last > GAP_S:
            gaps.append((last, a))
        last = max(last, b)
    if total - last > GAP_S:
        gaps.append((last, total))
    (outdir / "asr.srt").write_text("".join(f"{i}\n{fmt_ts(a)} --> {fmt_ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1)),
                                    encoding="utf-8")
    info = {"duration": round(total, 1), "langs": dict(langs), "chunks": int(-(-total // chunk)) if total else 0,
            "gaps": [[round(a, 1), round(b, 1)] for a, b in gaps], "gaps_text": ", ".join(f"{mmss(a)}–{mmss(b)}" for a, b in gaps),
            "model": model.stem.removeprefix("ggml-")}
    (outdir / "asr.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    for p in (wav, outdir / "part.wav", outdir / "part.srt"):
        p.unlink(missing_ok=True)
    return cues, info
