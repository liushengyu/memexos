"""Built-in search backend: an SQLite FTS5 index over the vault's markdown files.

It needs nothing beyond the Python standard library, so a fresh install can
search right away. Text is stored pre-tokenized (CJK bigrams + lowercase words,
see memexos.search.text) in a plain unicode61 FTS5 table and ranked with BM25,
titles weighted higher than bodies.

The index lives in the cache directory and is updated incrementally (by file
size and mtime). Reading never needs write access: when the index cannot be
opened at all (missing, or a read-only sandbox without it), search falls back
to scanning the files directly, slower but correct.

The index is derived from the files and can always be rebuilt from them. A
damaged index file (a crash of the disk or the machine, a copy cut short, a
file that is not a database) is moved aside and rebuilt by the next update;
an update checks the whole index for damage once a day, because a damaged
index can keep answering queries with pages silently missing.
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


CHECK_EVERY = 86400        # seconds between full integrity checks


def damaged(e: sqlite3.Error) -> bool:
    """The error means the file itself is unusable (corrupt, or not a database). Busy, locked or missing-table errors
    are not damage."""
    name = getattr(e, "sqlite_errorname", "") or ""
    if name.startswith("SQLITE_CORRUPT") or name == "SQLITE_NOTADB":
        return True
    return any(x in str(e).lower() for x in ("malformed", "not a database"))


def damage_note(db: Path) -> Path:
    """Left by a search that found the index damaged (searches may not write the index itself); the next update
    rebuilds it."""
    return db.with_name(db.name + ".damaged")


def _set_aside(db: Path) -> None:
    """Keep the last damaged copy next to the index (for a bug report), then start from nothing."""
    for suffix in ("", "-journal"):
        p = db.with_name(db.name + suffix)
        if p.exists():
            p.replace(db.with_name(db.name + suffix + ".corrupt"))
    damage_note(db).unlink(missing_ok=True)


def check(db: Path) -> str:
    """'' when the index is consistent, else SQLite's description of the damage."""
    try:
        c = sqlite3.connect(db, timeout=30)      # the integrity check is issued as an INSERT, so not read-only
        try:
            bad = [r[0] for r in c.execute("PRAGMA quick_check(5)") if r[0] != "ok"]
            if not bad and c.execute("SELECT 1 FROM sqlite_master WHERE name='fts'").fetchone():
                c.execute("INSERT INTO fts(fts) VALUES('integrity-check')")
        finally:
            c.close()
        return "; ".join(bad)
    except sqlite3.DatabaseError as e:
        if damaged(e):
            return str(e)
        raise


def update(vault: Path, db: Path) -> dict:
    """Bring the index in line with the vault. Cheap when nothing changed (one stat per file). A damaged index is
    rebuilt from the files; the result then says why under "rebuilt"."""
    why, checked = "", None
    note = damage_note(db)
    if note.exists():
        why = note.read_text(encoding="utf-8", errors="replace").strip() or "a search found the index damaged"
    elif db.exists() and time.time() - _last_check(db) > CHECK_EVERY:
        checked = time.time()
        why = check(db)
    if why:
        _set_aside(db)
    try:
        r = _update(vault, db, checked)
    except sqlite3.DatabaseError as e:
        if not damaged(e):
            raise
        why = str(e)
        _set_aside(db)
        r = _update(vault, db)
    if why:
        r["rebuilt"] = why[:300]
    return r


def _last_check(db: Path) -> float:
    try:
        c = connect(db, readonly=True)
        try:
            v = c.execute("SELECT v FROM meta WHERE k='checked'").fetchone()
        finally:
            c.close()
        return float(v[0]) if v else 0.0
    except (sqlite3.Error, ValueError):
        return 0.0


def _update(vault: Path, db: Path, checked: float | None = None) -> dict:
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
    if checked or not c.execute("SELECT 1 FROM meta WHERE k='checked'").fetchone():   # (built just now: nothing to check)
        c.execute("INSERT OR REPLACE INTO meta VALUES('checked', ?)", (str(checked or time.time()),))
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


def _parts(terms: list[str]) -> list[str]:
    """The query as phrases (space-joined tokens). A short term (a keyword, up to 3 tokens) must match as a phrase, so
    "rank fusion" or a CJK keyword (its bigrams) matches only in that order; a long term (the whole question) is split
    into its content tokens, because a sentence never appears verbatim."""
    parts: list[str] = []
    for t in terms:
        toks = [x.replace('"', "") for x in T.tokens(t)]
        if not toks:
            continue
        if len(toks) <= 3 or all(T.CJK.fullmatch(x) for x in toks) and len(toks) <= 6:
            parts.append(" ".join(toks))
        else:
            parts += [x for x in T.content_tokens(t) if (len(x) > 1 or T.CJK.fullmatch(x)) and '"' not in x]
    return list(dict.fromkeys(p for p in parts if p))


def _match_expr(terms: list[str]) -> str:
    return " OR ".join(f'"{p}"' for p in _parts(terms))


# BM25 with the Lucene / Elasticsearch weight for how rare a term is: log(1 + (N - df + 0.5) / (df + 0.5)). SQLite's own
# bm25() clamps that weight to almost zero for a term found in more than half of the documents, which in a small
# knowledge base about one subject is exactly the subject's own name. SQLite still finds the candidates; their scores
# are recomputed here from the index (stemmed term positions, column lengths), so stemming and phrases behave the same.
K1, B = 1.2, 0.75
COLUMNS = {"title": 4.0, "body": 1.0}


def _varints(blob: bytes) -> list[int]:
    """SQLite varints (as in the fts5 docsize table): 7 bits per byte, high-order groups first, a set top bit meaning
    "more follows"; the ninth byte contributes all 8 bits."""
    out, i = [], 0
    while i < len(blob):
        v = 0
        for k in range(9):
            byte = blob[i]
            i += 1
            if k == 8:
                v = (v << 8) | byte
                break
            v = (v << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        out.append(v)
    return out


def idf(n: int, df: int, keyword: bool) -> float:
    """SQLite's weight (near zero above half the documents) for tokens of the whole question, which silences the many
    common tokens a sentence brings, especially CJK bigrams; the never-zero Lucene weight for keywords, which carry the
    subject even when the subject is on most pages of a small knowledge base."""
    if keyword:
        return math.log(1 + (n - df + 0.5) / (df + 0.5))
    return max(math.log((n - df + 0.5) / (df + 0.5)), 1e-6)


def _rescore(c: sqlite3.Connection, parts: list[str], cands: list[int], keywords: set[str] | frozenset = frozenset()) -> dict[int, float]:
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.memex_vi USING fts5vocab(main, fts, instance)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.memex_q USING fts5(x, tokenize='porter unicode61 remove_diacritics 2')")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.memex_qv USING fts5vocab(temp, memex_q, instance)")
    c.execute("DELETE FROM temp.memex_q")
    c.executemany("INSERT INTO temp.memex_q(rowid, x) VALUES(?, ?)", list(enumerate(parts, 1)))
    stems: dict[int, list[str]] = {}
    for doc, term in c.execute("SELECT doc, term FROM temp.memex_qv ORDER BY doc, offset"):
        stems.setdefault(doc, []).append(term)
    sizes = {rid: _varints(sz) for rid, sz in c.execute("SELECT id, sz FROM fts_docsize")}
    n = max(len(sizes), 1)
    avg = [max(sum(v[i] for v in sizes.values() if len(v) > i) / n, 1) for i in range(len(COLUMNS))]
    want = set(cands)
    terms = sorted({t for ts in stems.values() for t in ts})
    pos: dict[tuple[str, int, str], set[int]] = {}
    for i in range(0, len(terms), 200):
        chunk = terms[i:i + 200]
        q = f"SELECT term, doc, col, offset FROM temp.memex_vi WHERE term IN ({','.join('?' * len(chunk))})"
        for term, doc, col, off in c.execute(q, chunk):
            if doc in want:
                pos.setdefault((term, doc, col), set()).add(off)
    total_avg = sum(avg)
    scores = dict.fromkeys(cands, 0.0)
    for i, part in enumerate(parts, 1):
        toks = stems.get(i)
        if not toks:
            continue
        df = c.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", ('"' + part.replace('"', "") + '"',)).fetchone()[0]
        w_idf = idf(n, df, part in keywords)
        for doc in cands:
            # as SQLite's bm25(): column weights scale the hit count before saturation; the length is the whole row's
            f = 0.0
            for col, w in COLUMNS.items():
                first = pos.get((toks[0], doc, col))
                if first:
                    f += w * sum(1 for o in first if all(o + k in pos.get((t, doc, col), ()) for k, t in enumerate(toks[1:], 1)))
            if f:
                length = sum(sizes.get(doc, [])) or total_avg
                scores[doc] += w_idf * f * (K1 + 1) / (f + K1 * (1 - B + B * length / total_avg))
    return scores


def search(vault: Path, db: Path, terms: list[str], n: int = 20, degraded: list | None = None,
           keywords: list[str] | None = None) -> list[dict]:
    """[{path, title, snippet, score}] best first. `keywords` (a subset of terms) are the topical words. Falls back to
    a file scan when the index is unusable."""
    parts = _parts(terms)
    kw_parts = set(_parts(keywords)) if keywords else set()
    if not parts:
        return []
    expr = " OR ".join(f'"{p}"' for p in parts)
    try:
        c = connect(db, readonly=True)
        cands = [r for (r,) in c.execute("SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 4.0, 1.0) LIMIT ?",
                                         (expr, max(200, n * 10)))]
        try:
            scores = _rescore(c, parts, cands, kw_parts)
        except sqlite3.Error:                  # an SQLite without fts5vocab: keep SQLite's own order
            scores = {r: -i for i, r in enumerate(cands)}
        best = sorted(cands, key=lambda r: -scores[r])[:n]
        meta = {rid: (path, title) for rid, path, title in
                c.execute(f"SELECT id, path, title FROM docs WHERE id IN ({','.join('?' * len(best))})", best)} if best else {}
        c.close()
    except sqlite3.Error as e:
        if damaged(e):
            try:
                damage_note(db).write_text(str(e), encoding="utf-8")
            except OSError:
                pass                    # a read-only sandbox: the daily check of the next update finds it too
            if degraded is not None:
                degraded.append(f"the search index is damaged ({e}); scanned files directly. The next `memex reindex` rebuilds it")
        elif degraded is not None:
            degraded.append(f"index unavailable ({e}); scanned files directly")
        return scan(vault, terms, n)
    out = []
    for rid in best:
        if rid not in meta:
            continue
        path, title = meta[rid]
        try:
            body = (vault / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append({"path": path, "title": title, "snippet": T.snippet(split_frontmatter(body)[1], terms), "score": round(scores[rid], 4)})
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
