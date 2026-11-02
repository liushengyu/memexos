#!/usr/bin/env python3
"""Build a demo MemexOS installation with generated content and simulated activity: for the README screenshots and
for trying the console without any data of your own.

    uv run python tools/make_demo.py DIR [--lang en|zh]
    HOME=DIR/home PATH=DIR/bin:$PATH uv run memex console serve --port 8790     # then open http://127.0.0.1:8790

DIR/home is a separate home directory (config, state, agent sessions); the knowledge base is DIR/home/memex; DIR/bin
holds stand-ins named claude and codex that only print a message, so the console shows both command lines installed.
Nothing outside DIR is read or written, and no model is called. What it contains, all generated:

- the sample vault plus about 250 pages in 11 domains (tools/demo_content.py), each source page citing its raw
  file, with a git history spread over the last 60 days
- two weeks of activity written through the real event API with a simulated clock: nightly collection and
  admission, classification, compiles with gate 1, self-check and review (one failing and reworked), publishes,
  index updates and model costs; plus one compile still running (a background `sleep` stands in for its process
  and ends by itself after 90 minutes)
- Claude Code sessions in which an agent runs `memex search` and `memex get` on the demo vault for real, so the
  usage page comes from the real extraction
- inbox reports with triage, an unsure intake candidate, review findings, and a real retrieval evaluation (L1)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--lang", default="en", choices=["en", "zh"])
    ap.add_argument("--force", action="store_true", help="replace DIR if it exists")
    a = ap.parse_args()
    base = Path(a.dir).expanduser().resolve()
    if base.exists() and any(base.iterdir()):
        if not a.force:
            sys.exit(f"{base} is not empty (use --force to replace it)")
        shutil.rmtree(base)
    home = base / "home"
    home.mkdir(parents=True)
    # everything below runs against the demo home only
    os.environ["HOME"] = str(home)
    for k in [k for k in os.environ if k.startswith(("XDG_", "MEMEX_", "CODEX_HOME", "CLAUDE_CONFIG_DIR"))]:
        del os.environ[k]
    os.environ["MEMEX_SEARCH__BACKEND"] = "fts"          # self-contained: no qmd models needed
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(HERE))
    Demo(home, a.lang).build()
    print(f"\nDemo ready in {base}\n  HOME={home} PATH={base / 'bin'}:$PATH memex console serve --port 8790")
    return 0


# ---------------------------------------------------------------- a clock the event log believes
class Clock:
    """Stands in for the time module inside memexos.events, so runs and steps get simulated timestamps."""

    def __init__(self):
        self.t = time.time()

    def time(self):
        return self.t

    def localtime(self, t=None):
        return time.localtime(self.t if t is None else t)

    def strftime(self, fmt, tup=None):
        return time.strftime(fmt, tup if tup is not None else time.localtime(self.t))

    def __getattr__(self, name):
        return getattr(time, name)


def slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^\w]+", "-", s, flags=re.U).strip("-")[:n].strip("-")


class Demo:
    def __init__(self, home: Path, lang: str):
        self.home, self.lang = home, lang
        self.vault = home / "memex"
        self.rng = random.Random(7)
        self.now = time.time()
        self.today = dt.date.today()

    # ------------------------------------------------------------ pieces
    def git(self, *args, date: float | None = None, check=True):
        env = dict(os.environ)
        if date is not None:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(date))
            env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        return subprocess.run(["git", "-C", str(self.vault), *args], env=env, capture_output=True, text=True, check=check)

    def at(self, days_ago: int, hh: int, mm: int = 0, ss: int = 0) -> float:
        """A time on that day; today's times never lie in the future (they move back to before now)."""
        d = self.today - dt.timedelta(days=days_ago)
        t = time.mktime((d.year, d.month, d.day, hh, mm, ss, 0, 0, -1))
        if days_ago == 0 and t > self.now - 1800:
            t = self.now - 1800 - (24 - hh) * 300
        return t

    def memex(self, *args, check=True) -> str:
        r = subprocess.run([sys.executable, "-m", "memexos", *args], capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, cwd=str(self.home))
        if check and r.returncode != 0:
            raise SystemExit(f"memex {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
        return r.stdout

    # ------------------------------------------------------------ build
    def build(self):
        from memexos import config, i18n, scaffold
        i18n.set_language(self.lang)
        # stand-ins for the Claude Code and Codex command lines, so the console shows a typical setup; they call no model
        bin_dir = self.home.parent / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name in ("claude", "codex"):
            f = bin_dir / name
            f.write_text("#!/bin/sh\necho 'demo stand-in: not a real model command line' >&2\nexit 1\n")
            f.chmod(0o755)
        print("creating the vault with the sample content")
        scaffold.init(self.vault, self.lang, sample=True)
        cfgfile = self.home / ".config" / "memexos" / "config.toml"
        from memexos.setup import toml_set
        cfgfile.write_text(toml_set(cfgfile.read_text(), "search", "backend", "fts"))
        shutil.rmtree(self.vault / ".git")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "MemexOS")
        self.git("config", "user.email", "memexos@localhost")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init: MemexOS knowledge base with the sample content", date=self.at(62, 21, 4))
        self.cfg = config.load(self.vault)
        self.plan()
        self.write_history()
        print("search index")
        self.memex("reindex", "--no-embed")
        self.simulate()
        self.sessions()
        self.inbox()
        print("retrieval evaluation (L1, local, no model calls)")
        self.memex("eval", "retrieval", "l1", check=False)
        self.sort_events()

    def sort_events(self):
        """Simulated events were written after real ones (the commands this script ran); readers expect time order."""
        from memexos import events
        for f in events.files(self.cfg.paths.state):
            lines = [line for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
            lines.sort(key=lambda line: events._epoch(json.loads(line).get("ts", "")))
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ content plan
    def plan(self):
        from demo_content import DOMAINS
        self.sources, self.pages = [], {}
        order = []
        for dom, spec in DOMAINS.items():
            concepts = [tuple(x.split(" — ", 1)) for x in spec["concepts"].strip().splitlines()]
            entities = [tuple(x.split(" — ", 1)) for x in spec["entities"].strip().splitlines()]
            srcs = []
            for line in spec["sources"].strip().splitlines():
                kind_platform, rest = line.rsplit(" | ", 1)
                kind, platform = [x.strip() for x in kind_platform.split("|")]
                title, desc = rest.split(" — ", 1)
                srcs.append({"kind": kind, "platform": platform, "title": title.strip(), "desc": desc.strip(), "domain": dom})
            topic_title, topic_desc = spec["topic"].split(" — ", 1)
            # every concept and entity is introduced by one source of its domain and mentioned by another
            for i, (t, _d) in enumerate(concepts + entities):
                s = srcs[i % len(srcs)]
                s.setdefault("introduces", []).append(t)
                other = srcs[(i + 1) % len(srcs)]
                if other is not s:
                    other.setdefault("mentions", []).append(t)
            for t, d in concepts:
                assert t not in self.pages, f"two pages named {t}"
                self.pages[t] = {"type": "concepts", "desc": d, "domain": dom}
            for t, d in entities:
                assert t not in self.pages, f"two pages named {t}"
                self.pages[t] = {"type": "entities", "desc": d, "domain": dom}
            assert topic_title not in self.pages, f"two pages named {topic_title}"
            self.pages[topic_title] = {"type": "topics", "desc": topic_desc, "domain": dom, "members": [t for t, _ in concepts + entities],
                                       "sources": [s["title"] for s in srcs]}
            for s in srcs:
                assert s["title"] not in self.pages, f"two pages named {s['title']}"
                self.pages[s["title"]] = {"type": "sources", "desc": s["desc"], "domain": dom, "src": s}
            order += srcs
        # spread capture dates over 60 days, denser recently; the newest four wait to be compiled
        self.rng.shuffle(order)
        recent = {0: 7, 1: 6, 2: 6}                     # the simulated days are busy; older ones thin out
        i = 0
        for days_ago, k in recent.items():
            for s in order[i:i + k]:
                s["days_ago"] = days_ago
            i += k
        rest = order[i:]
        for j, s in enumerate(rest):
            s["days_ago"] = 3 + int(55 * (j / max(1, len(rest) - 1)) ** 1.4)
        order.sort(key=lambda s: (-s["days_ago"], s["title"]))
        for s in order:
            day = (self.today - dt.timedelta(days=s["days_ago"])).isoformat()
            name = f"{day}-{s['kind']}-{s['platform']}-{slug(s['title'])}.md"
            s["raw"] = f"raw/{name}"
        self.sources = order
        self.queued = [s for s in order if s["days_ago"] == 0][-4:]
        for s in self.queued:
            s["queued"] = True
        # cross-domain links make the globe one connected thing
        names = [t for t, p in self.pages.items() if p["type"] in ("concepts", "entities")]
        for t in names:
            p = self.pages[t]
            same = [x for x in names if x != t and self.pages[x]["domain"] == p["domain"]]
            p["related"] = self.rng.sample(same, k=min(len(same), self.rng.choice((2, 2, 3))))
            if self.rng.random() < 0.18:
                p["related"].append(self.rng.choice([x for x in names if self.pages[x]["domain"] != p["domain"]]))

    # ------------------------------------------------------------ files
    def raw_text(self, s: dict) -> str:
        url = f"https://example.com/{s['platform']}/{slug(s['title']).lower()}"
        source_type = {"tweet": "tweet", "video": "video", "podcast": "podcast", "session": "session", "doc": "doc"}.get(s["kind"], "web")
        head = {"source_type": source_type, "platform": s["platform"], "title": s["title"], "url": url,
                "captured": (self.today - dt.timedelta(days=s["days_ago"])).isoformat(), "fetched_via": "demo"}
        if s["kind"] == "session":
            head.pop("url")
            head["original"] = f"~/.{s['platform']}/sessions/demo-{slug(s['title']).lower()[:20]}.jsonl"
        fm = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n" for k, v in head.items()) + "---\n\n"
        points = "\n".join(f"- {t}: {self.pages[t]['desc']}." for t in s.get("introduces", [])[:6])
        return fm + f"# {s['title']}\n\n{s['desc'][0].upper() + s['desc'][1:]}.\n\n{points}\n\n" \
                    "(Generated demo text: this file stands in for the captured source.)\n"

    def page_text(self, title: str, p: dict, created: str, raws: list[str]) -> str:
        fm = f"---\ntitle: {json.dumps(title, ensure_ascii=False)}\ntype: {p['type'][:-1] if p['type'] != 'entities' else 'entity'}\n" \
             f"domain: {p['domain']}\nsources: {len(raws)}\ncreated: {created}\nupdated: {created}\n---\n\n# {title}\n\n"
        cites = "\n".join(f"- `{r}`" for r in raws)
        if p["type"] in ("concepts", "entities"):
            intro = next((s for s in self.sources if title in s.get("introduces", [])), None)
            body = f"**{title}**: {p['desc']}" + (f" ([[{intro['title']}]])." if intro else ".") + "\n\n"
            rel = [r for r in p.get("related", []) if r in self.live]
            if rel:
                body += "- Related: " + ", ".join(f"[[{r}]]" for r in rel) + ".\n"
            topic = next((t for t, q in self.pages.items() if q["type"] == "topics" and q["domain"] == p["domain"]), None)
            if topic and topic in self.live:
                body += f"- Part of [[{topic}]].\n"
        elif p["type"] == "sources":
            s = p["src"]
            body = f"{p['desc'][0].upper() + p['desc'][1:]}. Captured from {s['platform']} ({s['kind']}).\n\n## Key points\n\n"
            body += "\n".join(f"- [[{t}]]: {self.pages[t]['desc']}." for t in s.get("introduces", []) + s.get("mentions", [])[:3]
                              if t in self.live) + "\n"
        else:
            members = [m for m in p["members"] if m in self.live]
            body = f"{p['desc'][0].upper() + p['desc'][1:]}.\n\n## Concepts and names\n\n" + \
                   "\n".join(f"- [[{m}]] — {self.pages[m]['desc']}" for m in members) + "\n\n## Sources\n\n" + \
                   "\n".join(f"- [[{s}]]" for s in p["sources"] if s in self.live) + "\n"
            return fm + body
        return fm + body + f"\n## Sources\n\n{cites}\n"

    def write_index(self):
        from memexos.console.data import INDEX_LINE
        idx = self.vault / "index.md"
        text = idx.read_text(encoding="utf-8")
        have = {m.group(1).strip() for m in INDEX_LINE.finditer(text)}
        heads = {"topics": "## Topics", "concepts": "## Concepts", "entities": "## Entities", "sources": "## Sources"}
        for kind, head in heads.items():
            new = [f"- [[{t}]] — {p['desc']}" + (f" · {p['domain']}" if kind == "sources" else "")
                   for t, p in self.pages.items() if p["type"] == kind and t in self.live and t not in have]
            if not new:
                continue
            i = text.index(head)
            j = text.find("\n## ", i + len(head))
            j = len(text.rstrip().rstrip("-").rstrip()) if j < 0 else j
            text = text[:j].rstrip() + "\n" + "\n".join(new) + "\n\n" + text[j:].lstrip("\n")
        idx.write_text(text, encoding="utf-8")

    def catalog_line(self, s: dict) -> str:
        ct = {"video": "explainer", "web": "explainer", "tweet": "opinion", "podcast": "interview", "session": "project-log",
              "doc": "project-log"}[s["kind"]]
        ev = {"tweet": "opinion", "session": "first-hand", "doc": "first-hand", "podcast": "third-party"}.get(s["kind"], "third-party")
        if s["platform"] in ("arxiv", "sqlite", "anthropic", "12factor"):
            ev = "official"
        return json.dumps({"raw": s["raw"], "domain": s["domain"], "model_domain": s["domain"], "secondary": [], "content_type": ct,
                           "level": self.rng.choice(["intro", "intro", "intermediate", "expert"]), "evidence": ev,
                           "usage": [self.rng.choice(["understand", "learn-skill", "facts", "compare"])],
                           "durability": self.rng.choice(["long-term", "long-term", "within-year"]), "scope_note": "",
                           "summary": s["desc"][:120], "taxonomy": "v1", "model": "demo", "domain_by": "model",
                           "ts": (self.today - dt.timedelta(days=s["days_ago"])).isoformat() + "T03:30:00"}, ensure_ascii=False)

    def write_history(self):
        """One capture commit and one compile commit per day, so the growth chart and page histories look lived-in."""
        print(f"writing {len(self.sources)} sources and their pages")
        self.live = {p.stem for p in (self.vault / "wiki").rglob("*.md")}
        created: dict[str, str] = {}
        cat = self.vault / ".memex" / "catalog.jsonl"
        ingested = self.vault / ".memex" / "ingested.txt"
        by_day: dict[int, list[dict]] = {}
        for s in self.sources:
            by_day.setdefault(s["days_ago"], []).append(s)
        for days_ago in sorted(by_day, reverse=True):
            batch = by_day[days_ago]
            day = (self.today - dt.timedelta(days=days_ago)).isoformat()
            for s in batch:
                (self.vault / s["raw"]).write_text(self.raw_text(s), encoding="utf-8")
            self.git("add", "-A")
            self.git("commit", "-q", "-m", "capture: " + ", ".join(Path(s["raw"]).name for s in batch)[:300],
                     date=self.at(days_ago, 3, 21) if days_ago else self.at(0, 3, 21))
            todo = [s for s in batch if not s.get("queued")]
            if not todo:
                continue
            for s in todo:
                for t in [s["title"]] + s.get("introduces", []):
                    if t not in self.live:
                        self.live.add(t)
                        created[t] = day
            for t, p in self.pages.items():
                if p["type"] == "topics" and t not in self.live and sum(x in self.live for x in p["sources"]) >= 2:
                    self.live.add(t)
                    created[t] = day
            touched = {t for s in todo for t in [s["title"]] + s.get("introduces", []) + s.get("mentions", [])} | \
                      {t for t, p in self.pages.items() if p["type"] == "topics" and t in self.live}
            for t in touched:
                if t not in self.live or t not in self.pages:
                    continue
                p = self.pages[t]
                raws = [s["raw"] for s in self.sources if not s.get("queued") and s["title"] in self.live and
                        (s["title"] == t or t in s.get("introduces", []) or t in s.get("mentions", []))]
                f = self.vault / "wiki" / p["type"] / f"{t}.md"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(self.page_text(t, p, created.get(t, day), raws), encoding="utf-8")
            self.write_index()
            with open(cat, "a", encoding="utf-8") as fh:
                fh.write("".join(self.catalog_line(s) + "\n" for s in todo))
            with open(ingested, "a", encoding="utf-8") as fh:
                fh.write("".join(s["raw"] + "\n" for s in todo))
            with open(self.vault / "log.md", "a", encoding="utf-8") as fh:
                for s in todo:
                    fh.write(f"\n## [{day}] ingest | {s['title']}\n\n- Source: `{s['raw']}`\n- Created source page: [[{s['title']}]]\n")
            self.git("add", "-A")
            hh = 4 if days_ago else 9
            self.git("commit", "-q", "-m", "auto-ingest: " + " ".join(Path(s["raw"]).name for s in todo)[:900],
                     date=self.at(days_ago, hh, 12 + len(todo)))

    # ------------------------------------------------------------ activity
    def simulate(self):
        from memexos import events
        print("simulating two weeks of activity")
        clock = Clock()
        events.time = clock
        st = self.cfg.paths.state
        self.clock, self.st, self.ev = clock, st, events
        recent = [s for s in self.sources if s["days_ago"] <= 13]
        for days_ago in range(13, -1, -1):
            todays = [s for s in recent if s["days_ago"] == days_ago]
            self.night(days_ago, todays)
            if days_ago in (1, 2):
                self.daytime(days_ago)
        self.morning_today()
        self.running_compile()
        events.time = time

    def tick(self, seconds: float):
        self.clock.t += seconds * self.rng.uniform(0.85, 1.15)

    def model(self, role: str, backend: str, model: str, cost: float, seconds: float):
        self.tick(seconds)
        self.ev.emit(self.st, "model.call", role=role, kind="agent" if role == "writer" else "structured", backend=backend,
                     model=model, ok=True, seconds=round(seconds, 1), cost_usd=round(cost * self.rng.uniform(0.8, 1.25), 4),
                     limited=False, transient=False, tokens={"input_tokens": int(cost * 90000), "output_tokens": int(cost * 9000)},
                     denials=0)

    def compile(self, batch: list[dict], fail_first: bool = False):
        ev, st = self.ev, self.st
        files = [s["raw"] for s in batch]
        ev.emit(st, "ingest.start", files=files, remaining=len(files))
        branch = f"staging/{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock.t))}"
        ev.emit(st, "stage.open", branch=branch)
        with ev.step(st, "write") as info:
            self.model("writer", "claude-code", "claude-opus", 0.55 * len(batch), 780 * len(batch))
            info["result"] = "ok"
        n_pages = sum(1 + len(s.get("introduces", [])) for s in batch)
        ev.emit(st, "writer.done", summary=f"{len(batch)} source(s): {n_pages} pages created or updated")
        ev.emit(st, "gate1.result", passed=True, errors=0, warnings=self.rng.choice([0, 0, 1, 2]), round=1)
        with ev.step(st, "selfcheck") as info:
            self.model("judge", "claude-code:sonnet", "sonnet", 0.12 * len(batch), 150)
            info["result"] = "ok"
        ev.emit(st, "selfcheck.result", code=0, summary=f"self-check: {8 * len(batch)} sentences checked, 0 unsupported")
        for rnd in (1, 2) if fail_first else (1,):
            d = f"{time.strftime('%Y-%m-%d', time.localtime(self.clock.t))}-{branch.replace('/', '_')}" + (f"-r{rnd}" if rnd > 1 else "")
            failed = fail_first and rnd == 1
            mode = "ingest" if rnd == 1 else "fix"
            with ev.step(st, "gate2", mode=mode) as info:
                self.model("reviewer", "codex", "gpt-5-codex", 0.35 * len(batch), 480)
                info.update(result="failed" if failed else "passed", model="gpt-5-codex", problems=2 if failed else 0)
            self.review_dir(d, batch, failed)
            ev.emit(st, "gate2.result", code=1 if failed else 0, passed=not failed, mode=mode, problems=2 if failed else 0,
                    backend="codex", model="gpt-5-codex", seconds=300, cost_usd=round(0.35 * len(batch), 3), dir=d)
            if failed:
                with ev.step(st, "rework"):
                    self.model("writer", "claude-code", "claude-opus", 0.3, 420)
                ev.emit(st, "gate1.result", passed=True, errors=0, warnings=0, round=2)
        self.tick(8)
        ev.emit(st, "publish", branch=branch, message="auto-ingest: " + " ".join(Path(f).name for f in files)[:280])
        with ev.run(st, "reindex"):                     # publishing starts a reindex in the background
            with ev.step(st, "index"):
                self.tick(2)
                ev.emit(st, "index.update", added=n_pages, updated=len(batch) + 2, removed=0, seconds=1.4)

    def review_dir(self, name: str, batch: list[dict], failed: bool):
        d = self.st / "gate2" / name
        d.mkdir(parents=True, exist_ok=True)
        s = batch[0]
        t = (s.get("introduces") or [s["title"]])[0]
        page = f"wiki/{self.pages[t]['type']}/{t}.md"
        claims = [{"page": page, "claim": f"{t}: {self.pages[t]['desc']}.", "evidence": s["desc"], "raw_location": s["raw"],
                   "verdict": "SUPPORTED", "critical": False, "reason": ""}]
        if failed:
            claims += [{"page": page, "claim": f"{t} was first described in 2019 and is now used by most teams.",
                        "evidence": "The source gives no year and no adoption figures.", "raw_location": s["raw"],
                        "verdict": "UNSUPPORTED", "critical": True, "reason": "The year and the share of teams are not in the source."},
                       {"page": f"wiki/sources/{s['title']}.md", "claim": "The author measured the effect on 40 projects.",
                        "evidence": "The author describes the approach; no measurement is reported.", "raw_location": s["raw"],
                        "verdict": "CONTRADICTED", "critical": False, "reason": "An opinion was turned into a measured result."}]
        review = {"claims": claims, "overwrites": [], "injections": [], "key_points": [], "integration_notes": [],
                  "passed": not failed}
        (d / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "report.json").write_text(json.dumps({"raws": [x["raw"] for x in batch], "passed": not failed}), encoding="utf-8")

    def night(self, days_ago: int, todays: list[dict]):
        ev, st = self.ev, self.st
        self.clock.t = self.at(days_ago, 3, 17, 4)
        with ev.run(st, "nightly"):
            with ev.job("intake"):
                channels = [("sessions", 6), ("rss", 9), ("youtube", 7), ("bilibili", 4), ("x", 11)]
                with ev.step(st, "collect", channels=len(channels)):
                    self.tick(420)
                items = sum(n for _, n in channels)
                ev.emit(st, "intake.run", items=items, batches=len(channels))
                admitted = list(todays)
                for i, (ch, n) in enumerate(channels, 1):
                    kept = [s for s in admitted if self.channel_of(s) == ch]
                    with ev.step(st, "admit", channel=ch, items=n):
                        self.model("worker", "claude-code", "claude-opus", 0.04 * n, 55 * n)
                    ev.emit(st, "intake.batch", i=i, of=len(channels), channel=ch, admit=len(kept), reject=n - len(kept) - (1 if ch == "x" else 0),
                            uncertain=1 if ch == "x" else 0, seconds=22 * n, cost=f"${0.04 * n:.2f}")
                with ev.step(st, "capture", items=len(admitted)):
                    for s in admitted:
                        self.tick(14 if s["kind"] != "video" else 75)
                        typ = "capture.video" if s["kind"] in ("video", "podcast") else "capture.done"
                        ev.emit(st, typ, path=s["raw"], how="subtitles" if s["kind"] == "video" else "transcript")
                ev.emit(st, "intake.done", admit=len(admitted), reject=items - len(admitted) - 1, uncertain=1, blocked=0, missing=0)
            if any(s["kind"] == "video" for s in todays):
                with ev.job("visual"):
                    v = next(s for s in todays if s["kind"] == "video")
                    with ev.step(st, "visual", path=v["raw"]):
                        ev.emit(st, "visual.start", path=v["raw"])
                        self.model("vision", "codex", "gpt-5-codex", 0.2, 140)
                        ev.emit(st, "visual.done", path=v["raw"], frames=24)
            with ev.job("classify"):
                with ev.step(st, "classify", items=len(todays)):
                    self.model("judge", "claude-code:sonnet", "sonnet", 0.02 * max(1, len(todays)), 18 * max(1, len(todays)))
                    ev.emit(st, "classify.batch", done=len(todays), total=len(todays), seconds=40)
            with ev.job("usage"):
                with ev.step(st, "usage", questions=self.rng.randint(4, 9)):
                    self.model("worker", "claude-code", "claude-opus", 0.18, 60)
            with ev.job("inbox"):
                with ev.step(st, "triage", items=4):
                    self.model("worker", "claude-code", "claude-opus", 0.12, 50)
            with ev.job("ingest"):
                todo = [s for s in todays if not s.get("queued")]
                night_n = 2 if days_ago == 0 else 4
                for i in range(0, min(len(todo), night_n), 2):
                    fail = (days_ago == 1 and i == 0) or (days_ago > 2 and self.rng.random() < 0.3)
                    self.compile(todo[i:i + 2], fail_first=fail)
            ev.emit(st, "nightly.done")
        # what the night did not get to is compiled by the hourly job during the day
        for k, s in enumerate(todo[night_n:]):
            self.clock.t = self.at(days_ago, 10 + 3 * k, self.rng.randint(2, 20))
            with ev.run(st, "ingest"):
                self.compile([s], fail_first=(days_ago == 2 and k == 0))

    def channel_of(self, s: dict) -> str:
        return {"session": "sessions", "video": "youtube" if s["platform"] == "youtube" else "bilibili", "tweet": "x"}.get(s["kind"], "rss")

    def daytime(self, days_ago: int):
        ev, st = self.ev, self.st
        for hh in (11, 15, 20):
            self.clock.t = self.at(days_ago, hh, self.rng.randint(0, 50))
            with ev.run(st, "reindex"):
                with ev.step(st, "index"):
                    self.tick(1.2)
                    ev.emit(st, "index.update", added=0, updated=self.rng.randint(1, 4), removed=0, seconds=1.1)
        self.clock.t = self.at(days_ago, 16, 40)
        with ev.run(st, "add-url"):
            with ev.step(st, "capture"):
                self.tick(3)
        # questions agents asked during the day
        from demo_content import QUESTIONS
        for q, _ in self.rng.sample(QUESTIONS, 5):
            self.clock.t = self.at(days_ago, self.rng.randint(9, 22), self.rng.randint(0, 59))
            ev.emit(st, "search", job="search", query=q, mode="fts", hits=self.rng.randint(4, 10), seconds=round(self.rng.uniform(0.05, 0.4), 2), top=[])

    def morning_today(self):
        ev, st = self.ev, self.st
        self.clock.t = min(self.at(0, 9, 40), self.now - 3 * 3600)
        with ev.run(st, "eval"):
            with ev.step(st, "eval_search"):
                self.tick(12)
        from demo_content import QUESTIONS
        for q, _ in QUESTIONS[:6]:
            self.clock.t = self.now - self.rng.randint(15, 300) * 60
            ev.emit(st, "search", job="search", query=q, mode="fts", hits=self.rng.randint(4, 10), seconds=round(self.rng.uniform(0.05, 0.4), 2), top=[])

    def running_compile(self):
        """A compile of the newest sources that is still writing: a sleeping process stands in for it."""
        ev, st = self.ev, self.st
        sleeper = subprocess.Popen(["sleep", "5400"], start_new_session=True, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.clock.t = self.now - 7 * 60
        ev.new_run("ingest")
        ev.emit(st, "run.start", pid=sleeper.pid)
        files = [s["raw"] for s in self.queued[:2]]
        ev.emit(st, "ingest.start", files=files, remaining=len(self.queued))
        ev.emit(st, "stage.open", branch=f"staging/{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock.t))}")
        self.clock.t += 40
        ev.emit(st, "step.start", name="write")
        ev._run.clear()
        os.environ.pop("MEMEX_RUN", None)

    # ------------------------------------------------------------ agents using the knowledge base
    def sessions(self):
        """Claude Code session logs in which an agent searched the demo vault; extracted by the real usage code."""
        from demo_content import QUESTIONS
        from memexos import usage
        print("agent sessions that use the knowledge base")
        proj = self.home / ".claude" / "projects" / "-demo-projects-agent-lab"
        proj.mkdir(parents=True, exist_ok=True)
        cwd = str(self.home / "projects" / "agent-lab")
        for i, (q, page) in enumerate(QUESTIONS):
            t0 = self.now - (len(QUESTIONS) - i) * 2.7 * 3600
            stamp = lambda k, t0=t0: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t0 + k * 20))
            kw = " ".join(w for w in re.findall(r"[A-Za-z0-9']+", page.lower()))
            search_cmd = f'memex search "{q}" --kw "{kw}"'
            out = self.memex("search", q, "--kw", kw, check=False)
            got = self.memex("get", page, check=False)
            p = self.pages.get(page, {})
            answer = (f"{page}: {p.get('desc', '')}. (From the knowledge base: [[{page}]].)" if p else
                      f"The knowledge base covers this on [[{page}]].")
            lines = [
                {"type": "user", "message": {"role": "user", "content": q}, "timestamp": stamp(0), "cwd": cwd, "sessionId": f"demo-{i}"},
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"t{i}a", "name": "Bash", "input": {"command": search_cmd}}]}, "timestamp": stamp(1), "cwd": cwd},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}a", "content": out[:4000]}]}, "timestamp": stamp(2), "cwd": cwd},
                {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"t{i}b", "name": "Bash", "input": {"command": f'memex get "{page}"'}}]}, "timestamp": stamp(3), "cwd": cwd},
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}b", "content": got[:4000]}]}, "timestamp": stamp(4), "cwd": cwd},
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
                 "timestamp": stamp(5), "cwd": cwd},
            ]
            (proj / f"demo-{i:02d}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        usage.extract(self.cfg)
        evs = [e for e in (json.loads(line) for line in (usage.dirs(self.cfg) / "events.jsonl").read_text().splitlines()) if e.get("kind") == "real"]
        verdicts = ["helped"] * 11 + ["partly"] * 3 + ["honest_gap", "not_helped"]
        rows = [{"key": e["key"], "verdict": verdicts[i % len(verdicts)], "reason": "demo judgment", "bad_pages": [], "by": "demo",
                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S")} for i, e in enumerate(evs)]
        (usage.dirs(self.cfg) / "judgments.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    # ------------------------------------------------------------ the inbox
    def inbox(self):
        from demo_content import INBOX
        from memexos import inbox as I
        day = self.today.isoformat()
        f = self.vault / "inbox" / f"{day}-inbox.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(INBOX, encoding="utf-8")
        cand = self.vault / "inbox" / "candidates" / day / "README.md"
        cand.parent.mkdir(parents=True, exist_ok=True)
        cand.write_text("# Unsure at admission\n\n- `x:demo-1842`: a long thread on prompt caching costs; useful numbers, but from an "
                        "unnamed source (https://example.com/x/prompt-caching-thread)\n", encoding="utf-8")
        triage = {
            "Two sources disagree on the year Anki was released": {
                "kind": "decide", "why": "Only you can say which source to trust here.",
                "question": "Which year should the page on Anki give?", "context": "The changelog says 2006; a newer video says 2007.",
                "options": [{"label": "Keep 2006", "effect": "The page keeps 2006 and cites the changelog."},
                            {"label": "Note both", "effect": "The page says sources differ (2006 or 2007)."}],
                "recommend": 0, "recommend_why": "The project's own changelog is the stronger source.", "agent_task": "",
                "needs_web": False, "needs_local": False, "pages": ["wiki/entities/Anki.md"]},
            "New category proposal: Personal finance tools": {
                "kind": "decide", "why": "Categories shape how everything is sorted.",
                "question": "Add a category for personal finance tools?", "context": "6 recent sources fit nowhere else.",
                "options": [{"label": "Add it", "effect": "A new category under Business."},
                            {"label": "Keep them in Finance and economics", "effect": "Nothing changes."}],
                "recommend": 0, "recommend_why": "The sources share a clear theme.", "agent_task": "", "needs_web": False,
                "needs_local": False, "pages": []},
            "Should liked videos about games be collected?": {
                "kind": "decide", "why": "This is about what you want collected.",
                "question": "Keep reading the liked-videos list?", "context": "9 game videos were rejected last week.",
                "options": [{"label": "Keep reading it", "effect": "Admission keeps filtering."},
                            {"label": "Stop reading liked videos", "effect": "Only watch later and playlists are read."}],
                "recommend": 0, "recommend_why": "Admission already filters them out at little cost.", "agent_task": "",
                "needs_web": False, "needs_local": False, "pages": []},
            "The page on Raft cites only a talk": {
                "kind": "agent", "why": "An agent can fetch the paper and check the page.", "question": "", "context": "",
                "options": [], "recommend": -1, "recommend_why": "", "agent_task": "Fetch the Raft paper and check the page against it.",
                "needs_web": True, "needs_local": False, "pages": ["wiki/entities/Raft.md"]},
        }
        for it in I.all_items(self.cfg):
            tri = triage.get(it["title"])
            if tri:
                I.append(self.cfg, {"id": it["id"], "event": "triaged", "by": "demo", "triage": tri})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "inbox: reports from the night", date=self.at(0, 4, 40))


if __name__ == "__main__":
    sys.exit(main())
