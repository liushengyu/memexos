"""Evaluations: `memex eval retrieval …` (the read side). Gate 2 itself is the write side's check."""

from __future__ import annotations

import sys

from memexos import i18n

SUITES = {"retrieval": "memexos.evals.retrieval"}


def cmd_eval(loader, argv: list[str]) -> int:
    if not argv or argv[0] not in SUITES:
        print(i18n.t("eval.usage", suites=", ".join(SUITES)), file=sys.stderr)
        return 2
    import importlib
    return importlib.import_module(SUITES[argv[0]]).cmd(loader(), argv[1:])
