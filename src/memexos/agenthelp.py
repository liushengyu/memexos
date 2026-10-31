"""`memex help [topic]`: layered help written for agents.

Layer one fits on one screen: when to use the knowledge base, the everyday
commands and the rules for answering. `memex help <topic>` or
`memex <command> --help` opens layer two. Read-only: works in any sandbox.
"""

from __future__ import annotations

import os
import re
import string
import sys
from importlib import resources
from pathlib import Path

from memexos import i18n

TOPICS = ("search", "get", "catalog", "add-url", "add-video", "snapshot", "feedback", "usage", "admin")


def top_domains(vault: Path) -> list[str]:
    """Top-level domains from the vault's taxonomy table (in order of first appearance)."""
    try:
        text = (vault / ".memex" / "taxonomy.md").read_text(encoding="utf-8")
    except OSError:
        return []
    sec = text.split("<!-- domains", 1)[-1]
    tops = []
    for line in sec.splitlines():
        m = re.match(r"^\|\s*([^|`\-\s][^|]*?)\s*\|\s*[^|]+?\s*\|\s*[^|]+?\s*\|\s*$", line)
        if m and not tops and m.group(1) in ("Top", "一级"):
            continue
        if m:
            tops.append(m.group(1))
        elif tops and not line.startswith("|"):
            break
    return list(dict.fromkeys(t for t in tops if t not in ("Top", "一级")))


def text(topic: str, cfg) -> str | None:
    if topic == "admin":
        return i18n.t("help.admin")
    for lang in (i18n.language(), i18n.FALLBACK):
        p = resources.files("memexos").joinpath("help", lang, f"{topic}.txt")
        if p.is_file():
            home = str(Path.home().resolve())
            v = str(cfg.vault)
            shown = "~" + v[len(home):] if v.startswith(home + os.sep) else v
            sep = "、" if i18n.language() == "zh" else ", "
            return string.Template(p.read_text(encoding="utf-8")).safe_substitute(
                vault=shown, domains=sep.join(top_domains(cfg.vault)) or "-")
    return None


def main(loader, argv: list[str]) -> int:
    cfg = loader()
    topic = argv[0] if argv else "main"
    if topic in ("-h", "--help", "help"):
        topic = "main"
    out = text(topic, cfg)
    if out is None:
        print(f"{topic}?\n\n{text('main', cfg)}", file=sys.stderr)
        return 1
    print(out.rstrip())
    return 0
