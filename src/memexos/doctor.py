"""`memex doctor`: what is installed, configured and reachable, with install hints for what is missing."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from memexos import backends, i18n, judge
from memexos.search import fts, qmd
from memexos.util import which

def hint(name: str) -> str:
    return i18n.t(f"doctor.hint.{name}")


def node_major() -> int | None:
    exe = which("node")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def checks(cfg) -> list[dict]:
    out: list[dict] = []

    def add(section, label, ok, required=False, hint=""):
        out.append({"section": section, "label": label, "ok": bool(ok), "required": required, "hint": hint})

    core = i18n.t("doctor.section_core")
    add(core, i18n.t("doctor.python", version=platform.python_version()), sys.version_info >= (3, 11), True)
    add(core, i18n.t("doctor.git"), which("git"), True, hint("git"))
    has_vault = (cfg.vault / "raw").is_dir() and (cfg.vault / "AGENTS.md").exists()
    add(core, i18n.t("doctor.vault", path=cfg.vault) if has_vault else i18n.t("doctor.vault_missing", path=cfg.vault), has_vault, True)
    add(core, i18n.t("doctor.config", sources=" < ".join(cfg.sources)), True)
    add(core, i18n.t("doctor.state", path=cfg.paths.state), True)

    models = i18n.t("doctor.section_models")
    st = backends.status(cfg)
    for name, s in st.items():
        b = backends.get(cfg, name)
        kind = backends.kind_of(cfg, name)
        if kind in ("claude-code", "codex"):
            label = i18n.t("doctor.backend_cli", name=name, cmd=b.section.get("command") or ("claude" if kind == "claude-code" else "codex"))
        elif kind == "openai":
            label = i18n.t("doctor.backend_openai", name=name, url=b.section.get("base_url") or "-", model=b.section.get("model") or "-")
        else:
            label = i18n.t("doctor.backend_api", name=name, env=b.section.get("api_key_env") or "ANTHROPIC_API_KEY")
        if s["exhausted"]:
            label += i18n.t("doctor.quota", at=s["exhausted"].get("at_str"))
        # a command line that is missing gets the install hint; one that is installed gets the actual reason (not logged in…)
        missing_cli = kind in ("claude-code", "codex") and not b.exe()
        why = hint("claude" if kind == "claude-code" else "codex") if missing_cli else s["why"]
        add(models, label, s["available"], False, why if not s["available"] else "")
    for role, required in (("writer", True), ("reviewer", True), ("worker", True), ("vision", False)):
        ch = backends.chain(cfg, role)
        ok = [n for n in ch if n in st and st[n]["available"]]
        name = i18n.t(f"console.role.{role}")
        name = role if name == role else f"{name} ({role})"
        add(models, i18n.t("doctor.role", role=name, chain=" → ".join(ok)) if ok else i18n.t("doctor.role_none", role=name, chain=", ".join(ch)),
            ok, required)
    j = judge.get(cfg)
    jok, jwhy = j.available() if j.name != "none" else (True, "")
    add(models, i18n.t("doctor.judge", backend=j.name), jok, False, jwhy)

    search = i18n.t("doctor.section_search")
    n = fts.doc_count(cfg.paths.search_index)
    if fts.damage_note(cfg.paths.search_index).exists():
        add(search, i18n.t("doctor.search_fts_damaged"), False, False, "memex reindex")
    else:
        add(search, i18n.t("doctor.search_fts", docs=n if n is not None else 0), n is not None, False, "memex reindex")
    nm = node_major()
    add(search, i18n.t("doctor.node", version=nm) if nm else i18n.t("doctor.node_missing"), (nm or 0) >= 22, False, hint("node"))
    if not qmd.command(cfg):
        add(search, i18n.t("doctor.search_qmd"), False, False, hint("qmd_install"))
    elif qmd.ready(cfg):
        add(search, i18n.t("doctor.search_qmd"), True)
    else:
        from memexos import lock
        building = lock.holder(cfg.paths.locks, "qmd")
        add(search, i18n.t("doctor.search_qmd_building") if building else i18n.t("doctor.search_qmd_unindexed"), False, False,
            "" if building else hint("qmd_index"))
    if qmd.package_dir():
        add(search, i18n.t("doctor.search_rerank"), qmd.reranker_model(), False, i18n.t("doctor.search_rerank_hint"))

    cap = i18n.t("doctor.section_capture")
    for tool in ("yt-dlp", "ffmpeg", "pdftotext"):
        add(cap, i18n.t("doctor.tool", name=tool), which(tool), False, hint(tool))
    if (cfg.get("asr.backend") or "whisper-cpp") == "whisper-cpp":
        add(cap, i18n.t("doctor.tool", name=cfg.get("asr.command") or "whisper-cli"), which(cfg.get("asr.command") or "whisper-cli"),
            False, hint("whisper-cli"))
        from memexos.capture import asr
        model = asr.configured_model(cfg)
        add(cap, i18n.t("doctor.asr_model", path=model or "-"), model and Path(model).expanduser().is_file(), False,
            hint("asr_model"))
    if cfg.get("plugins.x.enabled"):
        add(cap, i18n.t("doctor.tool", name="twitter"), which("twitter"), False, hint("twitter"))
    from memexos.capture import browser as login_browser, cookies
    sites = [s for s in cookies.SITES if cfg.get(f"plugins.{s}.enabled") and cookies.source(cfg, s)]
    if any(cookies.source(cfg, s) == "memex" for s in sites):
        exe = login_browser.find(cfg)
        add(cap, i18n.t("doctor.login_browser", path=exe or "-"), exe, False, hint("browser"))
    for s in sites:
        line = cookies.status(cfg, s)
        add(cap, line, s not in cookies.needs_login(cfg), False, f"memex login {s}")

    sched = i18n.t("doctor.section_schedule")
    try:
        from memexos import schedule
        add(sched, i18n.t("doctor.scheduler", backend=schedule.backend_name(cfg)), schedule.backend_name(cfg) != "none", False)
        blocked = schedule.protected_folder(cfg.vault)
        if blocked:
            add(sched, i18n.t("doctor.protected", folder=blocked), False, True, i18n.t("doctor.hint.protected"))
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
