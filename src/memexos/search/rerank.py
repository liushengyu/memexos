"""Rerankers: reorder the top candidates by "does this page contain the answer?".

Fusion puts pages that share words or meaning with the question near the top;
a reranker reads question and page together and asks the sharper question of
whether the page holds the specific fact, rule, number or decision asked for,
rather than merely the same topic. Every reranker degrades to the fused order
on failure and says so in the output.

  none  keep the fused order
  qmd   qmd's bundled local Qwen3 reranker (nothing leaves the machine)
  llm   the worker model scores all candidates in one structured call
  jev   the judge service (one yes/no probability per candidate)
"""

from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path

from memexos.search import qmd as qmd_mod
from memexos.util import run, split_frontmatter, which

INSTRUCTION = ("A user asked the Query about their knowledge base. Does the Document contain the specific information needed to "
               "answer the Query (the fact, rule, number, decision, or explanation asked for), not merely the same topic?")


def passage(vault: Path, hit: dict, limit: int = 1500) -> str:
    try:
        text = split_frontmatter((vault / hit["path"]).read_text(encoding="utf-8", errors="replace"))[1]
    except OSError:
        text = ""
    snip = re.sub(r"@@[^@]*@@", "", hit.get("snippet", "")).strip()
    return f"{hit.get('title') or Path(hit['path']).stem} ({hit['path']})\n{text[:limit]}\n…\nmatched: {snip[:600]}"


def qmd_scores(query: str, texts: list[str]) -> dict[int, float] | None:
    pkg = qmd_mod.package_dir()
    node = which("node")
    if not pkg or not node:
        return None
    script = resources.files("memexos.search").joinpath("qmd_rerank.mjs")
    req = {"id": "q", "query": query, "instruction": INSTRUCTION, "docs": [{"file": str(i), "text": t} for i, t in enumerate(texts)]}
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("https_proxy", "http_proxy", "all_proxy")}
    env["QMD_PKG"] = str(pkg)
    try:
        r = run([node, str(script)], input=json.dumps(req, ensure_ascii=False) + "\n", env=env, timeout=180)
        lines = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
        return {int(k): float(v) for k, v in lines[0]["scores"].items()} if lines else None
    except Exception:  # noqa: BLE001 — any failure means "no reranker"
        return None


def judge_scores(cfg, query: str, texts: list[str], backend: str) -> dict[int, float] | None:
    try:
        from memexos import judge
    except ImportError:
        return None
    try:
        return judge.get(cfg, backend).relevance(query, texts, INSTRUCTION)
    except Exception:  # noqa: BLE001 — unavailable judge or network trouble: keep the fused order
        return None


def available(cfg) -> str:
    """Resolve rerank = "auto": qmd when its reranker can run here, else none."""
    want = cfg.get("search.rerank") or "auto"
    if want != "auto":
        return want
    return "qmd" if qmd_mod.package_dir() and which("node") else "none"


def rerank(cfg, query: str, hits: list[dict], degraded: list, k: int | None = None) -> list[dict]:
    method = available(cfg)
    if method == "none" or len(hits) < 2:
        return hits
    k = k or int(cfg.get("search.candidates") or 20)
    head, tail = hits[:k], hits[k:]
    texts = [passage(cfg.vault, h) for h in head]
    scores = qmd_scores(query, texts) if method == "qmd" else judge_scores(cfg, query, texts, method)
    if not scores:
        degraded.append(f"reranker '{method}' unavailable; kept the fused order")
        return hits
    order = sorted(range(len(head)), key=lambda i: (-scores.get(i, -1.0), i))
    for i in order:
        head[i]["rerank"] = scores.get(i)
    return [head[i] for i in order] + tail
