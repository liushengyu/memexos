"""Configuration: one place that knows where things live and which tools to use.

Precedence (later wins): built-in defaults < <vault>/.memex/config.toml <
user config (~/.config/memexos/config.toml or $MEMEX_CONFIG) < environment.

Environment overrides: MEMEX_<SECTION>__<KEY>=value (double underscore between
levels, e.g. MEMEX_SEARCH__RERANK=none, MEMEX_BACKENDS__CODEX__MODEL=...), plus
the shortcuts MEMEX_VAULT, MEMEX_LANG, MEMEX_STATE_DIR and MEMEX_CACHE_DIR.
Values that look like JSON (lists, numbers, true/false) are parsed as JSON.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from memexos.util import expand

ENV_PREFIX = "MEMEX_"
VAULT_DIR = ".memex"


class ConfigError(Exception):
    pass


def _xdg(var: str, fallback: str) -> Path:
    v = os.environ.get(var)
    return Path(v).expanduser() if v else Path.home() / fallback


def user_config_path() -> Path:
    if os.environ.get("MEMEX_CONFIG"):
        return Path(os.environ["MEMEX_CONFIG"]).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "memexos" / "config.toml"


def default_config_text() -> str:
    return resources.files("memexos").joinpath("default_config.toml").read_text(encoding="utf-8")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _parse_env_value(v: str) -> Any:
    try:
        return json.loads(v)
    except ValueError:
        return v


def _env_overrides(environ: dict[str, str], defaults: dict) -> dict:
    """MEMEX_A__B__C=v → {"a": {"b": {"c": v}}}.

    Environment variable names cannot contain dashes, so a part such as
    CLAUDE_CODE is matched against the defaults and becomes "claude-code" when
    that is the existing key.
    """
    out: dict = {}
    short = {"MEMEX_VAULT": ("vault", "path"), "MEMEX_LANG": ("language",),
             "MEMEX_STATE_DIR": ("paths", "state"), "MEMEX_CACHE_DIR": ("paths", "cache")}
    for k, v in environ.items():
        if k in short:
            keys = short[k]
        elif k.startswith(ENV_PREFIX) and "__" in k:
            keys, ref = [], defaults
            for p in k[len(ENV_PREFIX):].split("__"):
                p = p.lower()
                if isinstance(ref, dict) and p not in ref and p.replace("_", "-") in ref:
                    p = p.replace("_", "-")
                keys.append(p)
                ref = ref.get(p, {}) if isinstance(ref, dict) else {}
            keys = tuple(keys)
        else:
            continue
        cur = out
        for part in keys[:-1]:
            cur = cur.setdefault(part, {})
        cur[keys[-1]] = v if k in ("MEMEX_VAULT", "MEMEX_STATE_DIR", "MEMEX_CACHE_DIR") else _parse_env_value(v)
    return out


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


@dataclass(frozen=True)
class Paths:
    vault: Path
    state: Path
    cache: Path
    config_file: Path

    # -- vault-side (versioned with the knowledge base) --
    @property
    def ledger(self) -> Path:
        return self.vault / VAULT_DIR

    @property
    def raw(self) -> Path:
        return self.vault / "raw"

    @property
    def wiki(self) -> Path:
        return self.vault / "wiki"

    @property
    def inbox(self) -> Path:
        return self.vault / "inbox"

    # -- machine-side (never committed) --
    @property
    def events(self) -> Path:
        return self.state / "events.jsonl"

    @property
    def log(self) -> Path:
        return self.state / "memex.log"

    @property
    def locks(self) -> Path:
        return self.state / "locks"

    @property
    def staging(self) -> Path:
        return self.state / "staging"

    @property
    def runs(self) -> Path:
        return self.state / "runs"

    @property
    def search_index(self) -> Path:
        return self.cache / "search" / f"{self.state.name}.sqlite"


def vault_id(vault: Path) -> str:
    """Stable, filesystem-safe id for a vault: <name>-<hash of its absolute path>.

    Each vault (including throwaway test copies) gets its own state directory,
    so a test run can never write into a real vault's logs, locks or staging.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", vault.name).strip("-") or "vault"
    return f"{slug}-{hashlib.sha1(str(vault).encode()).hexdigest()[:8]}"


class Config:
    """Effective configuration plus resolved paths.

    cfg.get("search.rerank") reads a dotted key; cfg.paths has every location.
    """

    def __init__(self, data: dict, paths: Paths, sources: list[str]):
        self.data = data
        self.paths = paths
        self.sources = sources

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def section(self, dotted: str) -> dict:
        v = self.get(dotted, {})
        return v if isinstance(v, dict) else {}

    @property
    def language(self) -> str:
        lang = str(self.get("language", "en")).lower()
        return lang if lang in ("en", "zh") else "en"

    @property
    def vault(self) -> Path:
        return self.paths.vault

    def as_toml_like(self) -> str:
        """Readable dump for `memex config show` (JSON is unambiguous and stdlib-only)."""
        return json.dumps(self.data, ensure_ascii=False, indent=2)


def load(vault: str | os.PathLike | None = None, environ: dict[str, str] | None = None,
         user_config: str | os.PathLike | None = None) -> Config:
    environ = dict(os.environ if environ is None else environ)
    defaults = tomllib.loads(default_config_text())
    ucfg_path = Path(user_config).expanduser() if user_config else user_config_path()
    user = _load_toml(ucfg_path)
    env = _env_overrides(environ, defaults)
    sources = ["defaults"] + ([str(ucfg_path)] if user else []) + (["environment"] if env else [])

    # The vault location is needed before the vault's own config can be read.
    vpath = (expand(vault) if vault else None) or expand(env.get("vault", {}).get("path")) \
        or expand(user.get("vault", {}).get("path")) or _find_vault_upwards(Path.cwd()) \
        or expand(defaults["vault"]["path"])
    vcfg_path = vpath / VAULT_DIR / "config.toml"
    vcfg = _load_toml(vcfg_path)
    if vcfg:
        sources.insert(1, str(vcfg_path))
        vcfg.pop("vault", None)        # a vault cannot relocate itself
        vcfg.pop("paths", None)        # machine paths belong to the user config
    data = _merge(_merge(_merge(defaults, vcfg), user), env)
    data.setdefault("vault", {})["path"] = str(vpath)

    state = expand(data["paths"].get("state")) or (_xdg("XDG_STATE_HOME", ".local/state") / "memexos" / vault_id(vpath))
    cache = expand(data["paths"].get("cache")) or (_xdg("XDG_CACHE_HOME", ".cache") / "memexos")
    paths = Paths(vault=vpath, state=state, cache=cache, config_file=ucfg_path)
    return Config(data, paths, sources)


def _find_vault_upwards(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / VAULT_DIR).is_dir() and (d / "raw").is_dir():
            return d.resolve()
    return None
