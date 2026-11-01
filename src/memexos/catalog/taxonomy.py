"""How the domain tree grows: draft → pending → official.

Without this, a classifier that finds no fitting category invents one, nobody handles the proposals, and the same
idea ends up under several names. Instead:
  - a judge screens first (if configured): candidates are all official categories and drafts; only when the most
    likely one is confident AND its stated scope really covers the source is the domain decided there;
  - otherwise the worker model decides: an official category first, then a draft, and only then a new draft; before
    a draft is created the program checks for the same or a synonymous name, names the person rejected, and the
    per-top-level limit;
  - drafts can hold sources, but writers and readers only see them as "Top (provisional: name)";
  - once a draft has enough real material (distinct works, distinct capture days, and a blind re-check that puts
    most members back into it) it becomes pending and the person decides: approve (optionally renamed), merge, reject.
    Drafts with no new material for a long time expire.

The registry .memex/taxonomy-nodes.jsonl is an append-only event log; the current state is a replay of it.
The official table is the first table after the "domains" marker in .memex/taxonomy.md.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

from memexos import i18n, judge
from memexos.util import append_jsonl, atomic_write, split_frontmatter

DRAFT = "draft:"
ACTIVE = ("draft", "pending")
ROW = re.compile(r"^\|\s*([^|`\-\s][^|]*?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")
HEADERS = {"Top", "一级"}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Taxonomy:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vault = cfg.vault
        self.md = cfg.paths.ledger / "taxonomy.md"
        self.nodes = cfg.paths.ledger / "taxonomy-nodes.jsonl"
        self._cache: dict = {}

    def th(self, k: str, default):
        return type(default)(self.cfg.get(f"taxonomy.{k}", default))

    # -- official table --
    def official(self) -> "collections.OrderedDict[str, str]":
        sig = (self.md.stat().st_mtime, self.md.stat().st_size) if self.md.exists() else None
        if self._cache.get("off_sig") == sig and "off" in self._cache:
            return self._cache["off"]
        out: collections.OrderedDict[str, str] = collections.OrderedDict()
        text = self.md.read_text(encoding="utf-8") if self.md.exists() else ""
        sec = text.split("<!-- domains", 1)[-1] if "<!-- domains" in text else ""
        started = False
        for line in sec.splitlines()[1:]:
            m = ROW.match(line)
            if m:
                started = True
                if m.group(1) not in HEADERS:
                    out[f"{m.group(1)}/{m.group(2)}"] = m.group(3)
            elif started and not line.startswith("|"):
                break
        self._cache.update(off=out, off_sig=sig)
        return out

    def tops(self) -> list[str]:
        return list(dict.fromkeys(k.split("/")[0] for k in self.official()))

    def version(self) -> str:
        m = re.search(r"^> (?:Version|版本) (v[\d.]+)", self.md.read_text(encoding="utf-8") if self.md.exists() else "", re.M)
        return m.group(1) if m else "v1"

    def add_official(self, parent: str, name: str, desc: str, why: str) -> str:
        """After approval: add the row under its top-level domain, bump the minor version, note it in the history."""
        text = self.md.read_text(encoding="utf-8")
        lines = text.split("\n")
        rows = [i for i, l in enumerate(lines) if (m := ROW.match(l)) and m.group(1) == parent]
        if not rows:
            raise RuntimeError(i18n.t("catalog.no_top", top=parent))
        if any((m := ROW.match(l)) and m.group(1) == parent and m.group(2) == name for l in lines):
            return self.version()
        lines.insert(rows[-1] + 1, f"| {parent} | {name} | {desc.replace('|', '/').strip()} |")
        text = "\n".join(lines)
        old = self.version()
        parts = old.lstrip("v").split(".")
        new = "v" + (f"{parts[0]}.{int(parts[1]) + 1}" if len(parts) > 1 else f"{parts[0]}.1")
        text = re.sub(r"^> (Version|版本) v[\d.]+", lambda m: f"> {m.group(1)} {new}", text, count=1, flags=re.M)
        text = text.rstrip("\n") + f"\n- {new} ({time.strftime('%Y-%m-%d')}): `{parent}/{name}`. {why}\n"
        atomic_write(self.md, text)
        self._cache.clear()
        return new

    # -- registry --
    def events(self) -> list[dict]:
        out = []
        if self.nodes.exists():
            for line in self.nodes.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    def append(self, ev: dict) -> dict:
        ev = {"ts": now(), **ev}
        append_jsonl(self.nodes, [ev])
        self._cache.pop("state", None)
        return ev

    def state(self) -> "collections.OrderedDict[str, dict]":
        st: collections.OrderedDict[str, dict] = collections.OrderedDict()
        for e in self.events():
            t, i = e.get("event"), e.get("id")
            if t == "create":
                st[i] = {"id": i, "parent": e["parent"], "name": e["name"], "status": "draft",
                         "aliases": [a for a in e.get("from") or [] if a != e["name"]], "definition": e.get("definition", ""),
                         "includes": e.get("includes", ""), "excludes": e.get("excludes", ""), "nearest": e.get("nearest") or [],
                         "created": e["ts"], "by": e.get("by", ""), "why": e.get("why", ""), "log": []}
                continue
            n = st.get(i)
            if not n:
                continue
            n["log"].append(e)
            if t == "alias" and e["name"] != n["name"] and e["name"] not in n["aliases"]:
                n["aliases"].append(e["name"])
            elif t == "recheck":
                n["recheck"] = e
            elif t == "pending":
                n["status"], n["pending"] = "pending", e
            elif t == "approved":
                if e.get("name") and e["name"] != n["name"]:
                    n["aliases"].append(n["name"])
                    n["name"] = e["name"]
                n["status"], n["official"] = "official", e.get("official") or f"{n['parent']}/{n['name']}"
            elif t == "merged":
                n["status"], n["into"] = "merged", e["into"]
            elif t == "rejected":
                n["status"] = "rejected"
            elif t == "expired":
                n["status"] = "expired"
        return st

    def active(self, st=None) -> list[dict]:
        return [n for n in (st or self.state()).values() if n["status"] in ACTIVE]

    @staticmethod
    def key(n: dict) -> str:
        return f"{n['parent']}/{n['name']}"

    def find(self, ident: str, st=None) -> dict | None:
        st = st or self.state()
        if ident in st:
            return st[ident]
        plain = ident.removeprefix(DRAFT)
        parent, _, name = plain.partition("/")
        for n in sorted(st.values(), key=lambda n: n["status"] not in ACTIVE):
            if self.key(n) == plain or (n["parent"] == parent and name in n["aliases"]):
                return n
        return None

    # -- reading domain values --
    def resolve(self, domain: str | None, st=None, off=None):
        """("official", "Top/Sub") | ("draft", node) | ("gone", node: rejected or expired) | (None, value: unknown)."""
        st = self.state() if st is None else st
        off = self.official() if off is None else off
        d = (domain or "").strip()
        plain = d.removeprefix(DRAFT)
        if plain in off:
            return "official", plain
        if "/" not in plain:
            return None, d
        parent, name = plain.split("/", 1)
        hits = [n for n in st.values() if n["parent"] == parent and (n["name"] == name or name in n["aliases"])]
        for want in (ACTIVE, ("official",), ("merged",), ("rejected", "expired")):
            for n in hits:
                if n["status"] in want:
                    if n["status"] in ACTIVE:
                        return "draft", n
                    if n["status"] == "official":
                        return "official", n["official"]
                    if n["status"] == "merged":
                        return ("official", n["into"]) if n["into"] in off else self.resolve(n["into"], st, off)
                    return "gone", n
        return None, d

    def canon(self, domain, st=None, off=None) -> str | None:
        kind, x = self.resolve(domain, st, off)
        if kind == "official":
            return x
        if kind == "draft":
            return DRAFT + self.key(x)
        return None

    def label(self, domain, st=None, off=None) -> str:
        """How writers, readers and source pages see a domain: official as is; drafts as "Top (provisional: name)"."""
        kind, x = self.resolve(domain, st, off)
        if kind == "official":
            return x
        if kind == "draft":
            return i18n.t("catalog.provisional", top=x["parent"], name=x["name"])
        if kind == "gone":
            return i18n.t("catalog.reclassify", top=x["parent"])
        d = (domain or "").removeprefix(DRAFT)
        return i18n.t("catalog.provisional", top=d.split("/")[0], name=d.split("/", 1)[1]) if (domain or "").startswith(DRAFT) and "/" in d else d

    @staticmethod
    def top(domain: str | None) -> str:
        return re.split(r"[/（(]", (domain or "").removeprefix(DRAFT), maxsplit=1)[0].strip()

    # -- members and thresholds --
    def work_key(self, rel: str) -> str:
        """One work, however often it was re-captured: by URL (video id for video sites), local path, session, else name."""
        try:
            fm, _ = split_frontmatter((self.vault / rel).read_text(encoding="utf-8", errors="ignore")[:4000])
        except OSError:
            fm = {}
        url = str(fm.get("url") or "")
        if url:
            u = urllib.parse.urlsplit(url)
            host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
            q = urllib.parse.parse_qs(u.query)
            bv = re.search(r"(BV[0-9A-Za-z]{10})", url)
            if bv:
                return f"bilibili:{bv.group(1)}:{(q.get('p') or ['1'])[0]}"
            if "youtube" in host or host == "youtu.be":
                return "youtube:" + (q.get("v") or [u.path.rsplit("/", 1)[-1]])[0]
            keep = urllib.parse.urlencode(sorted((k, v[0]) for k, v in q.items() if k in ("id", "p", "page")))
            return f"url:{host}{u.path.rstrip('/')}" + (f"?{keep}" if keep else "")
        if fm.get("original"):
            return f"file:{fm['original']}"
        if fm.get("sessions"):
            return f"session:{fm['sessions']}"
        return "name:" + re.sub(r"^\d{4}-\d{2}-\d{2}-", "", Path(rel).name)

    @staticmethod
    def day(rel: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})-", Path(rel).name)
        return m.group(1) if m else ""

    def members(self, n: dict, cat: dict, st=None, off=None) -> list[str]:
        out = []
        for raw, r in cat.items():
            kind, x = self.resolve(r.get("domain"), st, off)
            if kind == "draft" and x["id"] == n["id"]:
                out.append(raw)
        return sorted(out)

    def progress(self, n: dict, cat: dict, st=None, off=None) -> dict:
        mem = self.members(n, cat, st, off)
        days = {self.day(r) for r in mem}
        return {"members": mem, "works": len({self.work_key(r) for r in mem}), "days": len(days),
                "last": max([d for d in days if d] + [n["created"][:10]])}

    def gaps(self, n: dict, prog: dict) -> list[str]:
        out = []
        if prog["works"] < self.th("min_works", 8):
            out.append(i18n.t("catalog.need_works", n=self.th("min_works", 8) - prog["works"]))
        if prog["days"] < self.th("min_days", 3):
            out.append(i18n.t("catalog.need_days", n=self.th("min_days", 3) - prog["days"]))
        rc = n.get("recheck")
        if not out and rc and set(rc.get("members") or []) == set(prog["members"]) and rc["rate"] < self.th("min_stable", 0.8):
            out.append(i18n.t("catalog.need_stable", rate=f"{rc['rate']:.0%}", want=f"{self.th('min_stable', 0.8):.0%}"))
        return out

    # -- the judge's first-pass screen --
    def candidates(self, st=None, off=None, exclude=(), drafts=True) -> "collections.OrderedDict[str, str]":
        st = self.state() if st is None else st
        off = self.official() if off is None else off
        c = collections.OrderedDict((k, v) for k, v in off.items() if k not in exclude)
        if drafts:
            for n in self.active(st):
                if self.key(n) not in exclude:
                    c[self.key(n)] = f"{n['definition']} {i18n.t('catalog.includes')}: {n['includes']}. {i18n.t('catalog.excludes')}: {n['excludes']}"
        return c

    def screen(self, states: dict[str, str], st=None, off=None, exclude=(), drafts=True) -> dict:
        """{raw: {top, p, p_none, scope, alts, adopt} or None}. Two questions per source: which category (with a
        "none fits" option), then whether the top one's stated scope really covers it. Only both confident → adopt."""
        j = judge.get(self.cfg)
        ok, why = j.available() if j.name != "none" else (False, "no judge")
        if not ok:
            raise judge.JudgeUnavailable(why)
        st = self.state() if st is None else st
        off = self.official() if off is None else off
        cand = self.candidates(st, off, exclude, drafts)
        to_dir = lambda k: k if k in off else DRAFT + k  # noqa: E731
        out = {}
        for raw, text in states.items():
            r = j.choice({"source": text}, cand, i18n.t("catalog.choice_instruction"))
            if not r:
                out[raw] = None
                continue
            probs, choice = r["probabilities"], r["choice"]
            p = float(probs.get(choice, 0.0))
            scope = j.yes_no({"source": text}, i18n.t("catalog.scope_instruction", name=choice, desc=cand.get(choice, ""))) \
                if choice in cand else None
            alts = [(to_dir(k), round(float(v), 3)) for k, v in sorted(probs.items(), key=lambda kv: -float(kv[1])) if k in cand and float(v) >= 0.05][:3]
            p_none = float(probs.get(judge.NONE_FITS, 0.0))
            adopt = bool(choice in cand and p >= self.th("judge_min_top", 0.9) and p_none <= self.th("judge_max_none", 0.1)
                         and scope is not None and scope >= self.th("judge_min_scope", 0.8))
            out[raw] = {"top": to_dir(choice) if choice in cand else None, "p": round(p, 3), "p_none": round(p_none, 3),
                        "scope": None if scope is None else round(scope, 3), "alts": alts, "adopt": adopt}
        return out

    def same_topic(self, nd: dict, st, off, examples=()) -> str | None:
        """Is a proposed draft another name for an existing category, draft or rejected name under the same top level?"""
        parent = nd["parent"]
        opts = collections.OrderedDict((k, v) for k, v in off.items() if k.startswith(parent + "/"))
        for n in st.values():
            if n["parent"] == parent and n["status"] in ("draft", "pending", "rejected"):
                opts.setdefault(self.key(n), n["definition"])
        if not opts:
            return None
        j = judge.get(self.cfg)
        try:
            r = j.choice({"proposed_category": f"{parent}/{nd['name']}", "definition": nd.get("definition", ""),
                          "includes": nd.get("includes", ""), "excludes": nd.get("excludes", ""), "example_sources": list(examples)[:5]},
                         opts, i18n.t("catalog.same_instruction"))
        except judge.JudgeUnavailable:
            return None
        if r and r["choice"] in opts and float(r["probabilities"].get(r["choice"], 0)) >= self.th("synonym_min", 0.5):
            return r["choice"]
        return None

    def propose(self, nd: dict, examples=(), by: str = "", raws=(), synonyms: bool = True):
        """A draft the model wants to create → ("official", key) | ("draft", node) | ("refused", reason)."""
        st, off = self.state(), self.official()
        parent, name = nd["parent"].strip(), re.sub(r"\s+", " ", nd["name"].strip().removeprefix(DRAFT))
        if "/" in name:
            p2, name = name.split("/", 1)
            parent = parent or p2

        def refuse(reason):
            self.append({"event": "refused", "parent": parent, "name": name, "reason": reason, "raws": list(raws), "by": by})
            return "refused", reason

        if parent not in self.tops():
            return refuse(i18n.t("catalog.refuse_top", top=parent))
        if not name:
            return refuse(i18n.t("catalog.refuse_noname"))
        kind, x = self.resolve(DRAFT + f"{parent}/{name}", st, off)
        if kind in ("official", "draft"):
            return kind, x
        if kind == "gone" and x["status"] == "rejected":
            return refuse(i18n.t("catalog.refuse_rejected", name=name))
        if synonyms:
            same = self.same_topic({**nd, "parent": parent, "name": name}, st, off, examples)
            if same:
                k2, x2 = self.resolve(DRAFT + same, st, off)
                if k2 == "official":
                    return "official", x2
                if k2 == "draft":
                    self.append({"event": "alias", "id": x2["id"], "name": name, "by": "judge-same-topic"})
                    return "draft", self.state()[x2["id"]]
                if k2 == "gone":
                    return refuse(i18n.t("catalog.refuse_rejected", name=x2["name"]))
        if len([n for n in self.active(st) if n["parent"] == parent]) >= self.th("max_drafts_per_top", 5):
            return refuse(i18n.t("catalog.refuse_full", top=parent, n=self.th("max_drafts_per_top", 5)))
        if not all((nd.get(k) or "").strip() for k in ("definition", "includes", "excludes")):
            return refuse(i18n.t("catalog.refuse_incomplete"))
        nid = "tx-" + hashlib.sha1(f"{parent}/{name}/{now()}".encode()).hexdigest()[:8]
        self.append({"event": "create", "id": nid, "parent": parent, "name": name, "definition": nd["definition"].strip(),
                     "includes": nd["includes"].strip(), "excludes": nd["excludes"].strip(), "nearest": nd.get("nearest") or [],
                     "by": by, "why": nd.get("why", ""), "raws": list(raws)})
        return "draft", self.state()[nid]
