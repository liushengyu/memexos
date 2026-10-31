#!/usr/bin/env python3
"""Leak scan for this repository: secrets, personal terms and machine-specific details.

    python3 tools/leak_scan.py --staged        scan what is about to be committed (pre-commit hook)
    python3 tools/leak_scan.py --all           scan every tracked file (CI, before a release)
    python3 tools/leak_scan.py PATH...         scan given files or directories
    python3 tools/leak_scan.py --all --titles-from DIR
                                               also flag distinctive words taken from file names under DIR
                                               (point it at a private notes folder to catch examples copied from it)

Three layers:
  1. Built-in secret patterns (API keys, tokens, private keys, credentials in URLs).
  2. gitleaks, when installed.
  3. A private deny-list kept OUTSIDE the repository, because the list itself is personal:
     $MEMEX_DENYLIST, else ~/.config/memexos-dev/denylist.txt. One term per line, case-insensitive;
     ASCII terms match on word boundaries, other terms as substrings; a "re:" prefix means a regular expression.
     When the deny-list is missing the scan says so: in CI that is expected, locally it is not.

False positives go into .leakscan-allow (regular expressions matched against "path:line: text").
Exit code 1 when anything is found.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_PATTERNS = [
    (r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}", "API key (sk-…)"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}", "GitHub token"),
    (r"xox[abprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"AIza[0-9A-Za-z_\-]{35}", "Google API key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\b[a-z][a-z0-9+.\-]*://[^\s/:@\"'`<>]+:[^\s/@\"'`<>]{6,}@", "credentials in a URL"),
    (r"(?i)\b(?:api[_\-]?key|access[_\-]?token|auth[_\-]?token|secret|password|passwd)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_\-./+=%]{16,}[\"']",
     "credential assignment"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._\-]{24,}", "bearer token"),
    (r"[A-Za-z0-9._%+-]+@(?:qq|163|126|gmail|outlook|hotmail|icloud|yahoo|foxmail)\.com", "personal e-mail address"),
]
BINARY = {".pyc", ".pyo", ".woff2", ".woff", ".ttf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".sqlite", ".gguf"}


def denylist_path() -> Path:
    return Path(os.environ.get("MEMEX_DENYLIST") or Path.home() / ".config" / "memexos-dev" / "denylist.txt").expanduser()


def compile_terms(lines: list[str]) -> list[tuple[re.Pattern, str]]:
    out = []
    for raw in lines:
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if t.startswith("re:"):
            out.append((re.compile(t[3:], re.I), t))
        elif re.fullmatch(r"[\x20-\x7e]+", t):
            out.append((re.compile(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", re.I), t))
        else:
            out.append((re.compile(re.escape(t), re.I), t))
    return out


def titles_terms(d: Path, min_cjk: int = 4, min_ascii: int = 7) -> list[tuple[re.Pattern, str]]:
    """Distinctive fragments of file names under a private folder (page titles, captured sources).

    Only long fragments are used (>= min_cjk CJK characters or >= min_ascii-letter ASCII words that are not
    common English), so the list flags copied examples without flagging ordinary vocabulary.
    """
    words = set()
    for dic in ("/usr/share/dict/words", "/usr/share/dict/american-english", "/usr/share/dict/british-english"):
        try:
            words |= {w.strip().lower() for w in open(dic, encoding="utf-8", errors="ignore")}
        except OSError:
            pass
    allow_terms = ROOT / ".leakscan-allow-terms"
    public = {l.strip().lower() for l in allow_terms.read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.startswith("#")} if allow_terms.exists() else set()
    seen = set()
    for p in d.rglob("*"):
        if not p.is_file():
            continue
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", p.stem)
        for frag in re.findall(r"[㐀-鿿]{%d,}" % min_cjk, stem):
            if frag not in public:
                seen.add(frag)
        for w in re.findall(r"[A-Za-z]{%d,}" % min_ascii, stem):
            lw = w.lower()
            if lw not in words and lw.rstrip("s") not in words and lw not in public:
                seen.add(lw)
    seen -= public
    return compile_terms(sorted(seen))


def tracked_files() -> list[str]:
    r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split("\0") if f]


def staged() -> dict[str, str]:
    r = subprocess.run(["git", "-C", str(ROOT), "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                       capture_output=True, text=True, check=True)
    out = {}
    for f in [x for x in r.stdout.split("\0") if x]:
        if Path(f).suffix.lower() in BINARY:
            continue
        b = subprocess.run(["git", "-C", str(ROOT), "show", f":{f}"], capture_output=True).stdout
        out[f] = b.decode("utf-8", "replace")
    return out


def read_paths(paths: list[str]) -> dict[str, str]:
    out = {}
    for p in paths:
        pp = Path(p)
        files = [pp] if pp.is_file() else [x for x in pp.rglob("*") if x.is_file() and not {".git", "__pycache__", ".venv"} & set(x.parts)]
        for f in files:
            if f.suffix.lower() in BINARY:
                continue
            try:
                out[str(f)] = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return out


def allow_rules() -> list[re.Pattern]:
    p = ROOT / ".leakscan-allow"
    if not p.exists():
        return []
    return [re.compile(l.strip()) for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def scan(texts: dict[str, str], rules: list[tuple[re.Pattern, str, str]], allow: list[re.Pattern]) -> list[str]:
    hits = []
    for path, text in texts.items():
        if path.endswith(".leakscan-allow"):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for rx, label, kind in rules:
                m = rx.search(line)
                if not m:
                    continue
                where = f"{path}:{n}: {line.strip()[:160]}"
                if any(a.search(where) for a in allow):
                    continue
                # Never print the matched personal term itself in full: CI logs are public.
                hits.append(f"[{kind}] {path}:{n}: matched {label if kind == 'secret' else _mask(m.group(0))}")
    return hits


def _mask(s: str) -> str:
    return s[:2] + "…" + f"({len(s)} chars)" if len(s) > 3 else "…"


def gitleaks(texts: dict[str, str]) -> list[str]:
    exe = shutil.which("gitleaks")
    if not exe or not texts:
        return []
    with tempfile.TemporaryDirectory(prefix="memex-leak-") as d:
        for path, text in texts.items():
            dest = Path(d) / path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        r = subprocess.run([exe, "dir", d, "--no-banner", "--redact", "--exit-code", "3", "-l", "error",
                            "--report-format", "csv", "--report-path", "-"], capture_output=True, text=True)
        if r.returncode == 3:
            rows = [l for l in r.stdout.splitlines()[1:] if l.strip()]
            return [f"[gitleaks] {l.replace(d + '/', '')[:200]}" for l in rows] or ["[gitleaks] leaks found"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--titles-from", help="private folder whose file names supply extra terms")
    ap.add_argument("--strict", action="store_true", help="private-title matches also fail the scan")
    ap.add_argument("--require-denylist", action="store_true", help="fail when the private deny-list is missing")
    a = ap.parse_args()

    texts = staged() if a.staged else read_paths([str(ROOT / f) for f in tracked_files()]) if a.all else read_paths(a.paths or ["."])
    texts = {os.path.relpath(k, ROOT) if os.path.isabs(k) else k: v for k, v in texts.items()}
    rules = [(re.compile(p), label, "secret") for p, label in SECRET_PATTERNS]
    dl = denylist_path()
    if dl.exists():
        rules += [(rx, label, "deny-list") for rx, label in compile_terms(dl.read_text(encoding="utf-8").splitlines())]
    else:
        print(f"leak-scan: private deny-list not found at {dl} (only secret patterns are checked)", file=sys.stderr)
        if a.require_denylist:
            return 1
    if a.titles_from:
        rules += [(rx, label, "private title") for rx, label in titles_terms(Path(a.titles_from).expanduser())]
    hits = scan(texts, rules, allow_rules()) + gitleaks(texts)
    for h in hits:
        print(h)
    advisory = [h for h in hits if h.startswith("[private title]")]
    blocking = [h for h in hits if h not in advisory]
    print(f"leak-scan: {len(texts)} files, {len(blocking)} finding(s)"
          + (f", {len(advisory)} advisory match(es) against private titles: review them, add public terms to "
             f".leakscan-allow-terms" if advisory else ""), file=sys.stderr)
    return 1 if blocking or (advisory and a.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
