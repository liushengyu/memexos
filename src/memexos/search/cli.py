"""`memex search`, `memex get`, `memex reindex`, `memex models`."""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
from pathlib import Path

from memexos import events, i18n, rawref
from memexos.search import engine, fts, qmd
from memexos.search import text as T
from memexos.util import split_frontmatter

KINDS = ("topics", "concepts", "entities", "sources")


def _no_vault(cfg) -> bool:
    if not (cfg.vault / "raw").is_dir():
        print(i18n.t("cli.no_vault", path=cfg.vault), file=sys.stderr)
        return True
    return False


def _fm_list(v) -> str:
    return "、".join(v) if isinstance(v, list) and i18n.language() == "zh" else ", ".join(v) if isinstance(v, list) else str(v or "")


_compiled: dict | None = None
SOURCES_HEADING = re.compile(r"(?ms)^#{2,3}\s*(?:Sources|来源)\b.*?(?=^## |\Z)")


def primary_raw(page_text: str, names: set[str], vault: Path) -> str | None:
    """The raw file a source page summarizes: the first list item in its Sources section that starts with a raw
    path; else the first raw cited in Sources; else the first raw on the page. Pages also cite sources they only
    borrow one sentence from, and those must not be mistaken for the page's own source."""
    m = SOURCES_HEADING.search(page_text)
    sec = m.group(0) if m else ""
    for line in sec.splitlines():
        if re.match(r"^\s*[-*]\s*`?(?:\S*/)?raw/", line):
            refs = rawref.find(line, names, vault)
            if refs:
                return refs[0]
    refs = rawref.find(sec, names, vault) or rawref.find(page_text, names, vault)
    return refs[0] if refs else None


def compiled_page(vault: Path, raw: str) -> str | None:
    """The wiki source page that summarizes this raw file (else the first source page citing it)."""
    global _compiled
    if _compiled is None:
        own, cited = {}, {}
        names = rawref.index(vault)
        for p in sorted(glob.glob(str(vault / "wiki" / "sources" / "*.md"))):
            try:
                text = open(p, encoding="utf-8").read()
            except OSError:
                continue
            prim = primary_raw(text, names, vault)
            if prim:
                own.setdefault(prim, Path(p).stem)
            for r in rawref.find(text, names, vault):
                cited.setdefault(r, Path(p).stem)
        _compiled = {**cited, **own}
    return _compiled.get(raw)


def describe(cfg, hit: dict) -> tuple[str, str, str | None]:
    """(kind label, one-line classification, compiled wiki page for raw hits)."""
    p = hit["path"]
    m = re.match(r"wiki/(\w+)/", p)
    cls = ""
    try:
        from memexos.catalog import view  # classification with drafts and writer overrides, once available
    except ImportError:
        view = None
    if m:
        label = i18n.t(f"search.kind.{m.group(1)}") if m.group(1) in KINDS else m.group(1)
        if view:
            cls = view(cfg, page=p) or ""
        if not cls:
            try:
                fm, _ = split_frontmatter((cfg.vault / p).read_text(encoding="utf-8", errors="replace")[:4000])
            except OSError:
                fm = {}
            if fm.get("domain"):
                cls = i18n.t("search.classification", domain=_fm_list(fm.get("domain")), type=_fm_list(fm.get("content_type")),
                             evidence=_fm_list(fm.get("evidence")))
                if fm.get("use_note"):
                    note = str(fm["use_note"])
                    cls += i18n.t("search.note", note=note if len(note) <= 140 else note[:140].rsplit(" ", 1)[0] + "…")
        return label, cls, None
    if p.startswith("raw/"):
        if view:
            cls = view(cfg, raw=p) or ""
        return i18n.t("search.kind.raw"), cls, compiled_page(cfg.vault, p)
    return {"index.md": i18n.t("search.kind.index"), "AGENTS.md": i18n.t("search.kind.rules")}.get(p, i18n.t("search.kind.other")), "", None


def cmd_search(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex search", add_help=False)
    ap.add_argument("query")
    ap.add_argument("--kw", help="2–5 keywords, space separated")
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--mode", choices=engine.MODES, default="auto")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--inbox", action="store_true", help="also search inbox/ (one-off notes for people, often stale)")
    a = ap.parse_args(argv)
    cfg = loader()
    if _no_vault(cfg):
        return 1
    t0 = time.time()
    found, kws, degraded = engine.search(cfg, a.query, a.kw.split() if a.kw else None, a.n + 6, a.mode, a.inbox or None)
    hits = [dict(h, **detail(cfg, h, kws, a.query)) for h in found]
    hits = dedupe(hits)[:a.n]
    note_search(cfg, a.query, a.mode, len(hits), time.time() - t0, top=[h["path"] for h in hits[:10]])
    if a.json:
        print(json.dumps({"degraded": degraded, "keywords": kws, "hits": [
            {k: h.get(k) for k in ("path", "title", "kind", "classification", "compiled_page", "sources", "matched", "snippet")}
            for h in hits]}, ensure_ascii=False))
        return 0
    print(i18n.t("search.header", query=a.query, mode=a.mode, keywords=" / ".join(kws) or "-", seconds=f"{time.time() - t0:.1f}"))
    for d in degraded:
        print(f"⚠️ {d}")
    print()
    for i, h in enumerate(hits, 1):
        snip = re.sub(r"\s+", " ", h.get("snippet", "")).strip()[:160]
        print(f"{i}. [{h['kind']}] {h['title']}  ({h['path']})")
        if h.get("compiled_page"):
            print("   " + i18n.t("search.compiled_as", page=h["compiled_page"]))
        if h.get("classification"):
            print("   " + h["classification"])
        info = []
        if h.get("sources"):
            info.append(i18n.t("search.sources", n=h["sources"]))
        if kws:
            info.append(i18n.t("search.matched", words=", ".join(h["matched"])) if h["matched"] else i18n.t("search.matched_none"))
        if info:
            print("   " + " · ".join(info))
        if snip:
            print("   " + i18n.t("search.excerpt", text=snip))
    if not hits:
        print(i18n.t("search.nothing"))
    return 0


_raw_names: set | None = None


def detail(cfg, hit: dict, kws: list[str], question: str) -> dict:
    """What an agent needs to judge a hit without opening it: kind, classification, the wiki page a raw file was
    compiled into, how many sources a wiki page rests on, which keywords it contains, and the passage where they meet."""
    global _raw_names
    kind, cls, page = describe(cfg, hit)
    try:
        text = (cfg.vault / hit["path"]).read_text(encoding="utf-8", errors="replace")[:400_000]
    except OSError:
        text = ""
    body = split_frontmatter(text)[1] if text else ""
    out = {"kind": kind, "classification": cls, "compiled_page": page, "sources": None,
           "matched": T.matched(hit.get("title", "") + "\n" + body, kws) if kws else [],
           "snippet": T.snippet(body, (kws or []) + [question]) if body else hit.get("snippet", "")}
    if hit["path"].startswith("wiki/") and body:
        if _raw_names is None:
            _raw_names = rawref.index(cfg.vault)
        out["sources"] = len(set(rawref.find(body, _raw_names, cfg.vault))) or None
    return out


def dedupe(hits: list[dict]) -> list[dict]:
    """A raw file and the wiki page compiled from it are the same knowledge: keep the page, drop the raw file (the
    page's Sources section leads back to it)."""
    paths = {h["path"] for h in hits}
    return [h for h in hits if not (h.get("compiled_page") and f"wiki/sources/{h['compiled_page']}.md" in paths)]


def note_search(cfg, query: str, mode: str, hits: int, seconds: float, job: str = "search", top: list[str] | None = None) -> None:
    """Best effort: lets the status view, the console and evaluations see what agents search and read.
    Read-only sandboxes skip it."""
    try:
        events.emit(cfg.paths.state, "search", job=job, query=query[:160], mode=mode, hits=hits, seconds=round(seconds, 2),
                    top=top or [])
    except OSError:
        pass


def note_read(cfg, path: str) -> None:
    try:
        events.emit(cfg.paths.state, "read", job="search", path=path)
    except OSError:
        pass


def clean_name(target: str) -> str:
    """What agents paste as a page name: [[Page|alias]], [[Page#Heading]], quotes, backticks, a path with or without
    .md, a trailing (wiki/…) from search output."""
    t = target.strip().strip("`'\"“”‘’「」 ")
    m = re.search(r"\(((?:wiki|raw|inbox)/[^)]+)\)\s*$", t)
    if m:
        t = m.group(1)
    t = re.sub(r"^\[\[|\]\]$", "", t).strip()
    t = t.split("|", 1)[0].split("#", 1)[0].strip()
    return t


def page_names(vault: Path) -> dict[str, str]:
    """Every page by its lower-case name (file stem): wiki first, then raw."""
    out: dict[str, str] = {}
    for root in ("wiki", "raw"):
        for p in sorted((vault / root).rglob("*.md")):
            out.setdefault(p.stem.lower(), p.relative_to(vault).as_posix())
    return out


def resolve_page(vault: Path, target: str) -> str | None:
    t = clean_name(target)
    for cand in (t, t + ".md", "wiki/" + t, "wiki/" + t + ".md"):
        if cand and (vault / cand).is_file() and (vault / cand).resolve().is_relative_to(vault.resolve()):
            return Path(cand).as_posix()
    return page_names(vault).get(Path(t).name.removesuffix(".md").lower())


def suggest(vault: Path, target: str, n: int = 5) -> list[str]:
    import difflib
    names = page_names(vault)
    key = Path(clean_name(target)).name.removesuffix(".md").lower()
    close = difflib.get_close_matches(key, list(names), n=n, cutoff=0.6)
    close += [k for k in names if key and key in k and k not in close][: n - len(close)]
    return [Path(names[k]).stem for k in close[:n]]


def cmd_get(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex get", add_help=False)
    ap.add_argument("page")
    ap.add_argument("--from", dest="start", type=int, default=1)
    ap.add_argument("--lines", type=int)
    a = ap.parse_args(argv)
    cfg = loader()
    if _no_vault(cfg):
        return 1
    rel = resolve_page(cfg.vault, a.page)
    if not rel:
        close = suggest(cfg.vault, a.page)
        print(i18n.t("get.not_found", page=a.page, close=" / ".join(close)) if close else i18n.t("get.not_found_none", page=a.page),
              file=sys.stderr)
        return 1
    lines = (cfg.vault / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    note_read(cfg, rel)
    start = max(a.start - 1, 0)
    end = start + a.lines if a.lines else len(lines)
    print(i18n.t("get.header", path=rel, start=start + 1, end=min(end, len(lines)), total=len(lines)) + "\n")
    print("\n".join(lines[start:end]))
    return 0


def cmd_reindex(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex reindex")
    ap.add_argument("--no-embed", action="store_true", help="qmd: update the keyword index only, skip embeddings")
    a = ap.parse_args(argv)
    cfg = loader()
    if _no_vault(cfg):
        return 1
    with events.run(cfg.paths.state, "reindex"):
        return _reindex(cfg, a)


def _reindex(cfg, a) -> int:
    """The full-text index first (seconds), then qmd under its own lock: a first embedding pass downloads models and
    can take hours or hang on the network, and must never keep new pages out of the full-text index meanwhile.
    A reindex asked for while one is running is not dropped: the running one goes once more when it finishes, so
    pages published during its run are indexed too."""
    from memexos import lock

    def full_text():
        with events.step(cfg.paths.state, "index"):
            r = fts.update(cfg.vault, cfg.paths.search_index)
            if r.get("rebuilt"):
                events.emit(cfg.paths.state, "index.rebuilt", why=r["rebuilt"], docs=r["docs"])
                print(i18n.t("reindex.rebuilt", why=r["rebuilt"]))
            if r["added"] or r["updated"] or r["removed"]:
                events.emit(cfg.paths.state, "index.update", **{k: v for k, v in r.items() if k != "rebuilt"})
            print(i18n.t("event.index.update", **r))

    def vectors():
        with events.step(cfg.paths.state, "embed") as info:
            q = qmd.update(cfg, embed=not a.no_embed)
            info["result"] = "ok" if q.get("ok") else "error"
            events.emit(cfg.paths.state, "index.qmd", **q)
            print(f"qmd: {q.get('summary') or ('ok' if q['ok'] else q.get('why'))}")

    if not lock.coalesced(cfg.paths.locks, "reindex", full_text):
        print(i18n.t("reindex.busy"))
    want = cfg.get("search.backend") or "auto"
    if (want == "qmd" or (want == "auto" and qmd.command(cfg))) and not lock.coalesced(cfg.paths.locks, "qmd", vectors):
        print(i18n.t("reindex.qmd_busy"))
    return 0


def cmd_models(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex models", description=i18n.t("models.help"))
    ap.add_argument("action", nargs="?", default="status", choices=["status", "pull"])
    ap.add_argument("--deep", action="store_true", help=i18n.t("models.opt_deep"))
    a = ap.parse_args(argv)
    cfg = loader()
    if not qmd.command(cfg):
        print(i18n.t("setup.qmd_missing"))
        return 1
    if a.action == "pull":
        print(i18n.t("models.pulling"))
    r = qmd.models(cfg, a.action if a.action == "pull" else "check", deep=a.deep)
    for name, path in r["models"].items():
        label = {"embed": i18n.t("models.default_embed"), "rerank": i18n.t("models.rerank"),
                 "generate": i18n.t("models.generate")}.get(name) or i18n.t("models.embed", name=name.rsplit("/", 1)[-1])
        print(f"{'✓' if path else '·'} {label}: {path or i18n.t('models.missing')}")
    if not r["ok"]:
        if a.action == "pull":
            print(i18n.t("models.not_ok", why=r.get("why") or "?"), file=sys.stderr)
        else:
            print(i18n.t("models.to_pull"))
    return 0 if r["ok"] else 1
