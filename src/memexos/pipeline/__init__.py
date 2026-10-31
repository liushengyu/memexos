"""The write side: compile raw sources into the wiki on a staging worktree, check, review, publish.

    stage.py     the staging worktree and the ledgers (what was compiled, what is held)
    gate1.py     program checks on what was written (write scope, Sources, index, log, citations)
    selfcheck.py the judge compares each new sentence with the sources before review
    gate2.py     the independent review (another model, claim by claim; pass/fail computed here)
    report.py    review results in plain words for people and for the writer
    compile.py   ingest, lint, fix, correct: the jobs that drive the above
"""
