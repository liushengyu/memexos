"""Evaluations: `memex eval retrieval …` (the read side), `memex eval tooluse …` (do agents call the right commands, and
how directly) and `memex eval gate2` (calibrating the write side's review)."""

from __future__ import annotations

import sys

from memexos import i18n

SUITES = {"retrieval": "memexos.evals.retrieval", "gate2": "memexos.evals.gate2", "tooluse": "memexos.evals.tooluse"}


def cmd_eval(loader, argv: list[str]) -> int:
    if not argv or argv[0] not in SUITES:
        print(i18n.t("eval.usage", suites=", ".join(SUITES)), file=sys.stderr)
        return 2
    import importlib
    return importlib.import_module(SUITES[argv[0]]).cmd(loader(), argv[1:])
