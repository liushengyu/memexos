"""`memex setup`: choose models, keys, search, collection sources and scheduling, then create the knowledge base.

Interactive by default; every question has a flag, and --yes accepts the detected defaults, so an agent or a script
can run it too. API keys go to the system keychain (or stay in your environment), never into a file. The settings
are written into ~/.config/memexos/config.toml, changing only the chosen keys and keeping its comments.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from memexos import config as config_mod
from memexos import credentials, i18n
from memexos.util import which

ROLES = ("writer", "reviewer", "worker", "vision", "judge")
KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


# ---------------------------------------------------------------- editing TOML without losing comments
def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def _comment_at(line: str) -> int:
    """Index of the first # outside a string, or -1."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == "#":
            return i
        i += 1
    return -1


def toml_set(text: str, section: str, key: str, value) -> str:
    """Set key = value in [section]: replace the line (keeping its trailing comment) or add it under the header; add
    the section at the end when it does not exist. Comments elsewhere are untouched."""
    lines = text.split("\n")
    header = re.compile(r"^\s*\[" + re.escape(section) + r"\]\s*(#.*)?$")
    start = next((i for i, l in enumerate(lines) if header.match(l)), None)
    new = f"{key} = {toml_value(value)}"
    if start is None:
        return text.rstrip("\n") + f"\n\n[{section}]\n{new}\n"
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for i in range(start + 1, end):
        if pat.match(lines[i]):
            at = _comment_at(lines[i])
            lines[i] = new + (("  " + lines[i][at:].strip()) if at >= 0 else "")
            return "\n".join(lines)
    lines.insert(start + 1, new)
    return "\n".join(lines)


# ---------------------------------------------------------------- what is on this machine
def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def detect() -> dict:
    d: dict = {}
    d["claude"] = bool(which("claude"))
    if d["claude"]:
        rc, out = _run([which("claude"), "auth", "status", "--json"])
        d["claude_login"] = rc == 0 and '"loggedIn": true' in out.replace('"loggedIn":true', '"loggedIn": true')
    d["codex"] = bool(which("codex"))
    if d["codex"]:
        rc, out = _run([which("codex"), "login", "status"])
        d["codex_login"] = rc == 0 and "not" not in out.lower()
    try:
        import anthropic  # noqa: F401,PLC0415
        d["anthropic_sdk"] = True
    except ImportError:
        d["anthropic_sdk"] = False
    d["keys"] = {k: credentials.available(v) for k, v in KEYS.items()}
    d["jev_key"] = credentials.available("JEV_API_KEY")
    d["ollama"] = []
    try:
        from memexos import net
        r = net.request("http://127.0.0.1:11434/api/tags", proxy="", timeout=2, prefer="urllib")
        d["ollama"] = [m.get("name") for m in json.loads(r.data).get("models", [])]
    except Exception:  # noqa: BLE001 — no local server
        pass
    d["qmd"] = bool(which("qmd"))
    rc, out = _run([which("node") or "node", "--version"]) if which("node") else (1, "")
    m = re.match(r"v(\d+)", out or "")
    d["node"] = int(m.group(1)) if rc == 0 and m else 0
    for tool in ("yt-dlp", "ffmpeg", "whisper-cli", "pdftotext", "curl", "git"):
        d[tool] = bool(which(tool))
    return d


def env_proxy() -> str:
    """The proxy this shell uses, proposed as MemexOS's route: scheduled jobs start without the shell's variables, so
    without it they would fetch directly. Not proposed when it carries a password (it would be written into the
    settings file); then the setting stays empty and the environment is used where there is one."""
    import urllib.parse
    v = next((os.environ[k] for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy") if os.environ.get(k)), "")
    return "" if not v or urllib.parse.urlsplit(v).password else v


def propose(d: dict, extra: str = "") -> dict[str, list[str]]:
    """Who does what, from what is available: the writer and the reviewer from different model families when possible.
    `extra` is an OpenAI-compatible endpoint configured in this run."""
    cc, cx = d["claude"], d["codex"]
    api = "anthropic" if d["keys"].get("anthropic") and d["anthropic_sdk"] else None
    oa = "openai" if d["keys"].get("openai") and d.get("openai_model") else None
    first = [b for b in ("claude-code", "codex", api, oa, extra or None) if b and (b != "claude-code" or cc) and (b != "codex" or cx)]
    if not first:
        return {}
    writer = [first[0]]
    others = [b for b in first if b != first[0]]
    reviewer = others[:1] + [first[0]] if others else [first[0]]
    judge = (["claude-code:sonnet"] if cc else []) + [b for b in first if b != "claude-code"][:1] or [first[0]]
    vision = ([b for b in ("codex", "claude-code", api) if b and (b != "claude-code" or cc) and (b != "codex" or cx)] or first)[:2]
    return {"writer": writer, "reviewer": reviewer, "worker": [first[0]], "vision": vision, "judge": judge[:2]}


# ---------------------------------------------------------------- asking
class Asker:
    def __init__(self, yes: bool):
        self.yes = yes or not sys.stdin.isatty()

    def text(self, key: str, default: str = "", **kw) -> str:
        if self.yes:
            return default
        ans = input(i18n.t(f"setup.{key}", **kw) + (f" [{default}]" if default else "") + " ").strip()
        return ans or default

    def yesno(self, key: str, default: bool, **kw) -> bool:
        if self.yes:
            return default
        hint = "Y/n" if default else "y/N"
        ans = input(i18n.t(f"setup.{key}", **kw) + f" [{hint}] ").strip().lower()
        return default if not ans else ans in ("y", "yes", "是", "好", "对")

    def secret(self, name: str) -> str:
        if self.yes:
            return ""
        return getpass.getpass(i18n.t("setup.key_prompt", name=name) + " ")


def tilde(path: Path) -> str:
    home = str(Path.home().resolve())
    p = str(Path(path).expanduser().resolve())
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def say(key: str, **kw) -> None:
    print(i18n.t(f"setup.{key}", **kw))


# ---------------------------------------------------------------- the wizard
def cmd_setup(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex setup", description="Set up MemexOS: models, keys, search, collection, scheduling.")
    ap.add_argument("--yes", "-y", action="store_true", help="accept the detected defaults (no questions)")
    ap.add_argument("--lang", choices=i18n.available())
    ap.add_argument("--vault", help="where the knowledge base lives (default ~/memex)")
    ap.add_argument("--sample", action=argparse.BooleanOptionalAction, default=None, help="start with the sample content")
    for r in ROLES:
        ap.add_argument(f"--{r}", help=f"backends for the {r} role, comma-separated, e.g. claude-code,codex")
    ap.add_argument("--openai-name", default="", help="add an OpenAI-compatible endpoint under this name (e.g. local, deepseek)")
    ap.add_argument("--openai-base-url", default="")
    ap.add_argument("--openai-model", default="")
    ap.add_argument("--openai-key-env", default=None, help='environment variable with its key ("" for none)')
    ap.add_argument("--judge-backend", choices=["llm", "jev", "none"])
    ap.add_argument("--sessions", action=argparse.BooleanOptionalAction, default=None, help="collect from your Claude Code / Codex sessions")
    ap.add_argument("--plugins", help="login-based collectors to enable: x,bilibili,youtube")
    ap.add_argument("--cookie-browser", default="", help="copy those logins from this everyday browser (chrome, firefox, safari, edge, brave) "
                    "instead of logging in once in MemexOS's own browser profile (the default)")
    ap.add_argument("--login", action=argparse.BooleanOptionalAction, default=None,
                    help="open the login windows for the enabled collectors now (default: ask; with --yes: no)")
    ap.add_argument("--proxy", default=None, help='route for fetching pages: "" = environment, "direct", or a proxy URL')
    ap.add_argument("--proxy-cacert", default=None)
    ap.add_argument("--qmd", action=argparse.BooleanOptionalAction, default=None, help="build the vector index (qmd) now")
    ap.add_argument("--schedule", action=argparse.BooleanOptionalAction, default=None, help="install the scheduled jobs")
    a = ap.parse_args(argv)
    ask = Asker(a.yes)
    cfg0 = loader(a.vault)
    lang = a.lang or (cfg0.language if config_mod.user_config_path().exists() else ("zh" if os.environ.get("LANG", "").startswith("zh") else "en"))
    if not a.lang and not ask.yes:
        lang = ask.text("lang", lang)
        lang = lang if lang in i18n.available() else "en"
    i18n.set_language(lang)
    say("title")

    # 1. where
    vault = Path(os.path.expanduser(a.vault or ask.text("vault", tilde(cfg0.vault)))).resolve()
    fresh = not (vault / "raw").is_dir()
    sample = a.sample if a.sample is not None else (ask.yesno("sample", True) if fresh else False)

    # 2. models
    d = detect()
    say("found", tools=", ".join(n for n, ok in (("Claude Code", d["claude"]), ("Codex", d["codex"]),
                                                  ("Anthropic API", d["keys"]["anthropic"] and d["anthropic_sdk"]),
                                                  ("OpenAI-compatible API", d["keys"]["openai"]),
                                                  (f"Ollama ({len(d['ollama'])} models)", bool(d["ollama"]))) if ok) or "-")
    if d["claude"] and not d.get("claude_login"):
        say("claude_login")
    if d["codex"] and not d.get("codex_login"):
        say("codex_login")
    oa_name = a.openai_name
    oa_section: dict = {}
    if oa_name or (not ask.yes and ask.yesno("add_openai", not (d["claude"] or d["codex"]))):
        oa_name = oa_name or ask.text("openai_name", "local" if d["ollama"] else "openai")
        base = a.openai_base_url or ask.text("openai_base_url", "http://localhost:11434/v1" if d["ollama"] and oa_name == "local" else "https://api.openai.com/v1")
        model = a.openai_model or ask.text("openai_model", (d["ollama"] or [""])[0] if "11434" in base else "")
        key_env = a.openai_key_env if a.openai_key_env is not None else ask.text("openai_key_env", "" if "localhost" in base or "127.0.0.1" in base else f"{oa_name.upper()}_API_KEY")
        oa_section = {"type": "openai", "base_url": base, "model": model, "api_key_env": key_env}
        if "localhost" in base or "127.0.0.1" in base:
            oa_section["proxy"] = "direct"
        if key_env and not credentials.available(key_env):
            v = ask.secret(key_env)
            if v:
                say("key_stored", where=credentials.put(key_env, v))
    roles = propose(d, oa_name if oa_section else "")
    for r in ROLES:
        flag = getattr(a, r)
        if flag:
            roles[r] = [x.strip() for x in flag.split(",") if x.strip()]
    if not roles:
        say("no_models")              # the knowledge base and search are still set up; writing waits for a model
    else:
        say("roles", lines="\n".join(f"  {r:9} {' → '.join(roles[r])}" for r in ROLES if r in roles))
    if roles and not ask.yes and not ask.yesno("roles_ok", True):
        for r in ROLES:
            v = ask.text("role_edit", ",".join(roles.get(r, [])), role=r)
            roles[r] = [x.strip() for x in v.split(",") if x.strip()]
    for b in {x.partition(":")[0] for chain in roles.values() for x in chain}:
        if b in KEYS and not credentials.available(KEYS[b]):
            v = ask.secret(KEYS[b])
            if v:
                say("key_stored", where=credentials.put(KEYS[b], v))

    # 3. the judge
    judge = a.judge_backend or ("jev" if d["jev_key"] else "llm")
    if not a.judge_backend and not ask.yes:
        judge = ask.text("judge", judge)
        judge = judge if judge in ("llm", "jev", "none") else "llm"
    if judge == "jev":
        say("jev_consent")
        if not credentials.available("JEV_API_KEY"):
            v = ask.secret("JEV_API_KEY")
            if v:
                say("key_stored", where=credentials.put("JEV_API_KEY", v))
            elif not credentials.available("JEV_API_KEY"):
                judge = "llm"
                say("jev_fallback")

    # 4. collection
    sessions = a.sessions if a.sessions is not None else ask.yesno("sessions", d["claude"] or d["codex"])
    plugins = [p.strip() for p in (a.plugins.split(",") if a.plugins else []) if p.strip()]
    if a.plugins is None and not ask.yes:
        plugins = [p.strip() for p in ask.text("plugins", "").split(",") if p.strip()]
    login_from = a.cookie_browser or "memex"
    proxy = a.proxy if a.proxy is not None else ask.text("proxy", env_proxy())
    cacert = a.proxy_cacert if a.proxy_cacert is not None else (ask.text("cacert", "") if proxy.startswith("https://") else "")

    # 5. write the settings
    ucfg = config_mod.user_config_path()
    from memexos import scaffold
    text = ucfg.read_text(encoding="utf-8") if ucfg.exists() else scaffold.user_config_text(vault, lang)
    text = toml_set(text, "vault", "path", tilde(vault))
    if re.search(r"(?m)^language\s*=", text):
        text = re.sub(r'(?m)^language\s*=.*$', f'language = "{lang}"', text, count=1)
    else:
        text = f'language = "{lang}"\n' + text
    for r in ROLES:
        if r in roles:
            text = toml_set(text, "models", r, roles[r])
    for k, v in oa_section.items():
        text = toml_set(text, f"backends.{oa_name}", k, v)
    text = toml_set(text, "judge", "backend", judge)
    text = toml_set(text, "intake", "sources", ["sessions", "rss"] if sessions else ["rss"])
    for p in ("x", "bilibili", "youtube"):
        if p in plugins:
            text = toml_set(text, f"plugins.{p}", "enabled", True)
            text = toml_set(text, f"plugins.{p}", "login", login_from)
    text = toml_set(text, "fetch", "proxy", proxy)
    if cacert:
        text = toml_set(text, "fetch", "proxy_cacert", cacert)
    ucfg.parent.mkdir(parents=True, exist_ok=True)
    tmp = ucfg.with_suffix(".toml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    import tomllib
    tomllib.loads(tmp.read_text(encoding="utf-8"))          # never leave a broken settings file behind
    tmp.replace(ucfg)
    say("written", path=ucfg)

    # 6. create the knowledge base
    if fresh:
        for line in scaffold.init(vault, lang=lang, write_user_config=False, sample=sample):
            print(line)
    cfg = loader(str(vault))
    from memexos.search import fts
    fts.update(cfg.vault, cfg.paths.search_index)

    # 7. logins for the collectors that need one: a window opens, the person logs in, MemexOS keeps it alive afterwards
    if plugins and login_from == "memex":
        from memexos.capture import browser as login_browser, cookies
        todo = [p for p in plugins if p in cookies.needs_login(cfg)]
        can = login_browser.find(cfg) and login_browser.can_show_window()
        now = a.login if a.login is not None else (False if ask.yes or not can else ask.yesno("login_now", True, sites=", ".join(todo)))
        for p in todo:
            if now and can:
                try:
                    cookies.login(cfg, p)
                    say("login_done", site=cookies.SITES[p].name)
                    continue
                except Exception as e:  # noqa: BLE001
                    say("login_failed", site=cookies.SITES[p].name, why=str(e)[:200])
            say("login_later", site=p)

    # 8. vector search
    if d["qmd"]:
        build = a.qmd if a.qmd is not None else ask.yesno("qmd", True)
        if build:
            from memexos.search import qmd as qmd_mod
            got = qmd_mod.models(cfg, "pull")          # in the foreground with a progress bar, so nothing races for the files
            if not got["ok"]:
                say("models_failed")
            from memexos.util import self_cmd
            log = cfg.paths.state / "logs"
            log.mkdir(parents=True, exist_ok=True)
            with open(log / "reindex.log", "ab") as out:
                subprocess.Popen(self_cmd() + ["reindex"], stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True, env={**os.environ, "MEMEX_VAULT": str(vault)})
            say("qmd_started")
    else:
        say("qmd_missing")

    # 9. scheduling
    sched = a.schedule if a.schedule is not None else ask.yesno("schedule", False)
    if sched:
        from memexos import schedule
        rc = schedule.cmd_schedule(lambda v=None: loader(str(vault)), ["install"])
        if rc:
            return rc
    else:
        say("schedule_later")

    say("done")
    from memexos import doctor
    return doctor.main(lambda v=None: loader(str(vault)), [])
