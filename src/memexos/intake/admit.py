"""`memex intake`: collect → admission agent → secret scan → raw/.

Two hard rules live in code, not in the prompt:
  1. Nothing enters raw/ without passing the secret scan; anything flagged is not admitted and stays in the local
     candidate area (never in git).
  2. The admission agent can only write its own candidate area; moving files into raw/ is done here (hidden
     temp file + rename, so readers never see half a file).
Everything else (how to read a conversation, what to keep, how to merge or split) is the agent's call, guided by
prompts/<lang>/admission.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from memexos import backends, events, i18n, lock, plugins, rawfile
from memexos.capture import fetch
from memexos.capture.redact import cookie_dir_of, known_secrets, scan
from memexos.intake import sessions as S
from memexos.pipeline.stage import ensure_identity, git
from memexos.util import append_jsonl, atomic_write, self_cmd, split_frontmatter

DOC_MAX_BYTES = 20 * 1024 * 1024
TEXT_EXT = (".md", ".markdown", ".txt")
DENY_READ = ["~/.ssh/**", "~/.gnupg/**", "~/.aws/**", "**/.env", "**/.env.*", "**/*credentials*", "**/*secret*",
             "~/Library/Keychains/**", "~/Library/Application Support/Google/Chrome/**", "~/.config/google-chrome/**",
             "~/.mozilla/**"]


def state_path(cfg) -> Path:
    return cfg.paths.ledger / "intake-state.json"


def load_state(cfg) -> dict:
    try:
        return json.loads(state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(cfg, st: dict) -> None:
    atomic_write(state_path(cfg), json.dumps(st, ensure_ascii=False, indent=1))


def ledger_keys(cfg) -> set[str]:
    return S.ledger_keys(cfg)


# ---------------------------------------------------------------- collectors
def collect_sessions(cfg, a) -> list[list[dict]]:
    items = S.list_sessions(cfg, a.since_days, float(cfg.get("intake.idle_hours") or 6))
    if a.keys:
        items = [it for it in items if it["key"] in a.keys]
    if a.limit:
        items = items[: a.limit]
    chars, nmax = int(cfg.get("intake.batch_chars") or 400000), int(cfg.get("intake.batch_max") or 6)
    batches, cur, size = [], [], 0
    for it in sorted(items, key=lambda x: (x["cwd"], x["first"] or "")):      # one project's sessions together: easier merging
        if cur and (size + it["approx_chars"] > chars or len(cur) >= nmax):
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += it["approx_chars"]
    return batches + ([cur] if cur else [])


def parse_date(s: str) -> float | None:
    import email.utils
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def collect_rss(cfg, a) -> list[list[dict]]:
    p = cfg.vault / (cfg.get("intake.rss.feeds_file") or ".memex/feeds.txt")
    if not p.exists():
        return []
    feeds = [l.split("#", 1)[0].strip() for l in p.read_text(encoding="utf-8").splitlines()]
    done, since, items = ledger_keys(cfg), time.time() - a.since_days * 86400, []
    for feed in [f for f in feeds if f]:
        try:
            entries = fetch.rss(cfg, feed, 40)
        except Exception as e:  # noqa: BLE001 — one broken feed must not stop the others
            events.emit(cfg.paths.state, "intake.channel_failed", channel="rss", feed=feed, why=str(e)[:200])
            continue
        for e in entries:
            key, ts = f"rss:{e.get('url')}", parse_date(e.get("date", ""))
            if e.get("url") and key not in done and not (ts and ts < since):
                items.append({"key": key, "url": e["url"], "feed": feed, "title": e.get("title", ""), "summary": e.get("summary", "")[:600],
                              "last": datetime.date.fromtimestamp(ts).isoformat() if ts else ""})
    return [items[i:i + 20] for i in range(0, len(items), 20)]


def plan(cfg, a, st: dict) -> list[tuple[str, list[dict]]]:
    out: list[tuple[str, list[dict]]] = []
    sources = cfg.get("intake.sources") or ["sessions", "rss"]
    want = a.source
    if "sessions" in sources and want in ("all", "sessions"):
        out += [("sessions", b) for b in collect_sessions(cfg, a)]
    if "rss" in sources and want in ("all", "rss"):
        out += [("rss", b) for b in collect_rss(cfg, a)]
    for name, mod in plugins.enabled(cfg).items():
        if want not in ("all", name) or not hasattr(mod, "collect"):
            continue
        pst = st.setdefault(name, {})
        try:
            batches = mod.collect(cfg, pst, ledger_keys(cfg), a.since_days)
        except Exception as e:  # noqa: BLE001
            events.emit(cfg.paths.state, "intake.channel_failed", channel=name, why=str(e)[:200])
            continue
        for err in pst.pop("_errors", []) or []:
            events.emit(cfg.paths.state, "intake.channel_failed", channel=name, why=str(err)[:200])
        out += [(name, b) for b in batches]
    return out


# ---------------------------------------------------------------- the admission agent
def admission_prompt(cfg, kind: str, items: list[dict], workdir: Path) -> str:
    memex = " ".join(self_cmd())
    channels = "\n".join(f"| {name} | | {getattr(mod, 'ADMISSION', {}).get(i18n.language()) or getattr(mod, 'ADMISSION', {}).get('en', '')} |"
                         for name, mod in plugins.enabled(cfg).items() if hasattr(mod, "ADMISSION"))
    rules = i18n.prompt("admission", memex=memex, channels=channels)
    if kind == "sessions":
        lines = "\n".join(f"- `{it['key']}` · {it['cwd']} · {it['last']} · {it['turns']} turns · ~{it['approx_chars']} chars · {it['first_user_msg']}"
                          for it in items)
        material = i18n.t("intake.material_sessions", memex=memex)
    else:
        atomic_write(workdir / "items.json", json.dumps(items, ensure_ascii=False, indent=1))
        lines = "\n".join(f"- `{it['key']}` · {it.get('last', '')} · {it.get('title', '')}" for it in items)
        material = i18n.t("intake.material_items")
    return rules + i18n.prompt("admission_task", n=len(items), channel=kind, items=lines, material=material,
                               date=datetime.date.today().isoformat())


def run_agent(cfg, kind: str, items: list[dict], workdir: Path):
    me = self_cmd()
    roots = [Path(os.path.expanduser(r)) for r in cfg.get("intake.sessions.project_roots") or []]
    pol = backends.Policy(write=[workdir], read=[S.claude_dir(cfg), S.codex_dir(cfg), *[r for r in roots if r.exists()]],
                          commands=[me + ["sessions"], me + ["fetch"], me + ["search"], me + ["get"]], network=True,
                          deny_read=DENY_READ + [str(cookie_dir_of(cfg)) + "/**"])
    prompt = admission_prompt(cfg, kind, items, workdir)
    atomic_write(workdir / "prompt.md", prompt)
    return backends.agent(cfg, "worker", prompt, cwd=workdir, workdir=workdir / ".run", policy=pol, effort="high", timeout=3600, job="intake")


# ---------------------------------------------------------------- writing admitted material
def text_key(p: Path) -> str:
    if p.suffix.lower() in TEXT_EXT:
        return rawfile.body_hash(p.read_text(encoding="utf-8", errors="ignore"))
    return hashlib.sha256(p.read_bytes()).hexdigest()


class RawIndex:
    """Body hashes of what raw/ already holds: the same material is never written twice."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.h: dict[str, str] = {}
        for f in sorted(cfg.paths.raw.glob("*")):
            if f.is_file() and not f.name.startswith("."):
                self.h.setdefault(text_key(f), f.relative_to(cfg.vault).as_posix())

    def same(self, p: Path) -> str | None:
        return self.h.get(text_key(p))

    def add(self, p: Path) -> None:
        self.h.setdefault(text_key(p), p.relative_to(self.cfg.vault).as_posix())


def project_of(cfg, path: Path) -> str:
    """The project a document belongs to: the nearest folder with .git, else the folder under a project root."""
    roots = [Path(os.path.expanduser(r)).resolve() for r in cfg.get("intake.sessions.project_roots") or []]
    d = path.parent
    for _ in range(10):
        if (d / ".git").exists() or d.parent in roots:
            return d.name
        if d.parent == d:
            break
        d = d.parent
    return "local"


def allowed_doc(cfg, p: Path) -> bool:
    roots = [Path(os.path.expanduser(r)).resolve() for r in cfg.get("intake.sessions.project_roots") or []]
    rp = p.resolve()
    return p.is_file() and p.stat().st_size <= DOC_MAX_BYTES and p.suffix.lower() in S.DOC_EXT and any(r in rp.parents for r in roots)


def materialize_doc(cfg, src: Path, date: str, idx: RawIndex) -> list[str]:
    """A document written in a conversation, into raw/: text as is with a header, HTML as extracted text plus the
    original in raw/assets/, other formats copied unchanged."""
    dup = idx.same(src)
    if dup:
        return [dup]
    title = src.stem
    plat = project_of(cfg, src)
    base = {"original": S.tilde(str(src)), "published": date}
    if src.suffix.lower() in (".html", ".htm"):
        raw_html = src.read_text(encoding="utf-8", errors="ignore")
        t = re.search(r"(?is)<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", raw_html)
        title = re.sub(r"<[^>]+>", "", next((g for g in t.groups() if g), "")).strip() if t else title
        md = rawfile.path_for(cfg.paths.raw, "doc", plat, title, src.stem)
        bundle = cfg.paths.raw / "assets" / md.stem
        bundle.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, bundle / src.name)
        body = i18n.t("intake.html_note", bundle=f"raw/assets/{md.stem}/") + "\n\n" + fetch.html_to_text(raw_html)
        atomic_write(md, rawfile.render({"source_type": "doc", "platform": plat, "title": title, "captured": datetime.date.today().isoformat(),
                                         "fetched_via": "html-to-text", "bundle": f"raw/assets/{md.stem}/{src.name}", **base}) + body + "\n")
        idx.add(md)
        return [md.relative_to(cfg.vault).as_posix(), f"raw/assets/{md.stem}/"]
    if src.suffix.lower() in TEXT_EXT:
        fm, body = split_frontmatter(src.read_text(encoding="utf-8", errors="ignore"))
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        title = h1.group(1).strip() if h1 else title
        p = rawfile.write(cfg.paths.raw, "doc", plat, title, {**{k: v for k, v in fm.items() if k not in ("title", "source_type")},
                                                               **base, "fetched_via": "copy"}, body, src.stem)
    else:
        p = rawfile.path_for(cfg.paths.raw, "doc", plat, title, src.stem, ext=src.suffix.lower())
        shutil.copy2(src, p)
    idx.add(p)
    return [p.relative_to(cfg.vault).as_posix()]


def write_output(cfg, src: Path, keys: list[str], session_date: str) -> Path:
    """An agent-written file into raw/: snap-* as a session snapshot, fb-* as feedback, doc-* as a document."""
    fm, body = split_frontmatter(src.read_text(encoding="utf-8", errors="ignore"))
    h1 = (re.search(r"^#\s+(.+)$", body, re.M) or [None, ""])[1].strip()
    sess = fm.get("sessions") if isinstance(fm.get("sessions"), list) else keys
    plat = (str(sess[0]).split(":", 1)[0] if sess else "claude") or "claude"
    stem = src.stem
    fallback = str(sess[0]).split(":")[-1][:8] if sess else stem
    if stem.startswith("fb-"):
        pages = fm.get("pages") if isinstance(fm.get("pages"), list) else [p for p in [fm.get("pages")] if p]
        try:
            return rawfile.write(cfg.paths.raw, "feedback", plat, fm.get("topic") or h1 or stem[3:],
                                 {"sessions": sess, "pages": pages, "claim": fm.get("claim", ""), "published": fm.get("date") or session_date,
                                  "fetched_via": "conversation (correction distilled by the admission agent)"}, body, fallback)
        except ValueError:               # the target sentence is not specified: keep the content as a snapshot instead
            stem = "snap-" + stem[3:]
    if stem.startswith("snap-"):
        extra = {k: v for k, v in fm.items() if k not in ("source_type", "date", "topic", "title", "sessions")}
        return rawfile.write(cfg.paths.raw, "session", plat, fm.get("topic") or h1 or stem[5:],
                             {**extra, "sessions": sess, "published": fm.get("date") or session_date,
                              "fetched_via": "session-snapshot (distilled by the admission agent)"}, body, fallback)
    if src.suffix.lower() not in TEXT_EXT:
        p = rawfile.path_for(cfg.paths.raw, "doc", plat, fm.get("title") or stem[4:], stem[4:], ext=src.suffix.lower())
        shutil.copy2(src, p)
        return p
    return rawfile.write(cfg.paths.raw, "doc", plat, fm.get("title") or h1 or stem[4:],
                         {**{k: v for k, v in fm.items() if k not in ("source_type", "title")}, "sessions": sess, "published": session_date,
                          "fetched_via": "agent-transcribed (a document from the conversation, copied by the admission agent)"}, body, stem[4:])


def post_process(cfg, workdir: Path, items: list[dict], kind: str, secrets: set[str], run_id: str, idx: RawIndex) -> tuple[dict, list[dict]]:
    """Apply decisions.jsonl: secret scan → admit to raw / uncertain to candidates / reject to the ledger only.
    Returns (counts, links to capture)."""
    want = {it["key"]: it for it in items}
    decisions = {}
    for line in (workdir / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if (workdir / "decisions.jsonl").exists() else []:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("key") in want:
            decisions[d["key"]] = d
    today = datetime.date.today().isoformat()
    cand_dir = cfg.vault / "inbox" / "candidates" / today
    stats = {"admit": 0, "reject": 0, "uncertain": 0, "blocked": 0, "missing": len(set(want) - set(decisions))}
    rows, to_capture, written = [], [], {}
    for key, d in decisions.items():
        dec = d.get("decision")
        outs = [workdir / Path(o).name for o in d.get("outputs") or [] if (workdir / Path(o).name).is_file()]
        copies = [Path(os.path.expanduser(c)) for c in d.get("copy_docs") or [] if allowed_doc(cfg, Path(os.path.expanduser(c)))]
        hits = {}
        for p in outs + [c for c in copies if c.suffix.lower() in (*TEXT_EXT, ".html", ".htm", ".csv")]:
            h = scan(p.read_text(encoding="utf-8", errors="ignore"), secrets)
            if h:
                hits[str(p)] = h
        row = {"key": key, "decision": dec, "reason": d.get("reason", ""), "five": d.get("five", {}), "run": run_id,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "raw": [], "candidates": [], "by": d.get("by", "agent")}
        if hits and dec in ("admit", "uncertain"):
            row["decision"] = "blocked"
            row["reason"] = i18n.t("intake.blocked", what="; ".join(sorted({x for h in hits.values() for x in h})), where=workdir, was=dec)
            stats["blocked"] += 1
        elif dec == "admit":
            date = (want[key].get("last") or today)[:10]
            for o in outs:
                if o.name in written:
                    row["raw"].append(written[o.name])
                    continue
                dup = idx.same(o)
                if dup:
                    written[o.name] = dup
                else:
                    p = write_output(cfg, o, [k for k, x in decisions.items() if o.name in [Path(y).name for y in x.get("outputs") or []]], date)
                    idx.add(p)
                    written[o.name] = p.relative_to(cfg.vault).as_posix()
                row["raw"].append(written[o.name])
            for c in copies:
                row["raw"] += materialize_doc(cfg, c, date, idx)
            if kind != "sessions":
                urls = [u for u in d.get("capture_urls") or [] if isinstance(u, str) and u.startswith("http")] or [want[key]["url"]]
                via = f"{kind}:{want[key].get('feed', '')}" if kind == "rss" else kind
                to_capture += [{"url": u, "via": via, "key": key} for u in dict.fromkeys(urls)]
            stats["admit"] += 1
        elif dec == "uncertain":
            cand_dir.mkdir(parents=True, exist_ok=True)
            for o in outs:
                dest = cand_dir / o.name
                n = 2
                while dest.exists():
                    dest = cand_dir / f"{o.stem}-{n}{o.suffix}"
                    n += 1
                shutil.copy2(o, dest)
                row["candidates"].append(dest.relative_to(cfg.vault).as_posix())
            stats["uncertain"] += 1
            with open(cand_dir / "README.md", "a", encoding="utf-8") as f:
                if f.tell() == 0:
                    f.write(i18n.t("intake.candidates_title", date=today) + "\n\n")
                f.write(f"- `{key}`: {row['reason']}" + (f" → {', '.join(row['candidates'])}" if row["candidates"] else "") +
                        (f" ({want[key].get('url')})" if want[key].get("url") else "") + "\n")
        else:
            row["decision"] = "reject"
            stats["reject"] += 1
        rows.append(row)
    append_jsonl(cfg.paths.ledger / "admission.jsonl", rows)
    return stats, to_capture


def capture(cfg, links: list[dict], st: dict) -> int:
    """Admitted links into raw/ (add-url). Failures are remembered and retried on the next runs (3 tries)."""
    from memexos.capture.add import add
    fails = st.setdefault("capture_failures", {})
    ok = 0
    for it in links:
        try:
            p = add(cfg, it["url"], via=it.get("via", ""))
            fails.pop(it["url"], None)
            events.emit(cfg.paths.state, "capture.done", path=p.relative_to(cfg.vault).as_posix(), url=it["url"])
            ok += 1
        except Exception as e:  # noqa: BLE001
            f = fails.setdefault(it["url"], {"n": 0, "via": it.get("via", "")})
            f.update(n=f["n"] + 1, last=str(e)[-240:], ts=time.strftime("%F %T"))
            events.emit(cfg.paths.state, "capture.failed", url=it["url"], attempt=f["n"], why=str(e)[-200:])
        save_state(cfg, st)
    return ok


def commit(cfg, msg: str) -> None:
    ensure_identity(cfg.vault)
    git(cfg.vault, "add", "-A", "raw", "inbox", ".memex", check=False)
    import subprocess
    if subprocess.run(["git", "-C", str(cfg.vault), "diff", "--cached", "--quiet"], stdin=subprocess.DEVNULL).returncode:
        git(cfg.vault, "commit", "-q", "-m", msg, check=False)


def run(cfg, a) -> int:
    if a.if_stale:
        last = next((e for e in reversed(list(events.read(cfg.paths.state, types=("intake.run",))))), None)
        if last and time.time() - events._epoch(last["ts"]) < a.if_stale * 3600:
            print(i18n.t("intake.fresh"))
            return 0
    try:
        with lock.held(cfg.paths.locks, "intake"):
            st = load_state(cfg)
            secrets = known_secrets(cfg)
            run_id = events.current_run()
            retry = [{"url": u, "via": f.get("via", "")} for u, f in st.get("capture_failures", {}).items() if f.get("n", 0) < 3]
            if retry:
                with lock.held(cfg.paths.locks, "write", wait=5400), events.step(cfg.paths.state, "capture", items=len(retry), retry=True):
                    capture(cfg, retry, st)
            with events.step(cfg.paths.state, "collect") as info:
                batches = plan(cfg, a, st)
                info.update(batches=len(batches), items=sum(len(b) for _, b in batches))
            save_state(cfg, st)
            events.emit(cfg.paths.state, "intake.run", batches=len(batches), items=sum(len(b) for _, b in batches))
            total = {"admit": 0, "reject": 0, "uncertain": 0, "blocked": 0, "missing": 0}
            for i, (kind, items) in enumerate(batches, 1):
                workdir = cfg.paths.state / "intake" / run_id / f"{i:03d}-{kind}"
                workdir.mkdir(parents=True, exist_ok=True)
                with events.step(cfg.paths.state, "admit", channel=kind, items=len(items)) as info:
                    r = run_agent(cfg, kind, items, workdir)
                    info.update(ok=r.ok, cost_usd=r.cost_usd)
                if r.limited:
                    events.emit(cfg.paths.state, "intake.quota", why=r.tail[-200:])
                    break
                with lock.held(cfg.paths.locks, "write", wait=5400):
                    stats, links = post_process(cfg, workdir, items, kind, secrets, run_id, RawIndex(cfg))
                    if links:
                        with events.step(cfg.paths.state, "capture", items=len(links)):
                            capture(cfg, links, st)
                    commit(cfg, f"intake {run_id} batch {i}: " + ", ".join(f"{k} {v}" for k, v in stats.items() if v))
                for k in total:
                    total[k] += stats.get(k, 0)
                events.emit(cfg.paths.state, "intake.batch", i=i, of=len(batches), channel=kind, seconds=r.elapsed_s, cost_usd=r.cost_usd,
                            summary=(r.text or r.tail)[-200:], **stats)
            save_state(cfg, st)
            with lock.held(cfg.paths.locks, "write", wait=5400):
                commit(cfg, f"intake {run_id}: state")
            events.emit(cfg.paths.state, "intake.done", **total)
            print(json.dumps({"run": run_id, **total}, ensure_ascii=False))
    except lock.Busy:
        print(i18n.t("intake.busy"))
    return 0


def cmd_intake(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex intake")
    ap.add_argument("--source", default="all", help="all, sessions, rss, or an enabled plugin channel")
    ap.add_argument("--since-days", type=float)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--keys", nargs="+", help="only these sessions (trial runs)")
    ap.add_argument("--if-stale", type=float, default=0, help="skip when the last intake ran less than this many hours ago")
    a = ap.parse_args(argv)
    cfg = loader()
    if not (cfg.vault / "raw").is_dir():
        print(i18n.t("cli.no_vault", path=cfg.vault), file=sys.stderr)
        return 1
    a.since_days = a.since_days if a.since_days is not None else float(cfg.get("intake.since_days") or 30)
    with events.run(cfg.paths.state, "intake"):
        return run(cfg, a)
