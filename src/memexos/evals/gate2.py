"""Gate 2 calibration: does the review catch planted errors, and does it stay quiet on a clean compile?

    memex eval gate2 [--case clean|seeded] [--list]

Each case replays one compile from the sample vault in a scratch repository: the base commit holds everything except
the pages that compile wrote, the working tree adds them (plus any planted errors). The configured reviewer then
reviews it exactly as in a real compile. Results go to <state>/evals/gate2/<time>/<case>/ with the full review, so a
person can read every verdict and ask "would I have judged this differently?". The vault itself is never touched.

Each case is one review run with the reviewer models (the cost of reviewing one compile).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from memexos import events, i18n
from memexos.scaffold import sample_dir, vault_files
from memexos.util import atomic_write

CASES = Path(__file__).parent / "gate2_cases.json"


def cases() -> dict:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout


def _edit(root: Path, e: dict) -> None:
    p = root / e["file"]
    text = p.read_text(encoding="utf-8")
    if e["old"] not in text:
        raise ValueError(f"seed text not found in {e['file']}: {e['old'][:60]!r}")
    p.write_text(text.replace(e["old"], e["new"], 1), encoding="utf-8")


def build(case: dict, root: Path) -> str:
    """The scratch repository for a case; returns the base commit."""
    shutil.copytree(sample_dir(), root)
    for rel, content in vault_files("en").items():    # the vault rules (AGENTS.md, taxonomy) the reviewer checks against
        if not (root / rel).exists():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content, encoding="utf-8")
    for s in case["seeds"]:                         # sources are input: planted text in a source is there before the compile
        if "raw_append" in s:
            p = root / s["raw_append"]["file"]
            p.write_text(p.read_text(encoding="utf-8") + s["raw_append"]["text"], encoding="utf-8")
    hold = {p: (root / p).read_text(encoding="utf-8") for p in case["pages"]}
    for p in case["pages"]:
        (root / p).unlink()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "MemexOS calibration")
    git(root, "config", "user.email", "calibration@localhost")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "before the compile")
    base = git(root, "rev-parse", "HEAD").strip()
    for p, text in hold.items():
        (root / p).write_text(text, encoding="utf-8")
    for s in case["seeds"]:
        for e in s.get("edits") or ([s["edit"]] if "edit" in s else []):
            _edit(root, e)
    return base


def caught(review: dict | None, rules: list[dict]) -> bool | None:
    if not review:
        return None
    for r in rules:
        if "claims" in r:
            c = r["claims"]
            if any(x["verdict"] in c["verdicts"] and any(t in x["claim"] for t in c["any_text"]) for x in review.get("claims", [])):
                return True
        if "overwrites" in r:
            if any(any(t in o.get("new_text", "") for t in r["overwrites"]["any_text"]) and o.get("substantive")
                   and not (o.get("backed_by_source") and o.get("disclosed")) for o in review.get("overwrites", [])):
                return True
        if "missed" in r:
            if any(k.get("importance") in ("critical", "must") and not k.get("covered") and
                   any(t.lower() in k.get("point", "").lower() for t in r["missed"]["any_text"]) for k in review.get("key_points", [])):
                return True
        if "injections" in r and any(i.get("complied") for i in review.get("injections", [])):
            return True
    return False


def run_case(cfg, name: str, out: Path) -> dict:
    from memexos.pipeline import gate2
    case = cases()[name]
    work = out / name
    work.mkdir(parents=True)
    base = build(case, work / "vault")
    with events.step(cfg.paths.state, "gate2", mode="calibration", case=name) as info:
        code, rep = gate2.run(cfg, work / "vault", base, case["raw"], work / "review", mode="ingest", job="eval")
        info["result"] = {0: "passed", 1: "failed"}.get(code, "incomplete")
    review_path = work / "review" / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else None
    seeds = {s["id"]: caught(review, s["detect"]) for s in case["seeds"]}
    summary = {"case": name, "desc": case["desc"], "expect_pass": case["expect_pass"], "completed": code != 2, "passed": rep.get("passed"),
               "as_expected": code != 2 and rep.get("passed") == case["expect_pass"], "seeds": seeds,
               "errors": rep.get("errors", []), "warnings": rep.get("warnings", []), "metrics": rep.get("metrics", {}),
               "review": str(review_path)}
    atomic_write(work / "summary.json", json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


def render(s: dict) -> str:
    c = cases()[s["case"]]
    lines = [i18n.t("eval.g2_case", case=s["case"], desc=s["desc"])]
    if not s["completed"]:
        return "\n".join(lines + ["  " + i18n.t("eval.g2_incomplete", why=(s["errors"] or ["?"])[0][:300])])
    lines.append("  " + i18n.t("eval.g2_result", result=i18n.t("eval.g2_pass" if s["passed"] else "eval.g2_fail"),
                               expected=i18n.t("eval.g2_pass" if s["expect_pass"] else "eval.g2_fail")))
    for seed in c["seeds"]:
        hit = s["seeds"].get(seed["id"])
        lines.append(f"  {'✓' if hit else '✕'} {seed['what']}")
    if s["expect_pass"] and s["errors"]:
        lines.append("  " + i18n.t("eval.g2_false_alarms", n=len(s["errors"])))
        lines += [f"    - {e[:240]}" for e in s["errors"][:10]]
    m = s.get("metrics") or {}
    lines.append("  " + i18n.t("eval.g2_cost", model=m.get("model") or m.get("backend") or "-", secs=m.get("elapsed_s") or 0,
                               cost=f"${m['cost_usd']:.2f}" if isinstance(m.get("cost_usd"), (int, float)) else "-", path=s["review"]))
    return "\n".join(lines)


def cmd(cfg, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="memex eval gate2")
    ap.add_argument("--case", action="append", choices=list(cases()))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list:
        for k, v in cases().items():
            print(f"{k:8} {v['desc']} ({len(v['seeds'])} planted errors)")
        return 0
    names = a.case or list(cases())
    out = cfg.paths.state / "evals" / "gate2" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    print(i18n.t("eval.g2_start", n=len(names)), file=sys.stderr)
    ok = True
    with events.run(cfg.paths.state, "eval"):
        for name in names:
            s = run_case(cfg, name, out)
            print(render(s))
            ok &= bool(s["as_expected"]) and all(s["seeds"].values())
    return 0 if ok else 1
