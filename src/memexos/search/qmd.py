"""Optional search backend: qmd (https://github.com/tobi/qmd), local vector search with a reranker.

MemexOS keeps its own named qmd index ("memexos-<vault id>" unless configured),
so it never touches collections you use qmd for elsewhere. The index's YAML
file is written once by `memex reindex` (collection path, mask, exclusions and,
optionally, the embedding model) and is yours to edit afterwards.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from memexos.util import run, which

SNIP_NOISE = re.compile(r"@@[^@]*@@|^\(\d+ before, \d+ after\)\s*")
EXCLUDE = [".memex/**", "raw/assets/**", ".git/**", ".obsidian/**"]


def command(cfg) -> str | None:
    return which(cfg.get("search.qmd.command") or "qmd")


def index_name(cfg) -> str:
    from memexos.config import vault_id
    return cfg.get("search.qmd.index") or f"memexos-{vault_id(cfg.vault)}"


def collection(cfg) -> str:
    return cfg.get("search.qmd.collection") or "memex"


def config_file(cfg) -> Path:
    base = os.environ.get("QMD_CONFIG_DIR") or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "qmd")
    return Path(base) / f"{index_name(cfg)}.yml"


def db_file(cfg) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "qmd" / f"{index_name(cfg)}.sqlite"


def ready(cfg) -> bool:
    return bool(command(cfg)) and config_file(cfg).exists() and db_file(cfg).exists()


def _env():
    # qmd runs local models and reads local files; it needs no proxy, and an inherited
    # HTTPS proxy with a private CA only makes its model downloads fail.
    return {k: v for k, v in os.environ.items() if k.lower() not in ("https_proxy", "http_proxy", "all_proxy")}


def ensure_index(cfg) -> str:
    """Write the named index's config the first time. Returns a short status."""
    f = config_file(cfg)
    if f.exists():
        return "exists"
    f.parent.mkdir(parents=True, exist_ok=True)
    embed = cfg.get("search.qmd.embed_model") or ""
    lines = []
    if embed:
        lines += ["models:", f"  embed: {json.dumps(embed)}"]
    lines += ["collections:", f"  {collection(cfg)}:", f"    path: {json.dumps(str(cfg.vault))}", '    pattern: "**/*.md"',
              "    ignore:"] + [f"      - {json.dumps(x)}" for x in EXCLUDE]
    desc = cfg.get("search.qmd.context") or ""
    if desc:
        lines += ["    context:", f'      "": {json.dumps(desc, ensure_ascii=False)}']
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "created"


def update(cfg, embed: bool = True) -> dict:
    exe = command(cfg)
    if not exe:
        return {"ok": False, "why": "qmd not installed"}
    ensure_index(cfg)
    out = run([exe, "--index", index_name(cfg), "update"], env=_env(), timeout=3600)
    msg = out.stdout + out.stderr
    if embed and out.returncode == 0:
        e = run([exe, "--index", index_name(cfg), "embed"], env=_env(), timeout=3600 * 3)
        msg += e.stdout + e.stderr
    return {"ok": out.returncode == 0, "summary": " ".join(re.findall(r"(Indexed:[^\n]*|Embedded [^\n]*)", msg))[:300],
            "why": "" if out.returncode == 0 else msg.strip()[-300:]}


def query(cfg, mode: str, q: str, n: int, degraded: list) -> list[dict] | None:
    """mode: vsearch (vectors) | search (qmd's BM25) | query (expansion + rerank). Returns [{path, title, snippet}]."""
    exe = command(cfg)
    if not exe:
        return None
    try:
        r = run([exe, "--index", index_name(cfg), mode, q, "--json", "-n", str(n), "-c", collection(cfg)],
                env=_env(), timeout=300)
    except Exception as e:  # noqa: BLE001
        degraded.append(f"qmd {mode} failed ({type(e).__name__})")
        return None
    if r.returncode != 0:
        errs = [l.strip() for l in r.stderr.splitlines() if re.search(r"Error|error:|CANTOPEN|denied|not permitted", l)]
        degraded.append(f"qmd {mode} unavailable ({(errs[-1] if errs else r.stderr.strip()[-100:]) or 'no output'})")
        return None
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    out = []
    for h in items:
        p = re.sub(r"^qmd://[^/]+/", "", h.get("file") or "").split("?", 1)[0].split(":", 1)[0]
        if p:
            out.append({"path": p, "title": h.get("title") or Path(p).stem, "snippet": SNIP_NOISE.sub("", h.get("snippet", "")).strip()})
    return out


def models_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")) / "qmd" / "models"


def reranker_model() -> Path | None:
    """qmd's local reranker model, when it has already been downloaded (qmd fetches it on first use, which takes a while)."""
    d = models_dir()
    hits = sorted(d.glob("*rerank*.gguf")) if d.is_dir() else []
    return hits[0] if hits else None


def package_dir() -> Path | None:
    """Where qmd's JavaScript package is installed (to reuse its local reranker model)."""
    exe = which("qmd")
    if not exe:
        return None
    p = Path(os.path.realpath(exe))
    for d in [p.parent, *p.parents]:
        pj = d / "package.json"
        if pj.is_file():
            try:
                if json.loads(pj.read_text()).get("name", "").endswith("/qmd") or json.loads(pj.read_text()).get("name") == "qmd":
                    return d
            except ValueError:
                pass
    return None
