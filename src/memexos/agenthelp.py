"""`memex help [topic]`: layered help written for agents.

Layer one fits on one screen: when to use the knowledge base, the everyday
commands and the rules for answering. `memex help <topic>` or
`memex <command> --help` opens layer two. Read-only: works in any sandbox.
"""

from __future__ import annotations

import os
import string
import sys
from importlib import resources
from pathlib import Path

from memexos import i18n

TOPICS = ("search", "get", "catalog", "add-url", "add-video", "snapshot", "feedback", "usage", "admin")


def covered_domains(vault: Path, n: int = 8) -> list[str]:
    """The domains the knowledge base actually has sources in (from its catalog), most sources first. The taxonomy
    lists every domain that could be used, which says little about what is in this knowledge base."""
    import collections
    import json
    count: collections.Counter = collections.Counter()
    try:
        with open(vault / ".memex" / "catalog.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line).get("domain") or ""
                except ValueError:
                    continue
                if d:
                    count[d] += 1
    except OSError:
        return []
    return [d for d, _ in count.most_common(n)]


def topic_names(vault: Path, n: int = 8) -> list[str]:
    """Without a taxonomy yet, the overview pages say what the knowledge base covers."""
    d = vault / "wiki" / "topics"
    return sorted(p.stem for p in d.glob("*.md"))[:n] if d.is_dir() else []


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
            covers = covered_domains(cfg.vault) or topic_names(cfg.vault)
            lang = {"en": "English", "zh": "中文"}.get(cfg.language, cfg.language) if i18n.language() == "en" else \
                {"en": "英文", "zh": "中文"}.get(cfg.language, cfg.language)
            return string.Template(p.read_text(encoding="utf-8")).safe_substitute(
                vault=shown, domains=sep.join(covers) or "-", language=lang)
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
