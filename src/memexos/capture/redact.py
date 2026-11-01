"""Scrub secrets from text before it is written into the vault (which is a git repository: written means kept).

Two layers:
  - exact values known to this machine: environment variables whose names match privacy.redact_env (shell-style
    patterns such as *_TOKEN) and the values in privacy.redact_files. Exact matching is far more reliable than patterns.
  - generic patterns for common key formats, credentials in URLs and credential assignments.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

PATTERNS = [
    (r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}", "API key (sk-)"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}", "GitHub token"),
    (r"xox[abprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"AKIA[0-9A-Z]{16}", "AWS key"),
    (r"AIza[0-9A-Za-z_\-]{35}", "Google API key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\b[a-z][a-z0-9+.\-]*://[^\s/:@\"'`]+:[^\s/@\"'`]{6,}@", "URL with credentials"),
    (r"(?i)\b(?:api[_\-]?key|access[_\-]?token|auth[_\-]?token|secret|password|passwd|sessdata|bili_jct)\b[\"']?\s*[:=]\s*[\"']?"
     r"[A-Za-z0-9_\-./+=%]{12,}", "credential assignment"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._\-]{20,}", "bearer token"),
    (r"(?i)\bcookie:\s*\S+=\S{12,}", "cookie header"),
]
MASK = "[redacted]"


def known_secrets(cfg) -> set[str]:
    vals: set[str] = set()
    pats = cfg.get("privacy.redact_env") or []
    for k, v in os.environ.items():
        if any(fnmatch.fnmatchcase(k.upper(), p.upper()) for p in pats) and len(v) >= 12:
            vals.add(v)
        for m in re.finditer(r"://[^\s/:@]+:([^\s/@]+)@", v):       # passwords inside proxy URLs
            vals.add(m.group(1))
    for f in cfg.get("privacy.redact_files") or []:
        try:
            for line in Path(os.path.expanduser(f)).read_text(encoding="utf-8", errors="ignore").splitlines():
                v = line.split("=", 1)[-1].strip().strip("\"'")
                if len(v) >= 12:
                    vals.add(v)
        except OSError:
            continue
    cookie_dir = cookie_dir_of(cfg)
    for cf in cookie_dir.glob("*.txt") if cookie_dir.is_dir() else []:
        try:
            for line in cf.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split("\t")
                if len(parts) >= 7 and len(parts[6]) >= 12:
                    vals.add(parts[6])
        except OSError:
            continue
    return {v for v in vals if len(v) >= 12}


def cookie_dir_of(cfg) -> Path:
    """Exported login cookies live next to the user config (~/.config/memexos/cookies), private to the user."""
    return Path(os.environ.get("MEMEX_COOKIE_DIR") or (cfg.paths.config_file.parent / "cookies"))


def scan(text: str, secrets: set[str]) -> list[str]:
    hits = []
    if any(v in text for v in secrets):
        hits.append("a secret known to this machine")
    hits += [label for pat, label in PATTERNS if re.search(pat, text)]
    return sorted(set(hits))


def scrub(text: str, secrets: set[str]) -> tuple[str, int]:
    n = 0
    for v in sorted(secrets, key=len, reverse=True):
        if v in text:
            n += text.count(v)
            text = text.replace(v, MASK)
    for pat, _ in PATTERNS:
        text, k = re.subn(pat, MASK, text)
        n += k
    return text, n
