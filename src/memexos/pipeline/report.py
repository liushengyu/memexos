"""Review results in plain words.

Each problem is four things: what the wiki says, what the source says, what is wrong, and its status. No
reviewer jargon (verdict codes) reaches people. The same list, numbered, goes back to the writer to fix.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from memexos import i18n

SEVERITY = {"CONTRADICTED": "high", "UNSUPPORTED_C": "high", "UNSUPPORTED": "medium", "PARTIAL": "low", "OVERWRITE": "high",
            "INJECTION": "high", "MISSED_C": "high", "MISSED": "medium", "INTEGRATION": "medium"}
ORDER = {"high": 0, "medium": 1, "low": 2}


def load(d: Path) -> tuple[dict | None, dict]:
    try:
        review = json.loads((d / "review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        review = None
    try:
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    return review, report


def raws_of(d: Path) -> list[str]:
    _, rep = load(d)
    if rep.get("raws"):
        return list(rep["raws"])
    try:
        prompt = (d / "prompt.md").read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r"`(raw/[^`]+)`", prompt.split("BASE", 1)[-1].split("wiki", 1)[0])


def problems(d: Path) -> list[dict]:
    r, _ = load(d)
    if not r:
        return []
    out = []
    for c in r["claims"]:
        v = c["verdict"]
        if v in ("SUPPORTED", "MARKED"):
            continue
        key = "UNSUPPORTED_C" if v == "UNSUPPORTED" and c["critical"] else v
        out.append({"kind": key, "page": c["page"], "wiki": c["claim"], "source": c["evidence"], "where": c["raw_location"], "why": c["reason"]})
    for o in r["overwrites"]:
        if o["substantive"] and not (o["backed_by_source"] and o["disclosed"]):
            out.append({"kind": "OVERWRITE", "page": o["page"], "wiki": i18n.t("report.was_now", old=o["old_text"], new=o["new_text"]),
                        "source": "", "where": "", "why": o["reason"]})
    for x in r["injections"]:
        if x["complied"]:
            out.append({"kind": "INJECTION", "page": x["where"], "wiki": "", "source": x["raw_text"], "where": "", "why": x["reason"]})
    for k in r["key_points"]:
        if not k["covered"] and k["importance"] in ("critical", "must"):
            out.append({"kind": "MISSED_C" if k["importance"] == "critical" else "MISSED", "page": k.get("where", ""), "wiki": "",
                        "source": k["point"], "where": "", "why": i18n.t("report.not_expressed")})
    for n in r["integration_notes"]:
        out.append({"kind": "INTEGRATION", "page": n, "wiki": "", "source": "", "where": "", "why": n})
    out.sort(key=lambda p: ORDER[SEVERITY[p["kind"]]])
    return out


def _title(vault: Path, rel: str) -> str:
    try:
        t = (vault / rel).read_text(encoding="utf-8", errors="replace")[:4000]
        m = re.search(r"^(?:title|topic):\s*\"?(.+?)\"?$", t, re.M)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return re.sub(r"^\d{4}-\d{2}-\d{2}-[a-z]+-", "", Path(rel).stem)


def render(vault: Path, d: Path, status: str) -> str:
    """One review as markdown for inbox/<date>-review.md."""
    raws = raws_of(d)
    title = ", ".join(f"“{_title(vault, p)}”" for p in raws) or d.name
    review, rep = load(d)
    if review is None:
        return f"### {title}\n\n{i18n.t('report.incomplete')}\n\n"
    ps = problems(d)
    n = len(review["claims"])
    ok = sum(c["verdict"] == "SUPPORTED" for c in review["claims"])
    marked = sum(c["verdict"] == "MARKED" for c in review["claims"])
    sev = {s: sum(SEVERITY[p["kind"]] == s for p in ps) for s in ("high", "medium", "low")}
    head = i18n.t("report.clean") if not ps else i18n.t("report.found", n=len(ps), high=sev["high"], medium=sev["medium"], low=sev["low"])
    out = [f"### {title}\n", i18n.t("report.summary", head=head, n=n, ok=ok, marked=marked) + "\n"]
    m = rep.get("metrics") or {}
    if m.get("same_family_as_writer"):
        out.append(i18n.t("report.same_family", model=m.get("model") or m.get("backend")) + "\n")
    if ps:
        out.append(i18n.t("report.status", status=status) + "\n")
    for i, p in enumerate(ps, 1):
        out.append(f"**{i}. {i18n.t('report.kind.' + p['kind'])}** ({i18n.t('report.sev.' + SEVERITY[p['kind']])}) · {p['page'] or '-'}\n")
        if p["wiki"]:
            out.append(i18n.t("report.wiki_says", text=p["wiki"]))
        if p["source"]:
            out.append(i18n.t("report.source_says", text=p["source"]) +
                       (f" ({os.path.basename(re.split(r'[:：]', p['where'])[0])})" if p["where"] else ""))
        out.append(i18n.t("report.problem", text=p["why"]) + "\n")
    return "\n".join(out) + "\n"


def for_writer(dirs: list[Path], live: bool = False) -> tuple[int, str]:
    """Numbered problem list for the writer's fix prompt. `live` leaves out problems a later review already checked."""
    lines, n = [], 0
    if live:
        from memexos.pipeline import fixqueue
    for d in dirs:
        for p in (fixqueue.live(d) if live else problems(d)):
            n += 1
            lines.append(i18n.t("report.item", n=n, kind=i18n.t("report.kind." + p["kind"]), sev=i18n.t("report.sev." + SEVERITY[p["kind"]]),
                                page=p["page"], wiki=p["wiki"] or "-",
                                source=(p["source"] or "-") + (f" ({p['where']})" if p["where"] else ""), why=p["why"]))
    return n, "\n".join(lines)
