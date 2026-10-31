"""Find the raw files a piece of text cites (wiki Sources sections, inbox reports, review findings).

Raw file names come from page and video titles, so they contain brackets,
full-width punctuation, question marks, even an inner ".pdf" ("…-report.pdf.md").
A single regular expression cannot know where such a name ends. Matching, from
most to least reliable:
  1. A path in backticks: `raw/…` is the whole path. "`raw/` … `name.md`" split forms are accepted too.
  2. Without backticks: from raw/ on, the longest prefix that names an existing file.
  3. Paths that match no existing file (a citation of a missing file, which gate 1 must catch): fall back to
     "extension followed by a separator", so ".pdf.md" never stops at ".pdf".
"""

from __future__ import annotations

import os
import re
from pathlib import Path

TICK = re.compile(r"`([^`\n]+)`")
SPLIT = re.compile(r"`raw/?`\s*(?:/|under|in|下的|里的|中的|目录下的|目录里的)\s*`([^`\n]+)`")
BARE = re.compile(r"raw/[^\s`'\"<>|*]+?\.(?:md|markdown|txt|html?|pdf|png|jpe?g|gif|webp|svg|json|csv)"
                  r"(?=$|[\s`'\"）)\]】|，。；、,;:：!！?？])", re.M)
SUFFIX = re.compile(r"(?::\d+(?:-\d+)?|#\S*)$")          # raw/x.md:12, raw/x.md#L3


def index(vault: str | os.PathLike) -> set[str]:
    """Every existing raw path (including raw/assets/), relative to the vault root."""
    out = set()
    root = Path(vault) / "raw"
    for d, _, files in os.walk(root):
        for f in files:
            if not f.startswith("."):
                out.add(Path(os.path.relpath(os.path.join(d, f), vault)).as_posix())
    return out


def _template(p: str) -> bool:
    return any(x in p for x in ("<", "YYYY", "*", "…", "..."))


def find(text: str, names: set[str] | None = None, vault: str | os.PathLike | None = None) -> list[str]:
    """Raw paths in order of appearance, de-duplicated. Pass `names` (from index()) when calling in a loop."""
    if names is None:
        names = index(vault) if vault else set()
    found: list[str] = []
    spans: list[tuple[int, int]] = []

    def add(p: str) -> None:
        p = p.strip().rstrip("，。；、,;:：")
        if p in names:
            found.append(p)
            return
        q = SUFFIX.sub("", p)
        if q in names:
            found.append(q)
        elif not _template(p) and re.search(r"\.[A-Za-z0-9]{1,8}$", q):
            found.append(q)                      # has an extension but does not exist: let gate 1 report it

    for m in SPLIT.finditer(text):
        add("raw/" + m.group(1).lstrip("/"))
        spans.append(m.span())
    vault_abs = Path(vault).expanduser().resolve() if vault else None

    def is_vault(prefix: str) -> bool:
        try:
            return vault_abs is not None and Path(os.path.expanduser(prefix)).resolve() == vault_abs
        except (OSError, RuntimeError):
            return False

    for m in TICK.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        s = m.group(1).strip()
        k = s.find("raw/")
        if k >= 0 and len(s) > k + 4 and (k == 0 or is_vault(s[:k])):   # `raw/…` or `<this vault>/raw/…`
            add(s[k:])
            spans.append(m.span())
    for m in re.finditer(r"raw/", text):
        i = m.start()
        if any(a <= i < b for a, b in spans) or (i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_.-")):
            continue
        line_end = text.find("\n", i)
        tail = text[i: line_end if line_end != -1 else len(text)][:400]
        best = next((tail[:j] for j in range(len(tail), 4, -1) if tail[:j] in names), None)
        if best:
            found.append(best)
            continue
        b = BARE.match(text, i)
        if b and not _template(b.group(0)):
            found.append(b.group(0))
    return list(dict.fromkeys(found))
