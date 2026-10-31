"""`memex doctor`: what is installed, configured and reachable, with install hints for what is missing."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from memexos import backends, i18n, judge
from memexos.search import fts, qmd
from memexos.util import which

HINTS = {
    "git": "https://git-scm.com/downloads",
    "claude": "https://docs.anthropic.com/en/docs/claude-code (npm install -g @anthropic-ai/claude-code)",
    "codex": "https://github.com/openai/codex (npm install -g @openai/codex)",
    "qmd": "https://github.com/tobi/qmd (npm install -g @tobilu/qmd), then memex reindex",
    "node": "https://nodejs.org (needed for qmd's reranker)",
    "yt-dlp": "https://github.com/yt-dlp/yt-dlp (brew/pipx install yt-dlp)",
    "ffmpeg": "https://ffmpeg.org (brew install ffmpeg / apt install ffmpeg)",
    "whisper-cli": "https://github.com/ggml-org/whisper.cpp (brew install whisper-cpp), then set asr.model to a ggml model file",
    "pdftotext": "poppler (brew install poppler / apt install poppler-utils)",
    "twitter": "twitter-cli (pipx install twitter-cli), for the x plugin",
    "bili": "bilibili-cli (pipx install bilibili-cli), for the bilibili plugin search",
}


def checks(cfg) -> list[dict]:
    out: list[dict] = []

    def add(section, label, ok, required=False, hint=""):
        out.append({"section": section, "label": label, "ok": bool(ok), "required": required, "hint": hint})

    core = i18n.t("doctor.section_core")
    add(core, i18n.t("doctor.python", version=platform.python_version()), sys.version_info >= (3, 11), True)
    add(core, i18n.t("doctor.git"), which("git"), True, HINTS["git"])
    has_vault = (cfg.vault / "raw").is_dir() and (cfg.vault / "AGENTS.md").exists()
    add(core, i18n.t("doctor.vault", path=cfg.vault) if has_vault else i18n.t("doctor.vault_missing", path=cfg.vault), has_vault, True)
    add(core, i18n.t("doctor.config", sources=" < ".join(cfg.sources)), True)
    add(core, i18n.t("doctor.state", path=cfg.paths.state), True)

    models = i18n.t("doctor.section_models")
    st = backends.status(cfg)
    for name, s in st.items():
        b = backends.get(cfg, name)
        label = i18n.t("doctor.backend_cli", name=name, cmd=b.section.get("command")) if name in ("claude-code", "codex") \
            else i18n.t("doctor.backend_api", name=name, env=b.section.get("api_key_env"))
        if s["exhausted"]:
            label += f" (quota exhausted at {s['exhausted'].get('at_str')})"
        add(models, label, s["available"], False, s["why"] or "")
    for role, required in (("writer", True), ("reviewer", True), ("worker", True), ("vision", False)):
        ch = backends.chain(cfg, role)
        ok = [n for n in ch if n in st and st[n]["available"]]
        add(models, i18n.t("doctor.role", role=role, chain=" → ".join(ok)) if ok else i18n.t("doctor.role_none", role=role, chain=", ".join(ch)),
            ok, required)
    j = judge.get(cfg)
    jok, jwhy = j.available() if j.name != "none" else (True, "")
    add(models, i18n.t("doctor.judge", backend=j.name), jok, False, jwhy)

    search = i18n.t("doctor.section_search")
    n = fts.doc_count(cfg.paths.search_index)
    add(search, i18n.t("doctor.search_fts", docs=n if n is not None else 0), n is not None, False, "memex reindex")
    add(search, i18n.t("doctor.search_qmd"), qmd.ready(cfg), False, HINTS["qmd"])
    add(search, "node", which("node"), False, HINTS["node"])

    cap = i18n.t("doctor.section_capture")
    for tool in ("yt-dlp", "ffmpeg", "pdftotext"):
        add(cap, i18n.t("doctor.tool", name=tool), which(tool), False, HINTS[tool])
    if (cfg.get("asr.backend") or "whisper-cpp") == "whisper-cpp":
        add(cap, i18n.t("doctor.tool", name=cfg.get("asr.command") or "whisper-cli"), which(cfg.get("asr.command") or "whisper-cli"),
            False, HINTS["whisper-cli"])
        model = cfg.get("asr.model") or ""
        add(cap, i18n.t("doctor.asr_model", path=model or "-"), model and Path(model).expanduser().is_file(), False,
            "download a ggml model, e.g. ggml-large-v3-turbo-q5_0.bin, and set asr.model")
    if cfg.get("plugins.x.enabled"):
        add(cap, i18n.t("doctor.tool", name="twitter"), which("twitter"), False, HINTS["twitter"])

    sched = i18n.t("doctor.section_schedule")
    try:
        from memexos import schedule
        add(sched, i18n.t("doctor.scheduler", backend=schedule.backend_name(cfg)), schedule.backend_name(cfg) != "none", False)
    except ImportError:
        pass
    return out


def main(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex doctor")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = loader()
    rows = checks(cfg)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(i18n.t("doctor.title"))
        section = None
        for r in rows:
            if r["section"] != section:
                section = r["section"]
                print(f"\n{section}")
            mark = "✓" if r["ok"] else ("✗" if r["required"] else "·")
            line = f"  {mark} {r['label']}"
            if not r["ok"] and r["hint"]:
                line += f"\n      {i18n.t('doctor.hint_install', hint=r['hint'])}"
            print(line)
    missing = [r for r in rows if r["required"] and not r["ok"]]
    print("\n" + (i18n.t("doctor.summary_missing", n=len(missing)) if missing else i18n.t("doctor.summary_ok")))
    return 1 if missing else 0
