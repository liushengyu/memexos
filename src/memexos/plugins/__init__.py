"""Optional plugins for sites that need your own logged-in session. All are off by default.

A plugin is a module with any of:
  NAME                       its config section is [plugins.<NAME>]; enabled = true turns it on
  FETCH = {command: fn}      read-only `memex fetch <command>` handlers: fn(cfg, args, n, as_json) -> str
  handles(url) -> bool       and capture(cfg, url, title, via) -> Path: `memex add-url` for that site's links
  collect(cfg, state, args)  for `memex intake`: returns batches of items (dicts with at least "key")
  ADMISSION = {lang: text}   the default stance for this channel, appended to the admission rules
Built in: x (bookmarks, likes, tweets and long-form articles) and bilibili (favorites).
Third-party plugins can register under the "memexos.plugins" entry-point group.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points

BUILTIN = {"x": "memexos.plugins.x", "bilibili": "memexos.plugins.bilibili"}


def _all() -> dict[str, str]:
    mods = dict(BUILTIN)
    try:
        for ep in entry_points(group="memexos.plugins"):
            mods.setdefault(ep.name, ep.value)
    except Exception:  # noqa: BLE001
        pass
    return mods


def load(name: str):
    return importlib.import_module(_all()[name])


def enabled(cfg) -> dict:
    out = {}
    for name in _all():
        if cfg.get(f"plugins.{name}.enabled"):
            try:
                out[name] = load(name)
            except ImportError:
                continue
    return out


def fetch_commands() -> list[str]:
    out = []
    for name in _all():
        try:
            out += list(getattr(load(name), "FETCH", {}))
        except ImportError:
            continue
    return out


def fetch(cfg, what: str, args: list[str], n: int, as_json: bool) -> str | None:
    """Plugin fetch commands are read-only, so they work even when the plugin is not enabled for intake."""
    for name in _all():
        mod = load(name)
        fn = getattr(mod, "FETCH", {}).get(what)
        if fn:
            return fn(cfg, args, n, as_json)
    return None


def capture_handler(cfg, url: str):
    for name in _all():
        try:
            mod = load(name)
        except ImportError:
            continue
        if hasattr(mod, "handles") and mod.handles(url):
            return mod
    return None
