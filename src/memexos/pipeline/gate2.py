"""Gate 2: the independent review.

A reviewer model (role "reviewer"; ideally a different model family from the writer) checks, in a read-only
sandbox, whether what was newly written is faithful to the raw sources, following the rubric in
prompts/<lang>/gate2_rubric.md. It returns per-claim verdicts with evidence; whether the batch may be published is
computed here from those verdicts, never decided by the reviewer.

run() returns (code, report): 0 passed, 1 failed (report["errors"] can go back to the writer), 2 the review
could not be completed (network, quota, unparseable output).
"""

from __future__ import annotations

import json
import subprocess
from importlib import resources
from pathlib import Path

from memexos import backends, i18n

BAD = ("UNSUPPORTED", "CONTRADICTED")
NONCRIT_BAD_MAX = 0.05
MUST_COVERAGE_MIN = 0.80


def schema() -> dict:
    return json.loads(resources.files("memexos.pipeline").joinpath("gate2_schema.json").read_text(encoding="utf-8"))


def git(d: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(d), *args], capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout


def build_prompt(base: str, raws: list[str], changed: list[str], mode: str = "ingest") -> str:
    return i18n.prompt("gate2_rubric") + i18n.prompt(
        "gate2_task", base=base, raws="\n".join(f"  - `{r}`" for r in raws), changed="\n".join(f"  - `{c}`" for c in changed),
        fix_note=i18n.prompt("gate2_fix_note") if mode == "fix" else i18n.prompt("gate2_lint_note") if mode == "lint" else "")


def decide(review: dict) -> tuple[list[str], list[str], dict]:
    claims, kps = review["claims"], review["key_points"]
    errors, warnings = [], []
    counts = {v: sum(1 for c in claims if c["verdict"] == v) for v in ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "CONTRADICTED", "MARKED")}
    for c in claims:
        if c["critical"] and c["verdict"] in BAD:
            errors.append(f"[critical {c['verdict']}] {c['page']}: “{c['claim']}” — {c['reason']} "
                          f"(raw: {c['raw_location'] or '-'}; source text: {c['evidence'][:200] or '-'})")
    for o in review["overwrites"]:
        if o["substantive"] and not (o["backed_by_source"] and o["disclosed"]):
            why = "no source behind it" if not o["backed_by_source"] else "not disclosed on the page, in the log or the inbox"
            errors.append(f"[earlier conclusion overwritten: {why}] {o['page']}: was “{o['old_text'][:160]}”, now “{o['new_text'][:160]}” — {o['reason']}")
    for inj in review["injections"]:
        if inj["complied"]:
            errors.append(f"[followed an instruction inside a source] the source says “{inj['raw_text'][:160]}”; the wiki complied at {inj['where']} — {inj['reason']}")
    noncrit = [c for c in claims if not c["critical"] and c["verdict"] != "MARKED"]
    noncrit_bad = [c for c in noncrit if c["verdict"] in BAD]
    rate = len(noncrit_bad) / len(noncrit) if noncrit else 0.0
    if rate > NONCRIT_BAD_MAX:
        errors.append(f"[unsupported non-critical claims {rate:.0%} > {NONCRIT_BAD_MAX:.0%}] " +
                      "; ".join(f"{c['page']}: “{c['claim'][:80]}” ({c['verdict']})" for c in noncrit_bad[:8]))
    else:
        warnings += [f"[non-critical {c['verdict']}] {c['page']}: “{c['claim']}” — {c['reason']}" for c in noncrit_bad]
    crit_kp = [k for k in kps if k["importance"] == "critical"]
    must = [k for k in kps if k["importance"] == "must"]
    nice = [k for k in kps if k["importance"] == "nice"]
    errors += [f"[critical point missing] {k['point']}" for k in crit_kp if not k["covered"]]
    if not crit_kp and kps:
        warnings.append("the reviewer marked no critical points; core coverage cannot be judged")
    must_cov = sum(k["covered"] for k in must) / len(must) if must else 1.0
    if must_cov < MUST_COVERAGE_MIN:
        errors.append(f"[must-have coverage {must_cov:.0%} < {MUST_COVERAGE_MIN:.0%}] missing: " +
                      "; ".join(k["point"] for k in must if not k["covered"]))
    else:
        warnings += [f"[must point missing] {k['point']}" for k in must if not k["covered"]]
    warnings += [f"[PARTIAL] {c['page']}: “{c['claim']}” — {c['reason']}" for c in claims if c["verdict"] == "PARTIAL"]
    warnings += [f"[integration] {n}" for n in review["integration_notes"]]
    judged = [c for c in claims if c["verdict"] != "MARKED"]
    crit = [c for c in judged if c["critical"]]
    metrics = {
        "claims": len(claims), "verdicts": counts,
        "faithfulness_strict": round(counts["SUPPORTED"] / len(judged), 3) if judged else None,
        "faithfulness_gate": round(1 - sum(c["verdict"] in BAD for c in judged) / len(judged), 3) if judged else None,
        "critical_claims": len(crit),
        "critical_faithfulness": round(1 - sum(c["verdict"] in BAD for c in crit) / len(crit), 3) if crit else None,
        "critical_points": len(crit_kp), "critical_points_missed": sum(not k["covered"] for k in crit_kp),
        "must_points": len(must), "must_coverage": round(must_cov, 3),
        "nice_points": len(nice), "nice_coverage": round(sum(k["covered"] for k in nice) / len(nice), 3) if nice else None,
        "silent_overwrites": sum(1 for o in review["overwrites"] if o["substantive"] and not (o["backed_by_source"] and o["disclosed"])),
        "injections_found": len(review["injections"]),
        "injections_complied": sum(i["complied"] for i in review["injections"]),
    }
    return errors, warnings, metrics


def run(cfg, stage: Path, base: str, raws: list[str], artifacts: Path, mode: str = "ingest", job: str = "") -> tuple[int, dict]:
    artifacts.mkdir(parents=True, exist_ok=True)
    changed = [p for p in git(stage, "diff", "--name-only", "-z", base, "--", "wiki", "index.md").split("\0") if p]
    changed += [p for p in git(stage, "ls-files", "--others", "--exclude-standard", "-z", "wiki").split("\0") if p]
    changed = list(dict.fromkeys(changed))
    if not changed:
        rep = {"passed": True, "errors": [], "warnings": ["no wiki changes; review skipped"], "metrics": {}, "artifacts": str(artifacts)}
        return 0, rep
    prompt = build_prompt(base, raws, changed, mode)
    (artifacts / "prompt.md").write_text(prompt, encoding="utf-8")
    for stale in ("review.json", "report.json"):
        (artifacts / stale).unlink(missing_ok=True)
    # Backends without a shell (the API, Claude Code's read-only tools) get the diff computed for them.
    subprocess.run(["git", "-C", str(stage), "add", "-A", "--", "wiki", "index.md"], capture_output=True, stdin=subprocess.DEVNULL)
    diff = git(stage, "diff", "--cached", "-U8", base, "--", "wiki", "index.md")
    if len(diff) > 600_000:
        diff = diff[:600_000] + "\n…(diff truncated; open the pages directly for the rest)"
    extra = f"### git diff -U8 {base} -- wiki index.md\n\n```diff\n{diff}\n```"
    r = backends.structured(cfg, "reviewer", prompt, schema(), cwd=stage, workdir=artifacts,
                            effort=cfg.get("pipeline.gate2_effort") or "xhigh",
                            timeout=float(cfg.get("pipeline.gate2_timeout") or 2400), extra=extra, job=job or "gate2")
    base_metrics = {"model": r.model, "backend": r.backend, "elapsed_s": r.elapsed_s, "usage": r.usage, "cost_usd": r.cost_usd,
                    "tried": r.tried}
    if not r.ok or not isinstance(r.result, dict):
        rep = {"passed": False, "errors": [f"review not completed ({r.backend}): {r.tail[-200:]} (details in {artifacts})"],
               "warnings": [], "metrics": base_metrics, "artifacts": str(artifacts)}
        (artifacts / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        return 2, rep
    review = r.result
    (artifacts / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    writer = backends.chain(cfg, "writer")
    errors, warnings, metrics = decide(review)
    metrics.update(base_metrics, same_family_as_writer=bool(writer) and r.backend in writer[:1],
                   notes_on_eval=review.get("notes_on_eval", ""))
    rep = {"passed": not errors, "errors": errors, "warnings": warnings, "metrics": metrics, "artifacts": str(artifacts),
           "raws": raws, "mode": mode}
    (artifacts / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return (0 if not errors else 1), rep
