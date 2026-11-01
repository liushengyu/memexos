"""Search orchestration: candidates from the backends → rank fusion → optional rerank.

Modes (choose with --mode; the default is "auto"):
  auto     question → vectors (qmd, when indexed) + keywords → BM25 (built-in index), fused by rank (RRF),
           then the top candidates are reranked. Falls back to keywords only when qmd is not available.
  fast     the same without reranking
  keyword  BM25 on the built-in index only (milliseconds; works in a read-only sandbox)
  vector   qmd vector search only
  deep     qmd's own pipeline (query expansion + its reranker); slowest
Keywords: the asking agent knows best what to look for, so --kw "a b c" is preferred; without it they are
extracted from the question. Whole-sentence BM25 finds little in languages without spaces, which is why the
question and the keywords travel separately.
"""

from __future__ import annotations

from memexos.search import fts, qmd, rerank as rr
from memexos.search import text as T

MODES = ("auto", "fast", "keyword", "vector", "deep")


def rrf(lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal rank fusion: score = Σ 1 / (k + rank). Robust to backends with incomparable scores."""
    score: dict[str, float] = {}
    best: dict[str, dict] = {}
    for lst in lists:
        seen = set()
        for rank, hit in enumerate(lst, 1):
            p = hit["path"]
            if p in seen:
                continue
            seen.add(p)
            score[p] = score.get(p, 0.0) + 1.0 / (k + rank)
            best.setdefault(p, hit)
    return [best[p] for p in sorted(score, key=lambda x: -score[x])]


def backend(cfg) -> str:
    want = cfg.get("search.backend") or "auto"
    if want == "auto":
        return "qmd" if qmd.ready(cfg) else "fts"
    return want


def search(cfg, question: str, kw: list[str] | None = None, n: int = 8, mode: str = "auto",
           include_inbox: bool | None = None) -> tuple[list[dict], list[str], list[str]]:
    """Returns (hits, keywords used, degradation notes). Each hit: {path, title, snippet, ...}."""
    degraded: list[str] = []
    kws = kw if kw else T.keywords(question)
    terms = list(dict.fromkeys((kws or []) + [question]))
    cand = int(cfg.get("search.candidates") or 20)
    db = cfg.paths.search_index
    use_qmd = backend(cfg) == "qmd"
    if mode in ("vector", "deep") and not use_qmd:
        degraded.append("qmd is not set up (memex reindex after installing qmd); used keyword search instead")
        mode = "keyword"

    if mode == "keyword":
        hits = fts.search(cfg.vault, db, terms, max(n * 2, cand), degraded, kws)
    elif mode == "vector":
        hits = qmd.query(cfg, "vsearch", question, max(n * 2, cand), degraded) or []
    elif mode == "deep":
        hits = qmd.query(cfg, "query", question, max(n * 2, cand), degraded) or []
    else:
        lists = [fts.search(cfg.vault, db, terms, cand, degraded, kws)]
        if use_qmd:
            v = qmd.query(cfg, "vsearch", question, cand, degraded)
            if v:
                lists.insert(0, v)
        hits = rrf([x for x in lists if x])
        if mode == "auto":
            hits = rr.rerank(cfg, question, hits, degraded, k=cand)

    if not hits and mode != "keyword":
        hits = fts.search(cfg.vault, db, terms, max(n * 2, cand), degraded, kws)
    inbox = cfg.get("search.include_inbox") if include_inbox is None else include_inbox
    out, seen = [], set()
    for h in hits:
        if h["path"] in seen or (not inbox and h["path"].startswith("inbox/")):
            continue
        seen.add(h["path"])
        out.append(h)
        if len(out) >= n:
            break
    return out, kws, list(dict.fromkeys(degraded))
