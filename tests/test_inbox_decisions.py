"""An inbox decision is carried out the same way whatever language the decision was made in and whatever language
the run that carries it out uses."""

import json
import subprocess
from pathlib import Path

import pytest

from memexos import config, i18n, inbox, scaffold

BRANCH = "staging/20260101-000000"


@pytest.fixture
def vault(home, monkeypatch):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    monkeypatch.setenv("MEMEX_VAULT", str(v))
    cand = v / "inbox" / "candidates" / "2026-01-01"
    cand.mkdir(parents=True)
    (cand / "README.md").write_text("# Candidates\n\n- `web:example`: an article on retrieval evaluation (https://example.com/a)\n",
                                    encoding="utf-8")
    (v / "inbox" / "2026-01-01-blocked.md").write_text(f"# Held\n\n## 12:00:00 · `{BRANCH}`\n\n- reason: review failed\n",
                                                       encoding="utf-8")
    subprocess.run(["git", "-C", str(v), "branch", BRANCH], check=True, capture_output=True)
    captured = []
    monkeypatch.setattr("memexos.capture.add.add", lambda cfg, url, via="": captured.append(url) or Path("raw/x.md"))
    before = i18n.language()
    yield v, captured
    i18n.set_language(before)


def item(cfg, src):
    return next(it for it in inbox.merged(cfg) if it["src"] == src)


def admission(cfg):
    p = cfg.paths.ledger / "admission.jsonl"
    return [json.loads(line)["decision"] for line in p.read_text().splitlines()] if p.exists() else []


def branch_exists(v):
    return bool(subprocess.run(["git", "-C", str(v), "branch", "--list", BRANCH], capture_output=True, text=True).stdout.strip())


@pytest.mark.parametrize("decide_lang", ["en", "zh"])
@pytest.mark.parametrize("run_lang", ["en", "zh"])
@pytest.mark.parametrize("case", ["keep", "drop", "discard", "leave"])
def test_a_decision_is_carried_out_the_same_in_any_language(vault, case, decide_lang, run_lang):
    v, captured = vault
    cfg = config.load(v)
    src, choice = {"keep": ("candidate", 0), "drop": ("candidate", 1), "discard": ("blocked", 0), "leave": ("blocked", 1)}[case]
    i18n.set_language(decide_lang)
    it = item(cfg, src)
    assert it["triage"]["options"][choice]["action"] == case
    ok, _ = inbox.decide(cfg, it["id"], choice=choice)
    assert ok
    i18n.set_language(run_lang)
    inbox.script_actions(cfg)
    if src == "candidate":
        assert admission(cfg) == (["admit"] if case == "keep" else ["reject"])
        assert captured == (["https://example.com/a"] if case == "keep" else [])
    else:
        assert branch_exists(v) == (case == "leave")


def test_a_decision_saved_without_its_position_is_matched_in_any_language(vault):
    # decisions recorded before the position was used only carry the label, here the Chinese “收” (keep)
    v, captured = vault
    cfg = config.load(v)
    i18n.set_language("en")
    it = item(cfg, "candidate")
    inbox.append(cfg, {"id": it["id"], "event": "decided", "dismiss": False, "text": "", "label": i18n.t("inbox.keep", "zh")})
    inbox.script_actions(cfg)
    assert admission(cfg) == ["admit"] and captured == ["https://example.com/a"]
