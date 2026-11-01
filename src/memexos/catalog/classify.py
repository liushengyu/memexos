"""Classification runs, the draft lifecycle, syncing into source pages, and the `memex catalog` commands."""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from memexos import backends, events, i18n, judge, rawref
from memexos.catalog import dimensions as D
from memexos.catalog.taxonomy import ACTIVE, DRAFT, Taxonomy
from memexos.util import append_jsonl, split_frontmatter

BATCH = 20
EXTERNAL = ("video", "tweet", "web", "podcast")
OWN = ("content_type", "level", "evidence", "durability", "usage")     # fields a writer may correct after reading the full text


def catalog_path(cfg) -> Path:
    return cfg.paths.ledger / "catalog.jsonl"


def load(cfg) -> dict[str, dict]:
    cat = {}
    p = catalog_path(cfg)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                cat[r["raw"]] = r
            except (ValueError, KeyError):
                pass
    return cat


def history(cfg) -> dict[str, list[dict]]:
    h = collections.defaultdict(list)
    p = catalog_path(cfg)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                h[r["raw"]].append(r)
            except (ValueError, KeyError):
                pass
    return h


def kind_of(rel: str) -> str:
    m = re.match(r"raw/\d{4}-\d{2}-\d{2}-([a-z]+)-", rel)
    return m.group(1) if m else "other"


def companion_parent(cfg, rel: str) -> str | None:
    try:
        fm, _ = split_frontmatter((cfg.vault / rel).read_text(encoding="utf-8", errors="ignore")[:4000])
    except OSError:
        return None
    return f"raw/{fm['companion_to']}" if fm.get("visual_source") and fm.get("companion_to") else None


def companion_child(cfg, rel: str) -> Path | None:
    base = Path(rel).name
    for p in cfg.paths.raw.glob("*.md"):
        head = p.read_text(encoding="utf-8", errors="ignore")[:3000]
        if "visual_source:" in head and re.search(r"^companion_to:\s*\"?" + re.escape(base) + r"\"?\s*$", head, re.M):
            return p
    return None


def all_raws(cfg) -> list[str]:
    return [p.relative_to(cfg.vault).as_posix() for p in sorted(cfg.paths.raw.glob("*"))
            if p.is_file() and not p.name.startswith(".") and p.suffix in (".md", ".markdown", ".txt", ".html", ".htm")]


def _prose(line: str) -> int:
    line = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"https?://\S+", "", line).strip(" *#-|>\t")
    return len(line) if len(line) >= 30 else 0


def skip_nav(body: str, window: int = 25) -> tuple[int, str]:
    """Web captures often start with long navigation; find where running text begins (the first 25-line window with
    at least 40% of the densest window's text), backing up to the nearest heading."""
    lines = body.splitlines()
    if len(lines) <= window:
        return 0, body
    score = [_prose(l) for l in lines]
    run = sum(score[:window])
    runs = [run]
    for i in range(1, len(lines) - window + 1):
        run += score[i + window - 1] - score[i - 1]
        runs.append(run)
    best = max(runs)
    i = next(i for i, r in enumerate(runs) if r >= best * 0.4)
    for j in range(i, max(i - 12, -1), -1):
        if lines[j].lstrip().startswith("#"):
            i = j
            break
    return i, "\n".join(lines[i:])


def digest(cfg, rel: str, n: int = 2500) -> str:
    text = (cfg.vault / rel).read_text(encoding="utf-8", errors="ignore").replace("\x00", "")
    fm, body = split_frontmatter(text)
    body = re.sub(r"(?m)^>\s*⚠️.*\n?", "", body)
    if rel.endswith((".html", ".htm")):
        body = re.sub(r"<[^>]+>", " ", body)
    title = fm.get("title") or fm.get("topic") or (re.search(r"^#\s+(.+)$", body, re.M) or [None, Path(rel).name])[1]
    head = [f"title: {title}", f"type: {fm.get('source_type') or kind_of(rel)}"]
    for k in ("author", "uploader", "platform", "url", "duration", "published", "date", "fetched_via", "coverage"):
        if fm.get(k):
            head.append(f"{k}: {fm[k]}")
    child = companion_child(cfg, rel) if kind_of(rel) == "video" else None
    extra = ""
    if child:
        ct = child.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"\n## [^\n]*\n\n(.*?)\n## ", ct, re.S)
        extra = f"\nvisual companion overview (model description): {(m.group(1) if m else '')[:1200]}"
    total = len(re.sub(r"\s", "", body))
    squeeze = lambda t: re.sub(r"\n{3,}", "\n\n", t).strip()  # noqa: E731
    skipped, main = skip_nav(body) if kind_of(rel) == "web" else (0, body)
    if skipped >= 5:
        return ("\n".join(head) + extra + f"\nfull text about {total} characters (two samples below, not the full text).\n"
                f"start of the capture:\n{squeeze(body)[:500]}\n…\nfrom line {skipped}, apparently the body:\n{squeeze(main)[:n - 500]}")
    return "\n".join(head) + extra + f"\nfull text about {total} characters (only a sample of the start below):\n{squeeze(body)[:n]}"


# ---------------------------------------------------------------- one-line views
def fmt(cfg, tx: Taxonomy, r: dict, raw_domain: bool = False, st=None, off=None) -> str:
    lang = i18n.language()
    sep = "、" if lang == "zh" else ", "
    if raw_domain:
        prim, sec = r["domain"], [d for d in r.get("secondary") or [] if d != r["domain"]][:2]
    else:
        prim = tx.label(r["domain"], st, off)
        sec = [tx.label(d, st, off) for d in r.get("secondary") or [] if tx.resolve(d, st, off)[0] in ("official", "draft")]
        sec = [d for d in dict.fromkeys(sec) if d != prim][:2]
    note = i18n.t("catalog.scope", note=r["scope_note"]) if r.get("scope_note") else ""
    return i18n.t("catalog.line", domain=prim + (i18n.t("catalog.secondary", list=sep.join(sec)) if sec else ""),
                  type=D.label("content_type", r.get("content_type"), lang), level=D.label("level", r.get("level"), lang),
                  evidence=D.label("evidence", r.get("evidence"), lang), usage=sep.join(D.label("usage", r.get("usage") or [], lang)),
                  durability=D.label("durability", r.get("durability"), lang)) + note


def ensure(cfg, raws: list[str]) -> int:
    """Classify the given raw files if they are not yet (called before compiling them)."""
    cat = load(cfg)
    todo_ = [r for r in raws if r not in cat and not companion_parent(cfg, r) and (cfg.vault / r).exists()]
    return classify(cfg, todo_) if todo_ else 0


def lines(cfg, raws: list[str]) -> str:
    """The batch's classifications for the compile prompt."""
    tx, cat = Taxonomy(cfg), load(cfg)
    st, off = tx.state(), tx.official()
    out = []
    for r in raws:
        e = cat.get(companion_parent(cfg, r) or r)
        out.append(f"- {r}: {fmt(cfg, tx, e, st=st, off=off) if e else i18n.t('pipeline.unclassified')}")
    return "\n".join(out)


def view(cfg, raw: str | None = None, page: str | None = None) -> str:
    """One line for search results: the catalog's classification, with fields the writer corrected on the source page."""
    names = rawref.index(cfg.vault)
    page_fm = None
    if page:
        if not page.startswith("wiki/sources/"):
            return ""
        text = (cfg.vault / page).read_text(encoding="utf-8", errors="ignore")
        raw = primary_raw(text, names, cfg.vault)
        page_fm = split_frontmatter(text)[0]
    if not raw:
        return ""
    e = effective(cfg, raw, page_fm=page_fm)
    if not e:
        return ""
    lang = i18n.language()
    s = i18n.t("search.classification", domain=e["domain"], type=D.label("content_type", e["content_type"], lang),
               evidence=D.label("evidence", e["evidence"], lang))
    if e["provisional"]:
        s += i18n.t("catalog.provisional_note")
    return s + (i18n.t("search.note", note=str(e["scope_note"])[:80]) if e.get("scope_note") else "")


# ---------------------------------------------------------------- the big-model classification
def schema(lang: str) -> dict:
    s = lambda vals: {"type": "string", "enum": vals}  # noqa: E731
    txt = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": ["items", "new_drafts"], "properties": {
        "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                  "required": ["id", "domain", "secondary", "content_type", "level", "evidence", "usage", "durability", "scope_note", "summary"],
                  "properties": {"id": {"type": "integer"}, "domain": txt, "secondary": {"type": "array", "items": txt},
                                 "content_type": s(D.labels("content_type", lang)), "level": s(D.labels("level", lang)),
                                 "evidence": s(D.labels("evidence", lang)), "usage": {"type": "array", "items": s(D.labels("usage", lang))},
                                 "durability": s(D.labels("durability", lang)), "scope_note": txt, "summary": txt}}},
        "new_drafts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                       "required": ["name", "definition", "includes", "excludes", "nearest", "why", "ids", "fallback"],
                       "properties": {"name": txt, "definition": txt, "includes": txt, "excludes": txt,
                                      "nearest": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                             "required": ["node", "diff"], "properties": {"node": txt, "diff": txt}}},
                                      "why": txt, "ids": {"type": "array", "items": {"type": "integer"}}, "fallback": txt}}}}}


def drafts_section(cfg, tx: Taxonomy, cat: dict) -> str:
    st, off = tx.state(), tx.official()
    act = tx.active(st)
    lst = "\n".join(f"- `{DRAFT}{tx.key(n)}`: {n['definition']} {i18n.t('catalog.includes')}: {n['includes']}. "
                    f"{i18n.t('catalog.excludes')}: {n['excludes']}." +
                    (" " + i18n.t("catalog.examples", list="; ".join(cat[r].get("summary", "") for r in tx.members(n, cat, st, off)[:4]))
                     if tx.members(n, cat, st, off) else "") for n in act) or i18n.t("catalog.no_drafts")
    per = collections.Counter(n["parent"] for n in act)
    mx = tx.th("max_drafts_per_top", 5)
    full = [t for t in tx.tops() if per[t] >= mx]
    rej = [n for n in st.values() if n["status"] == "rejected"]
    return i18n.prompt("classify_drafts", list=lst, max=mx, full=i18n.t("catalog.full_tops", list=", ".join(full)) if full else "",
                       rejected=i18n.t("catalog.rejected_names", list=", ".join(f"`{tx.key(n)}`" for n in rej)) if rej else "")


def precedents(cfg, rels: list[str], cat: dict, tx: Taxonomy) -> dict[str, str]:
    """Earlier classifications of the same URL or author, so the same material is classified consistently across batches."""
    by_url, by_who = collections.defaultdict(list), collections.defaultdict(list)
    for raw, r in cat.items():
        try:
            fm, _ = split_frontmatter((cfg.vault / raw).read_text(encoding="utf-8", errors="ignore")[:3000])
        except OSError:
            continue
        if fm.get("url"):
            by_url[str(fm["url"]).rstrip("/")].append(r)
        who = fm.get("author") or fm.get("uploader")
        if who:
            by_who[str(who)].append(r)
    out = {}
    for rel in rels:
        try:
            fm, _ = split_frontmatter((cfg.vault / rel).read_text(encoding="utf-8", errors="ignore")[:3000])
        except OSError:
            continue
        same = [r for r in by_url.get(str(fm.get("url") or "").rstrip("/"), []) if r["raw"] != rel][-2:]
        who = fm.get("author") or fm.get("uploader")
        peers = [r for r in by_who.get(str(who), []) if r["raw"] != rel][-3:] if who else []
        ls = [i18n.t("catalog.prec_url", name=Path(r["raw"]).name, line=fmt(cfg, tx, r, raw_domain=True)) for r in same]
        ls += [i18n.t("catalog.prec_author", who=who, name=Path(r["raw"]).name, line=fmt(cfg, tx, r, raw_domain=True)) for r in peers]
        if ls:
            out[rel] = i18n.t("catalog.prec_head") + "\n" + "\n".join(ls)
    return out


def hint(s: dict | None) -> str:
    if s and s["adopt"]:
        return i18n.t("catalog.hint_decided", top=s["top"])
    if not s:
        return i18n.t("catalog.hint_open")
    if not s["top"]:
        return i18n.t("catalog.hint_none")
    return i18n.t("catalog.hint_close", alts=", ".join(f"`{k}`" for k, _ in s["alts"]) or f"`{s['top']}`")


def run_batch(cfg, tx: Taxonomy, rels: list[str], scr: dict, cat: dict, role: str = "worker"):
    prev = precedents(cfg, rels, cat, tx)
    items = "\n\n".join(f"### item {i} ({rel})\n{hint(scr.get(rel))}\n{digest(cfg, rel)}" + (f"\n{prev[rel]}" if rel in prev else "")
                        for i, rel in enumerate(rels, 1))
    prompt = tx.md.read_text(encoding="utf-8") + i18n.prompt("classify_task", drafts=drafts_section(cfg, tx, cat), n=len(rels)) + items
    with tempfile.TemporaryDirectory(prefix="memex-classify-") as d:
        r = backends.structured(cfg, role, prompt, schema(i18n.language()), cwd=d, workdir=d, effort="high", timeout=1800, job="classify")
    if not r.ok:
        raise RuntimeError(f"{r.backend}: {r.tail[-200:]}")
    rows = []
    for it in r.result["items"]:
        if not 1 <= it["id"] <= len(rels):
            continue
        rows.append({"raw": rels[it["id"] - 1], "model_domain": it["domain"].strip(), "secondary": it["secondary"],
                     "content_type": D.to_id("content_type", it["content_type"]), "level": D.to_id("level", it["level"]),
                     "evidence": D.to_id("evidence", it["evidence"]), "usage": list(dict.fromkeys(D.to_id("usage", it["usage"]))),
                     "durability": D.to_id("durability", it["durability"]), "scope_note": it["scope_note"], "summary": it["summary"],
                     "taxonomy": tx.version(), "model": r.model or r.backend, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return rows, r.result.get("new_drafts") or [], r.model or r.backend


def settle(tx: Taxonomy, rows, nds, scr, rels, model) -> tuple[list[dict], list[str]]:
    """Final primary domain per row: the screen's when it was confident; else the model's, with any new draft passing
    the registry's checks first, falling back to the model's fallback, then the screen's closest guess."""
    made, fb, notes = {}, {}, []
    summ = {r["raw"]: r["summary"] for r in rows}
    for nd in nds:
        name = nd["name"].strip().removeprefix(DRAFT)
        parent, _, sub = name.partition("/")
        mine = [rels[i - 1] for i in sorted(set(nd["ids"])) if 1 <= i <= len(rels)]
        esc = [x for x in mine if not (scr.get(x) or {}).get("adopt")]
        fb[name] = nd.get("fallback") or ""
        if not esc:
            made[name] = None
            continue
        kind, x = tx.propose({"parent": parent, "name": sub, **{k: nd[k] for k in ("definition", "includes", "excludes", "nearest", "why")}},
                             examples=[summ[r] for r in esc if r in summ], by=model, raws=esc)
        if kind == "draft":
            made[name] = DRAFT + tx.key(x)
            notes.append(i18n.t("catalog.note_draft", name=tx.key(x)))
        elif kind == "official":
            made[name] = x
        else:
            made[name] = None
            notes.append(i18n.t("catalog.note_refused", name=name, why=x))
    st, off = tx.state(), tx.official()
    out = []
    for row in rows:
        s = scr.get(row["raw"])
        md = row.pop("model_domain")
        if s and s["adopt"]:
            dom, by = s["top"], "judge"
            if tx.canon(md, st, off) != dom:
                row["model_domain"] = md          # disagreement between screen and model: a running evaluation signal
        else:
            by, dom = model, tx.canon(md, st, off)
            plain = md.removeprefix(DRAFT)
            if dom is None and plain in made:
                dom = made[plain] or tx.canon(fb.get(plain), st, off)
            if dom is None and s and s["top"]:
                dom = s["top"]
            if dom is None:
                dom = DRAFT + plain                 # unknown: the next draft check re-homes it
        sec = [v for v in (tx.canon(x, st, off) for x in row["secondary"]) if v and v != dom]
        row = {"raw": row["raw"], "domain": dom, **{k: v for k, v in row.items() if k not in ("raw", "secondary")},
               "secondary": list(dict.fromkeys(sec))[:2], "domain_by": by}
        if s:
            row["screen"] = {k: s[k] for k in ("top", "p", "p_none", "scope")}
        out.append(row)
    return out, notes


def classify(cfg, rels: list[str], role: str = "worker", why: str = "") -> int:
    tx = Taxonomy(cfg)
    done = 0
    for i in range(0, len(rels), BATCH):
        part = rels[i:i + BATCH]
        t0 = time.time()
        cat = load(cfg)
        try:
            try:
                scr = tx.screen({r: digest(cfg, r) for r in part})
            except judge.JudgeUnavailable:
                scr = {}
            rows, nds, model = run_batch(cfg, tx, part, scr, cat, role)
            rows, notes = settle(tx, rows, nds, scr, part, model)
            for r in rows:
                if why:
                    r["redo"] = why
        except Exception as e:  # noqa: BLE001 — a failed batch is retried next time
            events.emit(cfg.paths.state, "classify.failed", batch=i // BATCH + 1, why=str(e)[-200:])
            continue
        append_jsonl(catalog_path(cfg), rows)
        done += len(rows)
        events.emit(cfg.paths.state, "classify.batch", done=done, total=len(rels), seconds=round(time.time() - t0),
                    by_judge=sum(r["domain_by"] == "judge" for r in rows), notes=notes)
    return done


def todo(cfg, rels: list[str], cat: dict) -> list[str]:
    out = [r for r in rels if r not in cat and not companion_parent(cfg, r) and (cfg.vault / r).exists()]
    return sorted(out, key=lambda r: (kind_of(r) not in EXTERNAL, r))


# ---------------------------------------------------------------- domains only (re-checks, re-homing)
def decide_domains(cfg, tx: Taxonomy, rels: list[str], allowed: list[str], note: str = "") -> dict[str, str]:
    st, off = tx.state(), tx.official()
    desc = {k: off[k] for k in allowed if k in off}
    for n in tx.active(st):
        if DRAFT + tx.key(n) in allowed:
            desc[DRAFT + tx.key(n)] = n["definition"] + " " + i18n.t("catalog.draft_tag")
    sch = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "domain", "why"],
        "properties": {"id": {"type": "integer"}, "domain": {"type": "string", "enum": allowed}, "why": {"type": "string"}}}}}}
    out = {}
    for i in range(0, len(rels), BATCH):
        part = rels[i:i + BATCH]
        items = "\n\n".join(f"### item {j} ({rel})\n{digest(cfg, rel)}" for j, rel in enumerate(part, 1))
        prompt = tx.md.read_text(encoding="utf-8") + i18n.prompt("classify_domains", note=note, n=len(part),
                                                                 listing="\n".join(f"- `{k}`: {desc.get(k, '')}" for k in allowed), items=items)
        with tempfile.TemporaryDirectory(prefix="memex-classify-") as d:
            r = backends.structured(cfg, "worker", prompt, sch, cwd=d, workdir=d, effort="high", timeout=1800, job="classify")
        for it in (r.result or {}).get("items", []) if r.ok else []:
            if 1 <= it["id"] <= len(part) and it["domain"] in allowed:
                out[part[it["id"] - 1]] = tx.canon(it["domain"], st, off) or it["domain"]
    return out


def reassign(cfg, tx: Taxonomy, raws: list[str], note: str, drafts: bool = False) -> dict[str, str]:
    if not raws:
        return {}
    st, off = tx.state(), tx.official()
    try:
        scr = tx.screen({r: digest(cfg, r) for r in raws}, st, off, drafts=drafts)
    except judge.JudgeUnavailable:
        scr = {}
    out = {r: s["top"] for r, s in scr.items() if s and s["adopt"]}
    rest = [r for r in raws if r not in out]
    if rest:
        allowed = list(off) + ([DRAFT + tx.key(n) for n in tx.active(st)] if drafts else [])
        out.update(decide_domains(cfg, tx, rest, allowed, note))
    return out


def move(cfg, tx: Taxonomy, changes: dict[str, str], why: str, by: str) -> int:
    cat = load(cfg)
    rows = []
    for raw, dom in changes.items():
        r = dict(cat.get(raw) or {})
        if not r or not dom or r.get("domain") == dom:
            continue
        r.pop("screen", None)
        r.pop("model_domain", None)
        r.update(domain=dom, secondary=[s for s in r.get("secondary") or [] if s != dom], redo=why, domain_by=by, taxonomy=tx.version(),
                 ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        rows.append(r)
    append_jsonl(catalog_path(cfg), rows)
    return len(rows)


def recheck(cfg, tx: Taxonomy, n: dict) -> float:
    """Classify a draft's members again without telling where they are now; the share that lands back in it."""
    cat = load(cfg)
    mem = tx.members(n, cat)
    me = DRAFT + tx.key(n)
    res = reassign(cfg, tx, mem, i18n.t("catalog.recheck_note"), drafts=True)
    stayed = [r for r in mem if res.get(r) == me]
    rate = round(len(stayed) / len(mem), 3) if mem else 0.0
    tx.append({"event": "recheck", "id": n["id"], "members": mem, "stayed": stayed,
               "moved": {r: res.get(r, "") for r in mem if res.get(r) != me}, "rate": rate})
    return rate


def check(cfg) -> list[str]:
    """Draft lifecycle: re-home unknown domains, expire idle drafts, promote ripe drafts to pending after a re-check."""
    tx = Taxonomy(cfg)
    notes = []
    st, off = tx.state(), tx.official()
    cat = load(cfg)
    bad = [raw for raw, r in cat.items() if tx.resolve(r.get("domain"), st, off)[0] in (None, "gone") and (cfg.vault / raw).exists()]
    if bad:
        k = move(cfg, tx, reassign(cfg, tx, bad, i18n.t("catalog.rehome_note"), drafts=True), "re-homed by the draft check", "check")
        notes.append(i18n.t("catalog.rehomed", n=k, total=len(bad)))
    st, cat = tx.state(), load(cfg)
    today = datetime.date.today()
    for n in [n for n in tx.active(st) if n["status"] == "draft"]:
        prog = tx.progress(n, cat, st, off)
        idle = (today - datetime.date.fromisoformat(prog["last"][:10])).days
        if idle > tx.th("expire_days", 60):
            res = reassign(cfg, tx, prog["members"], i18n.t("catalog.expire_note", name=tx.key(n), days=idle), drafts=False)
            move(cfg, tx, res, f"draft {tx.key(n)} expired", "check")
            tx.append({"event": "expired", "id": n["id"], "last": prog["last"], "moved": {r: res.get(r, "") for r in prog["members"]}})
            notes.append(i18n.t("catalog.expired", name=tx.key(n), days=idle))
            continue
        if prog["works"] < tx.th("min_works", 8) or prog["days"] < tx.th("min_days", 3):
            continue
        rc = n.get("recheck")
        rate = rc["rate"] if rc and set(rc.get("members") or []) == set(prog["members"]) else recheck(cfg, tx, n)
        if rate >= tx.th("min_stable", 0.8):
            tx.append({"event": "pending", "id": n["id"], "works": prog["works"], "days": prog["days"], "rate": rate, "members": prog["members"]})
            notes.append(i18n.t("catalog.pending", name=tx.key(n), works=prog["works"], days=prog["days"], rate=f"{rate:.0%}"))
    for x in notes:
        events.emit(cfg.paths.state, "catalog.check", note=x)
    return notes


def retarget_secondary(cfg, tx: Taxonomy, n: dict, target: str | None) -> int:
    names = {f"{n['parent']}/{a}" for a in [n["name"]] + n["aliases"]}
    rows = []
    for raw, r in load(cfg).items():
        sec = r.get("secondary") or []
        if not any(s.removeprefix(DRAFT) in names for s in sec):
            continue
        new = [s for s in dict.fromkeys(target if s.removeprefix(DRAFT) in names else s for s in sec) if s and s != r["domain"]][:2]
        rows.append({**r, "secondary": new, "taxonomy": tx.version(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    append_jsonl(catalog_path(cfg), rows)
    return len(rows)


def interpret(cfg, tx: Taxonomy, n: dict, text: str) -> dict:
    sch = {"type": "object", "additionalProperties": False, "required": ["action", "name", "into", "why"], "properties": {
        "action": {"type": "string", "enum": ["approve", "merge", "reject", "unclear"]}, "name": {"type": "string"},
        "into": {"type": "string"}, "why": {"type": "string"}}}
    prompt = i18n.prompt("classify_interpret", key=tx.key(n), definition=n["definition"], text=text, official=", ".join(tx.official()))
    with tempfile.TemporaryDirectory(prefix="memex-classify-") as d:
        r = backends.structured(cfg, "worker", prompt, sch, cwd=d, workdir=d, effort="medium", timeout=600, job="classify")
    return r.result if r.ok else {"action": "unclear", "name": "", "into": "", "why": "no answer from the model"}


def decide(cfg, ident: str, action: str, name: str = "", into: str = "", why: str = "") -> tuple[str, list[str]]:
    """Apply the person's decision on a pending (or draft) category. Returns (one-line result, files to commit)."""
    tx = Taxonomy(cfg)
    st, off = tx.state(), tx.official()
    n = tx.find(ident, st)
    if not n:
        raise RuntimeError(i18n.t("catalog.no_such", ident=ident))
    if n["status"] not in ACTIVE:
        raise RuntimeError(i18n.t("catalog.closed", ident=tx.key(n)))
    mem = tx.members(n, load(cfg), st, off)
    changed = {".memex/taxonomy-nodes.jsonl", ".memex/catalog.jsonl"}
    if action == "approve":
        nm = re.sub(r"\s+", " ", (name or n["name"]).strip().split("/")[-1])
        k = f"{n['parent']}/{nm}"
        if k in off:
            raise RuntimeError(i18n.t("catalog.exists", key=k))
        desc = n["includes"] + (f" ({i18n.t('catalog.excludes')}: {n['excludes']})" if n["excludes"] else "")
        ver = tx.add_official(n["parent"], nm, desc, i18n.t("catalog.approved_note", id=n["id"]) + (f" {why}" if why else ""))
        tx.append({"event": "approved", "id": n["id"], "name": nm, "official": k, "version": ver, "why": why, "members": mem})
        moved = move(cfg, tx, {r: k for r in mem}, f"category {k} approved", "person")
        retarget_secondary(cfg, tx, n, k)
        changed.add(".memex/taxonomy.md")
        return i18n.t("catalog.did_approve", key=k, version=ver, n=moved), sorted(changed)
    if action == "merge":
        if into not in off:
            raise RuntimeError(i18n.t("catalog.not_official", key=into))
        tx.append({"event": "merged", "id": n["id"], "into": into, "why": why, "members": mem})
        moved = move(cfg, tx, {r: into for r in mem}, f"draft {tx.key(n)} merged into {into}", "person")
        retarget_secondary(cfg, tx, n, into)
        return i18n.t("catalog.did_merge", key=tx.key(n), into=into, n=moved), sorted(changed)
    if action == "reject":
        res = reassign(cfg, tx, mem, i18n.t("catalog.reject_note", name=tx.key(n)), drafts=False)
        tx.append({"event": "rejected", "id": n["id"], "why": why, "moved": {r: res.get(r, "") for r in mem}})
        moved = move(cfg, tx, res, f"draft {tx.key(n)} rejected", "person")
        retarget_secondary(cfg, tx, n, None)
        return i18n.t("catalog.did_reject", key=tx.key(n), n=moved), sorted(changed)
    raise RuntimeError(f"unknown action: {action}")


# ---------------------------------------------------------------- source pages and the index
def primary_raw(page_text: str, names: set[str], vault: Path) -> str | None:
    m = re.search(r"(?ms)^#{2,3}\s*(?:Sources|来源)\b.*?(?=^## |\Z)", page_text)
    sec = m.group(0) if m else ""
    for line in sec.splitlines():
        if re.match(r"^\s*[-*]\s*`?(?:\S*/)?raw/", line):
            refs = rawref.find(line, names, vault)
            if refs:
                return refs[0]
    refs = rawref.find(sec, names, vault) or rawref.find(page_text, names, vault)
    return refs[0] if refs else None


def _norm(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def writer_set(tx: Taxonomy, hist: dict, raw: str, k: str, v) -> bool:
    """Did the writer set this field on the page (a value the catalog never gave)? Then sync must not overwrite it."""
    if v in (None, "", []):
        return False
    rows = hist.get(raw, [])
    if k == "domain":
        v = v[0] if isinstance(v, list) else v
        seen = set()
        for r in rows:
            seen |= {r["domain"], r["domain"].removeprefix(DRAFT), tx.label(r["domain"])}
        return v in tx.official() and v not in seen
    vid = D.to_id(k, v)
    return _norm(vid) not in {_norm(r.get(k)) for r in rows}


def effective(cfg, raw: str, cat=None, hist=None, page_fm=None) -> dict | None:
    cat = cat if cat is not None else load(cfg)
    key = companion_parent(cfg, raw) or raw
    r = cat.get(key)
    if not r:
        return None
    tx = Taxonomy(cfg)
    st, off = tx.state(), tx.official()
    prim = tx.label(r["domain"], st, off)
    sec = [tx.label(d, st, off) for d in r.get("secondary") or [] if tx.resolve(d, st, off)[0] in ("official", "draft")]
    out = {"domain": prim, "secondary": [d for d in dict.fromkeys(sec) if d != prim][:2],
           "provisional": tx.resolve(r["domain"], st, off)[0] != "official", **{k: r.get(k) for k in OWN},
           "scope_note": r.get("scope_note", ""), "writer": []}
    if page_fm:
        hist = hist if hist is not None else history(cfg)
        pd = page_fm.get("domain")
        if writer_set(tx, hist, key, "domain", pd):
            out.update(domain=pd[0] if isinstance(pd, list) else pd, secondary=pd[1:] if isinstance(pd, list) else [], provisional=False)
            out["writer"].append("domain")
        for k in OWN:
            if writer_set(tx, hist, key, k, page_fm.get(k)):
                out[k] = D.to_id(k, page_fm[k]) or page_fm[k]
                out["writer"].append(k)
        un = str(page_fm.get("use_note") or "").strip()
        if un and un not in {(x.get("scope_note") or "")[:300] for x in hist.get(key, [])}:
            out["scope_note"] = un
    return out


def set_frontmatter(text: str, fields: dict) -> str:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    kept = [l for l in (m.group(1).splitlines() if m else []) if l.split(":", 1)[0].strip() not in fields]
    new = kept + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items()]
    return "---\n" + "\n".join(new) + "\n---\n" + (text[m.end():] if m else text)


def sync(cfg, stage: Path) -> tuple[int, int]:
    """Catalog → source page frontmatter (fields the writer corrected are kept) → index Sources grouped by top domain."""
    lang = i18n.language()
    tx = Taxonomy(cfg)
    cat, hist = load(cfg), history(cfg)
    names = rawref.index(stage)
    page_dom, changed = {}, 0
    for p in sorted((stage / "wiki" / "sources").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        prim = primary_raw(text, names, stage)
        if not prim:
            continue
        key = companion_parent(cfg, prim) or prim
        r = cat.get(key)
        if not r:
            continue
        fm, _ = split_frontmatter(text)
        e = effective(cfg, prim, cat, hist, fm)
        fields = {}
        if "domain" not in e["writer"]:
            doms = [e["domain"]] + e["secondary"]
            fields["domain"] = doms if len(doms) > 1 else doms[0]
        for k in OWN:
            if k not in e["writer"]:
                fields[k] = D.label(k, r.get(k), lang)
        old = str(fm.get("use_note") or "").strip()
        ever = {(x.get("scope_note") or "")[:300] for x in hist.get(key, [])}
        if r.get("scope_note") and (not old or old in ever):
            fields["use_note"] = r["scope_note"][:300]
        new = set_frontmatter(text, fields)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
        page_dom[p.stem] = e["domain"]
    regroup_index(tx, stage / "index.md", page_dom)
    log = stage / "log.md"
    add = schema_log_entries(tx, log.read_text(encoding="utf-8") if log.exists() else "")
    if add:
        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(add))
    return changed, len(page_dom)


def regroup_index(tx: Taxonomy, path: Path, page_dom: dict[str, str]) -> None:
    """Group the index's Sources section by top-level domain (drafts under their top level; unclassified last).
    Entries are not rewritten, only ordered under subheadings."""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^## Sources[^\n]*\n(.*?)(?=^## |\Z)", text)
    if not m:
        return
    pre, entries, post = [], [], []
    for line in m.group(1).splitlines():
        if line.startswith("- "):
            entries.append(line)
        elif line.startswith("### ") or line.startswith(i18n.t("catalog.index_note_prefix")):
            continue
        elif line.strip():
            (post if entries else pre).append(line)
    if not entries:
        return
    order = tx.tops()
    other = i18n.t("catalog.unclassified_group")
    groups = collections.defaultdict(list)
    for e in entries:
        nm = re.search(r"\[\[([^\]|#]+)", e)
        dom = page_dom.get(nm.group(1).strip()) if nm else None
        groups[tx.top(dom) if dom else other].append(e)
    keys = [k for k in order if k in groups] + sorted(k for k in groups if k not in order and k != other) + ([other] if other in groups else [])
    body = "\n".join(pre) + ("\n\n" if pre else "") + i18n.t("catalog.index_note") + "\n"
    for k in keys:
        body += f"\n### {k}\n\n" + "\n".join(groups[k]) + "\n"
    if post:
        body += "\n" + "\n".join(post) + "\n"
    path.write_text(text[:m.start(1)] + "\n" + body.strip("\n") + "\n\n" + text[m.end(1):], encoding="utf-8")


def schema_log_entries(tx: Taxonomy, log_text: str) -> list[str]:
    out = []
    for n in tx.state().values():
        if n["status"] in ACTIVE or n["id"] in log_text:
            continue
        ev = next((e for e in reversed(n["log"]) if e["event"] in ("approved", "merged", "rejected", "expired")), None)
        if ev:
            out.append(f"\n## [{ev['ts'][:10]}] schema | {i18n.t('catalog.log_' + ev['event'], key=tx.key(n), official=ev.get('official', ''), into=ev.get('into', ''), version=ev.get('version', ''))}\n\n- {n['id']}\n")
    return out


def catalog_sync(cfg) -> int:
    """Run sync in a staging worktree, check it with gate 1 and publish (no model involved)."""
    from memexos import lock
    from memexos.pipeline import gate1
    from memexos.pipeline.stage import Stage
    with lock.held(cfg.paths.locks, "write", wait=5400):
        stage = Stage(cfg).open()
        changed, n = sync(cfg, stage.dir)
        with open(stage.dir / "log.md", "a", encoding="utf-8") as f:
            f.write(f"\n## [{datetime.date.today().isoformat()}] lint | {i18n.t('catalog.sync_log', changed=changed, n=n)}\n")
        rep = gate1.check(stage.dir, stage.base, "lint")
        if not rep["passed"]:
            stage.hold(i18n.t("catalog.sync_failed"), [], json.dumps(rep, ensure_ascii=False, indent=1))
            return 1
        stage.publish(f"lint: catalog sync ({changed} source pages)")
        events.emit(cfg.paths.state, "catalog.sync", changed=changed, pages=n)
        print(i18n.t("catalog.sync_log", changed=changed, n=n))
    return 0


# ---------------------------------------------------------------- inbox items for drafts
def inbox_items(cfg) -> list[dict]:
    tx = Taxonomy(cfg)
    st, off = tx.state(), tx.official()
    cat, hist = load(cfg), history(cfg)
    items = []
    for n in st.values():
        if n["status"] == "expired":
            ev = next((e for e in reversed(n["log"]) if e["event"] == "expired"), {})
            items.append({"id": f"taxnode:{n['id']}:expired", "src": "taxonomy", "date": ev.get("ts", "")[:10], "node_id": n["id"],
                          "title": i18n.t("catalog.inbox_expired_title", key=tx.key(n)),
                          "body": "\n".join(f"- `{r}` → {d}" for r, d in (ev.get("moved") or {}).items()),
                          "triage": {"kind": "fyi", "why": i18n.t("catalog.inbox_expired_why"), "question": i18n.t("catalog.inbox_expired_title", key=tx.key(n)),
                                     "context": "", "options": [], "recommend": -1, "recommend_why": "", "pages": []}})
        if n["status"] != "pending":
            continue
        prog = tx.progress(n, cat, st, off)
        mem = prog["members"]
        origin = collections.Counter()
        for r in mem:
            prev = [x["domain"] for x in hist.get(r, [])[:-1] if tx.resolve(x.get("domain"), st, off)[0] == "official"]
            if prev:
                origin[tx.resolve(prev[-1], st, off)[1]] += 1
        near = [x for x in n.get("nearest") or [] if x.get("node") in off][:2]
        merge = [d for d, c in origin.most_common(2) if c >= max(2, 0.2 * len(mem))] or [x["node"] for x in near[:1]]
        rc = n.get("recheck") or {}
        opts = [{"label": i18n.t("catalog.opt_approve"), "effect": i18n.t("catalog.opt_approve_effect", key=tx.key(n), n=len(mem)), "action": "approve"}]
        opts += [{"label": i18n.t("catalog.opt_merge", into=d), "effect": i18n.t("catalog.opt_merge_effect", into=d, n=len(mem)), "action": "merge", "into": d}
                 for d in merge]
        opts.append({"label": i18n.t("catalog.opt_reject"), "effect": i18n.t("catalog.opt_reject_effect"), "action": "reject"})
        body = (f"**{i18n.t('catalog.definition')}**: {n['definition']}\n\n**{i18n.t('catalog.includes')}**: {n['includes']}\n\n"
                f"**{i18n.t('catalog.excludes')}**: {n['excludes']}\n\n" +
                "\n".join(f"- {cat[r].get('summary', '')} (`{r}`)" for r in mem))
        items.append({"id": f"taxnode:{n['id']}", "src": "taxonomy", "date": n["pending"]["ts"][:10], "node_id": n["id"],
                      "title": i18n.t("catalog.inbox_title", key=tx.key(n)), "body": body,
                      "triage": {"kind": "decide", "why": i18n.t("catalog.inbox_why"),
                                 "question": i18n.t("catalog.inbox_question", top=n["parent"], name=n["name"]),
                                 "context": i18n.t("catalog.inbox_context", n=len(mem), works=prog["works"], days=prog["days"],
                                                   rate=f"{rc.get('rate', 0):.0%}"),
                                 "options": opts, "recommend": -1, "recommend_why": "", "pages": []}})
    return items


# ---------------------------------------------------------------- CLI
def cmd_catalog(loader, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex catalog")
    ap.add_argument("cmd", nargs="?", default="stats",
                    choices=["stats", "list", "drafts", "backlog", "ensure", "redo", "get", "check", "sync", "decide"])
    ap.add_argument("raws", nargs="*")
    ap.add_argument("--why", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--domain")
    ap.add_argument("--type")
    ap.add_argument("--evidence")
    ap.add_argument("--usage")
    ap.add_argument("-q")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--name", default="")
    ap.add_argument("--merge", default="")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--text", default="")
    a = ap.parse_args(argv)
    cfg = loader()
    if not (cfg.vault / "raw").is_dir():
        print(i18n.t("cli.no_vault", path=cfg.vault), file=sys.stderr)
        return 1
    if a.cmd in ("stats", "list", "drafts", "get"):          # read-only: usable from read-only sandboxes
        return {"stats": _stats, "list": _list, "drafts": _drafts, "get": _get}[a.cmd](cfg, a)
    events.new_run("classify")
    if a.cmd == "backlog":
        from memexos import lock
        try:
            with lock.held(cfg.paths.locks, "classify"):
                rels = todo(cfg, all_raws(cfg), load(cfg))[: a.limit]
                classify(cfg, rels)
                check(cfg)
        except lock.Busy:
            pass
        return 0
    if a.cmd == "ensure":
        ensure(cfg, a.raws)
        return 0
    if a.cmd == "redo":
        classify(cfg, [r for r in a.raws if (cfg.vault / r).exists() and not companion_parent(cfg, r)], why=a.why)
        return 0
    if a.cmd == "check":
        for x in check(cfg) or [i18n.t("catalog.nothing")]:
            print(x)
        return 0
    if a.cmd == "sync":
        return catalog_sync(cfg)
    if a.cmd == "decide":
        tx = Taxonomy(cfg)
        ident = (a.raws or [""])[0]
        action, name, into, why = ("approve" if a.approve else "merge" if a.merge else "reject" if a.reject else ""), a.name, a.merge, a.why
        if a.text:
            n = tx.find(ident)
            if not n:
                print(i18n.t("catalog.no_such", ident=ident), file=sys.stderr)
                return 1
            it = interpret(cfg, tx, n, a.text)
            if it["action"] == "unclear":
                print(i18n.t("catalog.unclear", why=it["why"]), file=sys.stderr)
                return 2
            action, name, into, why = it["action"], it["name"], it["into"], a.why or a.text
        note, files = decide(cfg, ident, action, name, into, why)
        print(note, file=sys.stderr)
        print("\n".join(files))
    return 0


def _get(cfg, a) -> int:
    print(lines(cfg, a.raws))
    return 0


def _stats(cfg, a) -> int:
    tx, rows = Taxonomy(cfg), list(load(cfg).values())
    st, off = tx.state(), tx.official()
    lang = i18n.language()
    kinds = collections.Counter(tx.resolve(r["domain"], st, off)[0] for r in rows)
    print(i18n.t("catalog.stats_head", n=len(rows), total=len(all_raws(cfg)), drafts=kinds["draft"]))
    top = collections.Counter(tx.top(r["domain"]) for r in rows)
    c = collections.Counter(tx.label(r["domain"], st, off) for r in rows)
    for t, n in top.most_common():
        print(f"{t}  {n}")
        for d, k in sorted(((d, k) for d, k in c.items() if tx.top(d) == t), key=lambda x: -x[1]):
            print(f"    {d.split('/', 1)[-1] if '/' in d else d}  {k}")
    for k in ("content_type", "evidence"):
        print(f"\n{k}: " + "  ".join(f"{D.label(k, v, lang)} {n}" for v, n in collections.Counter(r.get(k) for r in rows).most_common()))
    return 0


def _list(cfg, a) -> int:
    tx = Taxonomy(cfg)
    st, off = tx.state(), tx.official()
    want = (a.domain or "").removeprefix(DRAFT)
    for r in sorted(load(cfg).values(), key=lambda r: (r["domain"], r["raw"])):
        if want:
            forms = {f for d in [r["domain"]] + list(r.get("secondary") or []) for f in (d, d.removeprefix(DRAFT), tx.label(d, st, off))}
            if not any(f.startswith(want) for f in forms):
                continue
        if a.type and r.get("content_type") != D.to_id("content_type", a.type) or a.evidence and r.get("evidence") != D.to_id("evidence", a.evidence) \
                or a.usage and D.to_id("usage", a.usage) not in (r.get("usage") or []):
            continue
        if a.q and a.q.lower() not in (r["raw"] + r.get("summary", "") + r.get("scope_note", "")).lower():
            continue
        print(f"- {r.get('summary', '')}\n  {r['raw']}\n  {fmt(cfg, tx, r, st=st, off=off)}")
    return 0


def _drafts(cfg, a) -> int:
    tx = Taxonomy(cfg)
    st, off = tx.state(), tx.official()
    cat = load(cfg)
    act = tx.active(st)
    if not act:
        print(i18n.t("catalog.no_drafts"))
    for n in sorted(act, key=lambda n: (n["status"] != "pending", n["parent"], n["name"])):
        prog = tx.progress(n, cat, st, off)
        g = tx.gaps(n, prog)
        print(i18n.t("catalog.draft_line", status=i18n.t("catalog.status_" + n["status"]), label=tx.label(DRAFT + tx.key(n), st, off),
                     works=prog["works"], days=prog["days"],
                     next=i18n.t("catalog.waiting_you") if n["status"] == "pending" else ("; ".join(g) or i18n.t("catalog.next_check"))))
        print(f"    {n['definition']}")
        for r in prog["members"]:
            print(f"    - {cat[r].get('summary', '')} ({Path(r).name})")
    return 0
