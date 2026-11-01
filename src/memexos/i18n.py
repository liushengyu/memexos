"""Language packs for everything a person or a model reads: messages, prompts, templates.

Messages live in locale/<lang>.toml as nested tables and are addressed with
dotted keys: t("init.done", path=...). Prompts are markdown files in
prompts/<lang>/<name>.md; vault templates in templates/vault/<lang>/.
A missing translation falls back to English, so a partial pack still works.
"""

from __future__ import annotations

import functools
import string
import tomllib
from importlib import resources

FALLBACK = "en"
_lang = FALLBACK


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in available() else FALLBACK


def language() -> str:
    return _lang


@functools.cache
def available() -> tuple[str, ...]:
    return tuple(sorted(p.name[:-5] for p in resources.files("memexos").joinpath("locale").iterdir()
                        if p.name.endswith(".toml")))


@functools.cache
def _pack(lang: str) -> dict:
    p = resources.files("memexos").joinpath("locale", f"{lang}.toml")
    if not p.is_file():
        return {}
    flat: dict[str, str] = {}

    def walk(prefix: str, d: dict) -> None:
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                walk(key, v)
            else:
                flat[key] = v
    walk("", tomllib.loads(p.read_text(encoding="utf-8")))
    return flat


def t(name: str, lang: str | None = None, /, **kw) -> str:
    """The message `name` in `lang` (default: the current language), formatted with kw. The two parameters are
    positional-only, so placeholders may be called anything, {key} and {name} included."""
    lang = lang or _lang
    msg = _pack(lang).get(name) or _pack(FALLBACK).get(name) or name
    if isinstance(msg, list):
        return msg  # type: ignore[return-value]
    try:
        return msg.format(**kw) if kw else msg
    except (KeyError, IndexError):
        return msg


def has(key: str, lang: str | None = None) -> bool:
    return key in _pack(lang or _lang) or key in _pack(FALLBACK)


def _resource(kind: str, name: str, lang: str | None):
    lang = lang or _lang
    for lg in (lang, FALLBACK):
        p = resources.files("memexos").joinpath(kind, lg, name)
        if p.is_file():
            return p
    raise FileNotFoundError(f"{kind}/{lang}/{name}")


def prompt(name: str, lang: str | None = None, /, **kw) -> str:
    """Load prompts/<lang>/<name>.md and fill $placeholders (string.Template, so JSON braces are safe)."""
    text = _resource("prompts", name if name.endswith(".md") else name + ".md", lang).read_text(encoding="utf-8")
    return string.Template(text).safe_substitute(**kw) if kw else text


def template(name: str, lang: str | None = None) -> str:
    """templates/vault/<lang>/<name>, falling back to templates/vault/common/<name>."""
    lang = lang or _lang
    for lg in (lang, FALLBACK, "common"):
        p = resources.files("memexos").joinpath("templates", "vault", lg, name)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"templates/vault/*/{name}")
