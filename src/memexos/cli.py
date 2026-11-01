"""`memex`: the single entry point for people, agents and scheduled jobs.

Read-only commands (search, get, help, catalog list/stats) must work inside a
read-only sandbox, which is how some coding agents run tools. They dispatch
before anything that writes (state directory, locks, logs).
"""

from __future__ import annotations

import argparse
import importlib
import sys

from memexos import __version__, i18n
from memexos import config as config_mod

# command -> (module, function). Modules are imported lazily so a read-only
# `memex search` does not pay for (or fail on) the write-side machinery.
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("memexos.cli", "cmd_init"),
    "config": ("memexos.cli", "cmd_config"),
    "doctor": ("memexos.doctor", "main"),
    "help": ("memexos.agenthelp", "main"),
    "search": ("memexos.search.cli", "cmd_search"),
    "get": ("memexos.search.cli", "cmd_get"),
    "reindex": ("memexos.search.cli", "cmd_reindex"),
    "secret": ("memexos.cli", "cmd_secret"),
    "ingest": ("memexos.pipeline.compile", "cmd_ingest"),
    "lint": ("memexos.pipeline.compile", "cmd_lint"),
    "fix": ("memexos.pipeline.compile", "cmd_fix"),
    "correct": ("memexos.pipeline.compile", "cmd_correct"),
    "pause": ("memexos.cli", "cmd_pause"),
    "fetch": ("memexos.capture.fetch", "cmd_fetch"),
    "add-url": ("memexos.capture.add", "cmd_add_url"),
    "add-video": ("memexos.capture.video", "cmd_add_video"),
    "add-visual": ("memexos.capture.visual", "cmd_add_visual"),
    "cookies": ("memexos.capture.cookies", "cmd_cookies"),
    "sessions": ("memexos.intake.sessions", "cmd_sessions"),
    "intake": ("memexos.intake.admit", "cmd_intake"),
    "catalog": ("memexos.catalog.classify", "cmd_catalog"),
    "resume": ("memexos.cli", "cmd_resume"),
}


def cmd_init(cfg_loader, argv: list[str]) -> int:
    from memexos import scaffold
    ap = argparse.ArgumentParser(prog="memex init", description="Create a knowledge base and your settings.")
    ap.add_argument("path", nargs="?", help="where to create it (default: vault.path from your settings, ~/memex)")
    ap.add_argument("--lang", choices=i18n.available(), help="language of prompts, logs and the console")
    ap.add_argument("--no-user-config", action="store_true", help="do not write ~/.config/memexos/config.toml")
    a = ap.parse_args(argv)
    cfg = cfg_loader(a.path)
    lang = a.lang or cfg.language
    for line in scaffold.init(cfg.vault, lang=lang, write_user_config=not a.no_user_config):
        print(line)
    return 0


def cmd_config(cfg_loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex config")
    ap.add_argument("action", nargs="?", default="show", choices=["show", "path", "get"])
    ap.add_argument("key", nargs="?")
    a = ap.parse_args(argv)
    cfg = cfg_loader(None)
    if a.action == "path":
        print(f"user config:  {cfg.paths.config_file}")
        print(f"vault:        {cfg.vault}")
        print(f"state:        {cfg.paths.state}")
        print(f"cache:        {cfg.paths.cache}")
        return 0
    if a.action == "get":
        v = cfg.get(a.key or "")
        print(v if not isinstance(v, (dict, list)) else __import__("json").dumps(v, ensure_ascii=False, indent=2))
        return 0 if v is not None else 1
    print("# sources: " + " < ".join(cfg.sources))
    print(cfg.as_toml_like())
    return 0


def cmd_secret(cfg_loader, argv: list[str]) -> int:
    import getpass
    from memexos import credentials
    ap = argparse.ArgumentParser(prog="memex secret", description="Store an API key in the OS keychain.")
    ap.add_argument("action", choices=["set", "check"])
    ap.add_argument("name", help="the name settings refer to, e.g. ANTHROPIC_API_KEY")
    a = ap.parse_args(argv)
    if a.action == "check":
        ok = credentials.available(a.name)
        print(f"{a.name}: {'found' if ok else 'not found'}")
        return 0 if ok else 1
    value = getpass.getpass(f"{a.name}: ")
    print(f"stored in {credentials.put(a.name, value)}")
    return 0


def cmd_pause(cfg_loader, argv: list[str]) -> int:
    cfg = cfg_loader(None)
    cfg.paths.state.mkdir(parents=True, exist_ok=True)
    (cfg.paths.state / "PAUSE").write_text(" ".join(argv) or "paused by hand\n")
    print(i18n.t("pipeline.paused", path=cfg.paths.state / "PAUSE"))
    return 0


def cmd_resume(cfg_loader, argv: list[str]) -> int:
    cfg = cfg_loader(None)
    (cfg.paths.state / "PAUSE").unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        argv = ["help", *argv[1:]]
    if argv[0] in ("-V", "--version"):
        print(f"memex {__version__}")
        return 0
    cmd, rest = argv[0], argv[1:]
    # `memex <cmd> --help` for agent-facing commands shows the layered help, not argparse's.
    if rest[:1] in (["-h"], ["--help"]) and cmd in ("search", "get", "catalog", "add-url", "add-video", "feedback", "usage"):
        cmd, rest = "help", [cmd]

    def loader(vault=None):
        try:
            cfg = config_mod.load(vault)
        except config_mod.ConfigError as e:
            print(f"memex: {e}", file=sys.stderr)
            raise SystemExit(2)
        i18n.set_language(cfg.language)
        return cfg

    target = COMMANDS.get(cmd)
    if not target:
        loader()
        print(i18n.t("cli.unknown", cmd=cmd), file=sys.stderr)
        return 2
    mod_name, fn_name = target
    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith("memexos"):
            loader()
            print(i18n.t("cli.not_yet", cmd=cmd), file=sys.stderr)
            return 2
        raise
    return getattr(mod, fn_name)(loader, rest) or 0


if __name__ == "__main__":
    raise SystemExit(main())
