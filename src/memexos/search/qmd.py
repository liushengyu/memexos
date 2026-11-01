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
import time
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


def marker(cfg) -> Path:
    return cfg.paths.state / "qmd.json"


def ready(cfg) -> bool:
    """Usable for search: installed, indexed, and embedded at least once (the first embedding downloads models and can
    take a long time; until it has finished, search keeps to the built-in index)."""
    if not (command(cfg) and config_file(cfg).exists() and db_file(cfg).exists()):
        return False
    try:
        return json.loads(marker(cfg).read_text()).get("index") == index_name(cfg)
    except (OSError, ValueError):
        return False


PROXY_VARS = ("https_proxy", "http_proxy", "all_proxy")


def _env(cfg) -> dict:
    """qmd works offline except for its one-time model downloads (from Hugging Face). [search.qmd] proxy picks their
    route like [fetch] proxy does: "" = the environment's proxy variables, "direct" = none, or a proxy URL. Node only
    honours proxy variables with NODE_USE_ENV_PROXY=1, and a proxy with its own CA needs NODE_EXTRA_CA_CERTS."""
    env = dict(os.environ)
    want = (cfg.get("search.qmd.proxy") or "").strip()
    if want == "direct":
        return {k: v for k, v in env.items() if k.lower() not in PROXY_VARS}
    if want:
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            env[k] = want
    if any(k.lower() in PROXY_VARS for k in env):
        env.setdefault("NODE_USE_ENV_PROXY", "1")
        ca = cfg.get("fetch.proxy_cacert") or ""
        if ca and "NODE_EXTRA_CA_CERTS" not in env:
            env["NODE_EXTRA_CA_CERTS"] = os.path.expanduser(ca)
    return env


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
    out = run([exe, "--index", index_name(cfg), "update"], env=_env(cfg), timeout=3600)
    msg = out.stdout + out.stderr
    if embed and out.returncode == 0:
        e = run([exe, "--index", index_name(cfg), "embed"], env=_env(cfg), timeout=3600 * 3)
        msg += e.stdout + e.stderr
        if e.returncode == 0:              # vectors exist: from now on search may use them
            marker(cfg).parent.mkdir(parents=True, exist_ok=True)
            marker(cfg).write_text(json.dumps({"index": index_name(cfg), "embedded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    return {"ok": out.returncode == 0, "summary": " ".join(re.findall(r"(Indexed:[^\n]*|Embedded [^\n]*)", msg))[:300],
            "why": "" if out.returncode == 0 else msg.strip()[-300:]}


def query(cfg, mode: str, q: str, n: int, degraded: list) -> list[dict] | None:
    """mode: vsearch (vectors) | search (qmd's BM25) | query (expansion + rerank). Returns [{path, title, snippet}]."""
    exe = command(cfg)
    if not exe:
        return None
    try:
        r = run([exe, "--index", index_name(cfg), mode, q, "--json", "-n", str(n), "-c", collection(cfg)],
                env=_env(cfg), timeout=300)
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
