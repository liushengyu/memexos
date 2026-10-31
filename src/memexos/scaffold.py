"""`memex init`: create an empty knowledge base and the user's settings file.

Never overwrites an existing file, so running it again on a vault only adds
what is missing (useful after an upgrade adds a new template).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from memexos import config as config_mod
from memexos import i18n
from memexos.config import VAULT_DIR
from memexos.util import atomic_write

DIRS = ["raw", "raw/feedback", "raw/assets", "wiki/sources", "wiki/entities", "wiki/concepts", "wiki/topics", "inbox", VAULT_DIR]
LEDGERS = ["ingested.txt", "held.txt", "corrected.txt", "fixed-reviews.txt"]


def vault_files(lang: str) -> dict[str, str]:
    return {
        "AGENTS.md": i18n.template("AGENTS.md", lang),
        "index.md": i18n.template("index.md", lang),
        "log.md": i18n.template("log.md", lang),
        ".gitattributes": i18n.template("gitattributes", lang),
        ".gitignore": i18n.template("gitignore", lang),
        f"{VAULT_DIR}/taxonomy.md": i18n.template("taxonomy.md", lang),
        f"{VAULT_DIR}/feeds.txt": i18n.template("feeds.txt", lang),
        f"{VAULT_DIR}/config.toml": (
            "# Settings that travel with this knowledge base (machine-specific settings live in your user config).\n"
            f'language = "{lang}"\n'),
        **{f"{VAULT_DIR}/{n}": "" for n in LEDGERS},
        # Empty directories are not tracked by git; keep them with a placeholder.
        **{f"{d}/.gitkeep": "" for d in ("raw/feedback", "raw/assets", "wiki/sources", "wiki/entities", "wiki/concepts",
                                         "wiki/topics", "inbox")},
    }


def user_config_text(vault: Path, lang: str) -> str:
    """The commented default config with this vault's path and language filled in."""
    text = config_mod.default_config_text()
    home = str(Path.home())
    shown = "~" + str(vault)[len(home):] if str(vault).startswith(home + os.sep) else str(vault)
    text = re.sub(r'(?m)^path = ".*"$', f'path = "{shown}"', text, count=1)
    text = re.sub(r'(?m)^language = ".*"$', f'language = "{lang}"', text, count=1)
    return text


def git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def init(path: str | os.PathLike, lang: str = "en", write_user_config: bool = True,
         user_config: str | os.PathLike | None = None) -> list[str]:
    """Create the vault at `path`. Returns the human-readable report lines."""
    i18n.set_language(lang)
    vault = Path(path).expanduser().resolve()
    out = []
    existed = vault.exists() and any(vault.iterdir())
    if existed:
        out.append(i18n.t("init.exists", path=vault))
    for d in DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    for rel, content in vault_files(lang).items():
        p = vault / rel
        if not p.exists():
            atomic_write(p, content)
    # Claude Code reads CLAUDE.md, Codex and most other agents read AGENTS.md: one file, two names.
    claude = vault / "CLAUDE.md"
    if not claude.exists() and not claude.is_symlink():
        try:
            claude.symlink_to("AGENTS.md")
        except OSError:
            atomic_write(claude, "See [AGENTS.md](AGENTS.md).\n")

    if not (vault / ".git").exists():
        git(vault, "init", "-q", "-b", "main")
        out.append(i18n.t("init.git_init"))
    git(vault, "add", "-A")
    if git(vault, "diff", "--cached", "--quiet").returncode != 0:
        r = git(vault, "commit", "-q", "-m", "init: MemexOS knowledge base")
        if r.returncode != 0 and ("user.email" in r.stderr or "identity" in r.stderr.lower()):
            # No git identity on this machine: give this repository its own, local one.
            git(vault, "config", "user.name", "MemexOS")
            git(vault, "config", "user.email", "memexos@localhost")
            git(vault, "commit", "-q", "-m", "init: MemexOS knowledge base")
    out.append(i18n.t("init.created", path=vault))

    if write_user_config:
        ucfg = Path(user_config).expanduser() if user_config else config_mod.user_config_path()
        if ucfg.exists():
            out.append(i18n.t("init.config_kept", path=ucfg))
        else:
            atomic_write(ucfg, user_config_text(vault, lang), mode=0o600)
            out.append(i18n.t("init.config_written", path=ucfg))
    out.append(i18n.t("init.next"))
    return out
