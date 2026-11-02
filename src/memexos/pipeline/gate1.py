"""Gate 1: program checks before publishing. Reads the staging worktree's changes against BASE; changes nothing.

Returns {"passed", "errors", "warnings", "stats"}. Errors block publishing and go back to the writer verbatim;
warnings are only recorded. Only what was written this time (lines added in the diff) is checked: problems in
older content belong to the weekly lint, not to this batch.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import unicodedata
from pathlib import Path

from memexos import i18n, rawref

ALLOWED_DIRS = ("wiki/", "inbox/")
ALLOWED_FILES = {"index.md", "log.md"}
SPECIAL_LINKS = {"agents", "claude", "log", "index"}
WIKILINK = re.compile(r"\[\[([^\]]+?)\]\]")
SOURCES_HEADING = re.compile(r"^#{2,3}\s*(Sources|来源)\b", re.M)


def git(d: Path, *args: str) -> str:
    # core.quotepath=off: otherwise non-ASCII file names come back escaped ("\346\265…") and every path check fails
    return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(d), *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout


def added_lines(d: Path, base: str, path: str) -> str:
    diff = git(d, "diff", "--cached", "-U0", base, "--", path)
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def link_key(name: str) -> str:
    """A loose form of a page name for spotting near misses: no case, quote style, punctuation, spacing or plural -s."""
    s = unicodedata.normalize("NFKC", name).lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[\s'\"`.,:;!?()\[\]{}_-]+", "", s)
    return s[:-1] if len(s) > 4 and s.endswith("s") else s


def near_miss(target: str, keys: dict[str, str]) -> str | None:
    """The existing page a dangling link most likely meant, or None when it is plausibly a page still to be written."""
    k = link_key(target)
    if not k:
        return None
    if k in keys:
        return keys[k]
    close = difflib.get_close_matches(k, list(keys), n=1, cutoff=0.9)
    return keys[close[0]] if close and len(k) >= 6 else None


def link_target(raw: str) -> str:
    return raw.split("|", 1)[0].split("#", 1)[0].strip()


def check(d: Path, base: str, kind: str = "ingest") -> dict:
    d = Path(d)
    errors: list[str] = []
    warnings: list[str] = []
    git(d, "add", "-A")
    changes = []
    tokens = git(d, "diff", "--cached", "--name-status", "-M", "-z", base).split("\0")
    i = 0
    while i < len(tokens) and tokens[i]:
        st = tokens[i][0]
        n = 2 if st in "RC" else 1
        changes.append((st, tokens[i + 1:i + 1 + n]))
        i += 1 + n

    # 1. write scope: raw/ untouched; otherwise only wiki/, inbox/, index.md, log.md
    for st, paths in changes:
        for p in paths:
            if p.startswith("raw/"):
                errors.append(i18n.t("gate1.raw_touched", status=st, path=p))
            elif not (p.startswith(ALLOWED_DIRS) or p in ALLOWED_FILES):
                errors.append(i18n.t("gate1.out_of_scope", status=st, path=p))

    added = [p[-1] for st, p in changes if st in "AR" and p[-1].startswith("wiki/") and p[-1].endswith(".md")]
    modified = [p[-1] for st, p in changes if st == "M" and p[-1].startswith("wiki/") and p[-1].endswith(".md")]
    deleted = [p[0] for st, p in changes if st == "D" and p[0].startswith("wiki/")]
    for p in deleted:
        warnings.append(i18n.t("gate1.deleted", path=p))

    pages: dict[str, list[str]] = {}
    for root, _, files in os.walk(d / "wiki"):
        for f in files:
            if f.endswith(".md"):
                pages.setdefault(f[:-3].lower(), []).append(os.path.relpath(os.path.join(root, f), d))
    names = set(pages)
    raw_names = rawref.index(d)
    for rp in raw_names:
        f = os.path.basename(rp)
        names.add(f.lower())
        names.add(os.path.splitext(f)[0].lower())
    names |= SPECIAL_LINKS

    index_path = d / "index.md"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    index_links = {link_target(m).lower() for m in WIKILINK.findall(index_text)}

    # 2. new pages: not empty, frontmatter closed, a Sources section, listed in the index, unique name
    for p in added:
        name = os.path.basename(p)[:-3]
        text = (d / p).read_text(encoding="utf-8")
        if len(text.strip()) < 80:
            errors.append(i18n.t("gate1.empty", path=p))
        if text.startswith("---") and text.count("\n---") < 1:
            errors.append(i18n.t("gate1.frontmatter", path=p))
        if not SOURCES_HEADING.search(text):
            errors.append(i18n.t("gate1.no_sources", path=p))
        elif "raw/" not in text:
            warnings.append(i18n.t("gate1.sources_no_raw", path=p))
        if name.lower() not in index_links:
            errors.append(i18n.t("gate1.not_indexed", name=name))
        if len(pages.get(name.lower(), [])) > 1:
            errors.append(i18n.t("gate1.duplicate", paths=" / ".join(pages[name.lower()])))

    # 3. the index must not link to deleted pages
    for p in deleted:
        name = os.path.basename(p)[:-3]
        if name.lower() in index_links and name.lower() not in pages:
            errors.append(i18n.t("gate1.index_dangling", name=name))

    # 4. log.md is append-only and must get an entry of this kind
    try:
        base_log = git(d, "show", f"{base}:log.md")
    except subprocess.CalledProcessError:
        base_log = ""
    new_log = (d / "log.md").read_text(encoding="utf-8") if (d / "log.md").exists() else ""
    if not new_log.startswith(base_log.rstrip("\n")):
        errors.append(i18n.t("gate1.log_rewritten"))
    else:
        appended = new_log[len(base_log.rstrip("\n")):]
        kinds = re.findall(r"^## \[\d{4}-\d{2}-\d{2}\] (ingest|lint|query|schema) \|", appended, re.M)
        if kind not in kinds:
            errors.append(i18n.t("gate1.log_missing", kind=kind))

    # 5. in new text: cited raw files must exist; links to pages that do not exist yet are only warnings
    for p in added + modified + (["index.md"] if any(pp == ["index.md"] for _, pp in changes) else []):
        new_text = added_lines(d, base, p)
        for rp in rawref.find(new_text, raw_names, d):
            if not (d / rp).exists():
                errors.append(i18n.t("gate1.missing_raw", path=p, raw=rp))
        dangling = sorted({link_target(m) for m in WIKILINK.findall(new_text)
                           if link_target(m) and link_target(m).lower() not in names
                           and not link_target(m).lower().endswith((".png", ".jpg", ".jpeg", ".pdf"))})
        # a link that is almost an existing page name is a mistake, not a page to write later
        keys = {link_key(Path(v[0]).stem): Path(v[0]).stem for v in pages.values()}
        for t in list(dangling):
            meant = near_miss(t, keys)
            if meant:
                errors.append(i18n.t("gate1.near_link", path=p, link=t, page=meant))
                dangling.remove(t)
        if dangling:
            warnings.append(i18n.t("gate1.dangling", path=p, links=", ".join(dangling[:8]) + (" …" if len(dangling) > 8 else "")))

    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "stats": {"added_pages": len(added), "modified_pages": len(modified), "deleted_pages": len(deleted)}}
