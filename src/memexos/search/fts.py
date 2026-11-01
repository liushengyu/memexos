"""Built-in search backend: an SQLite FTS5 index over the vault's markdown files.

It needs nothing beyond the Python standard library, so a fresh install can
search right away. Text is stored pre-tokenized (CJK bigrams + lowercase words,
see memexos.search.text) in a plain unicode61 FTS5 table and ranked with BM25,
titles weighted higher than bodies.

The index lives in the cache directory and is updated incrementally (by file
size and mtime). Reading never needs write access: when the index cannot be
opened at all (missing, or a read-only sandbox without it), search falls back
to scanning the files directly, slower but correct.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
from pathlib import Path

from memexos.search import text as T
from memexos.util import split_frontmatter

SCHEMA_VERSION = 2          # 2: Porter stemming for words (box / boxes, invent / invented)
INDEXED_DIRS = ("wiki", "raw", "inbox")
INDEXED_FILES = ("index.md",)
EXTS = (".md", ".markdown", ".txt")


def iter_files(vault: Path):
    for f in INDEXED_FILES:
        p = vault / f
        if p.is_file():
            yield p
    for d in INDEXED_DIRS:
        root = vault / d
        if not root.is_dir():
            continue
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [x for x in dirnames if not x.startswith(".") and not (d == "raw" and x == "assets")]
            for n in files:
                if n.endswith(EXTS) and not n.startswith("."):
                    yield Path(dirpath) / n


def title_of(rel: str, text: str) -> str:
    fm, body = split_frontmatter(text)
    t = fm.get("title") or fm.get("topic")
    if isinstance(t, str) and t.strip():
        return t.strip()
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1).strip() if m else Path(rel).stem


def connect(db: Path, readonly: bool = False) -> sqlite3.Connection:
    """Readers open the file read-only; inside a read-only sandbox (no lock files possible) they fall back to
    immutable mode. The index uses a rollback journal, not WAL, so the main file is always complete."""
    if readonly:
        if not db.exists():
            raise sqlite3.OperationalError(f"no index at {db}")
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            c.execute("SELECT 1 FROM docs LIMIT 1")
            return c
        except sqlite3.Error:
            return sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db, timeout=30)
    c.execute("PRAGMA journal_mode=DELETE")
    c.executescript("""
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT, size INTEGER, mtime REAL);
    """)
    # The text itself is not stored (snippets are cut from the files), which keeps the index about half the
    # size. Contentless tables can only delete rows on SQLite >= 3.43; older versions get a regular table.
    v = c.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
    if not v or int(v[0]) != SCHEMA_VERSION:             # a new layout: rebuild from scratch
        c.executescript("DROP TABLE IF EXISTS fts; DELETE FROM docs;")
        c.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='fts'").fetchone():
        # Porter stems English words at index and query time; CJK bigrams pass through unchanged.
        tok = "tokenize='porter unicode61 remove_diacritics 2'"
        try:
            c.execute(f"CREATE VIRTUAL TABLE fts USING fts5(title, body, content='', contentless_delete=1, {tok})")
        except sqlite3.OperationalError:
            c.execute(f"CREATE VIRTUAL TABLE fts USING fts5(title, body, {tok})")
    c.commit()
    return c


def update(vault: Path, db: Path) -> dict:
    """Bring the index in line with the vault. Cheap when nothing changed (one stat per file)."""
    t0 = time.time()
    c = connect(db)
    known = {p: (i, s, m) for i, p, s, m in c.execute("SELECT id, path, size, mtime FROM docs")}
    seen, added, updated = set(), 0, 0
    for f in iter_files(vault):
        rel = f.relative_to(vault).as_posix()
        seen.add(rel)
        st = f.stat()
        old = known.get(rel)
        if old and old[1] == st.st_size and abs(old[2] - st.st_mtime) < 1e-6:
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = title_of(rel, raw)
        _, body = split_frontmatter(raw)
        if old:
            c.execute("DELETE FROM fts WHERE rowid=?", (old[0],))
            c.execute("UPDATE docs SET title=?, size=?, mtime=? WHERE id=?", (title, st.st_size, st.st_mtime, old[0]))
            rowid = old[0]
            updated += 1
        else:
            rowid = c.execute("INSERT INTO docs(path, title, size, mtime) VALUES(?,?,?,?)",
                              (rel, title, st.st_size, st.st_mtime)).lastrowid
            added += 1
        c.execute("INSERT INTO fts(rowid, title, body) VALUES(?,?,?)", (rowid, T.index_text(title + " " + rel), T.index_text(body)))
    gone = [(i, p) for p, (i, _, _) in known.items() if p not in seen]
    for i, _ in gone:
        c.execute("DELETE FROM fts WHERE rowid=?", (i,))
        c.execute("DELETE FROM docs WHERE id=?", (i,))
    c.execute("INSERT OR REPLACE INTO meta VALUES('updated', ?)", (str(time.time()),))
    c.commit()
    n = c.execute("SELECT count(*) FROM docs").fetchone()[0]
    c.close()
    return {"added": added, "updated": updated, "removed": len(gone), "docs": n, "seconds": round(time.time() - t0, 2)}


def doc_count(db: Path) -> int | None:
    try:
        c = connect(db, readonly=True)
        n = c.execute("SELECT count(*) FROM docs").fetchone()[0]
        c.close()
        return n
    except sqlite3.Error:
        return None


def _match_expr(terms: list[str]) -> str:
    """FTS5 query: OR of the terms. A short term (a keyword, up to 3 tokens) must match as a phrase, so
    "rank fusion" or a CJK keyword (its bigrams) matches only in that order; a long term (the whole question)
    is split into its tokens, because a sentence never appears verbatim."""
    parts = []
    for t in terms:
        toks = [x.replace('"', "") for x in T.tokens(t)]
        if not toks:
            continue
        if len(toks) <= 3 or all(T.CJK.fullmatch(x) for x in toks) and len(toks) <= 6:
            parts.append('"' + " ".join(toks) + '"')
        else:
            parts += [f'"{x}"' for x in T.content_tokens(t) if (len(x) > 1 or T.CJK.fullmatch(x)) and '"' not in x]
    return " OR ".join(dict.fromkeys(parts))


def search(vault: Path, db: Path, terms: list[str], n: int = 20, degraded: list | None = None) -> list[dict]:
    """[{path, title, snippet, score}] best first. Falls back to a file scan when the index is unusable."""
    expr = _match_expr(terms)
    if not expr:
        return []
    try:
        c = connect(db, readonly=True)
        rows = c.execute("""SELECT d.path, d.title, bm25(fts, 4.0, 1.0) AS s FROM fts JOIN docs d ON d.id = fts.rowid
                            WHERE fts MATCH ? ORDER BY s LIMIT ?""", (expr, n)).fetchall()
        c.close()
    except sqlite3.Error as e:
        if degraded is not None:
            degraded.append(f"index unavailable ({e}); scanned files directly")
        return scan(vault, terms, n)
    out = []
    for path, title, s in rows:
        try:
            body = (vault / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append({"path": path, "title": title, "snippet": T.snippet(split_frontmatter(body)[1], terms), "score": -s})
    return out


def scan(vault: Path, terms: list[str], n: int = 20) -> list[dict]:
    """No index: score files by how many query tokens they contain (a crude BM25 stand-in)."""
    qtok = list(dict.fromkeys(t for term in terms for t in T.content_tokens(term)))
    if not qtok:
        return []
    scored = []
    for f in iter_files(vault):
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = raw.lower()
        hits = [t for t in qtok if t in low]
        if not hits:
            continue
        rel = f.relative_to(vault).as_posix()
        title = title_of(rel, raw)
        tl = (title + " " + rel).lower()
        score = sum(1 + math.log(1 + low.count(t)) + (2 if t in tl else 0) for t in hits) / math.log(10 + len(raw) / 2000)
        scored.append((score, rel, title, raw))
    scored.sort(key=lambda x: -x[0])
    return [{"path": rel, "title": title, "snippet": T.snippet(split_frontmatter(raw)[1], terms), "score": s}
            for s, rel, title, raw in scored[:n]]
