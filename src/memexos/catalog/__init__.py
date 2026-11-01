"""Source classification: for every raw file, the field it belongs to, how expert it is, what it is good for and
how far it can be trusted (standard: .memex/taxonomy.md in the vault).

    memex catalog backlog [--limit N]     classify what is not classified yet, then run the draft check
    memex catalog list [filters]          browse the catalog (read-only)
    memex catalog stats                   counts by domain, type and evidence (read-only)
    memex catalog drafts                  provisional categories and what each still needs (read-only)
    memex catalog check                   draft lifecycle: re-home unknown domains, expire, promote to pending
    memex catalog sync                    write classifications into source pages and group the index by domain
    memex catalog redo <raw…> --why …     classify again (appends; the last line wins)
    memex catalog decide <draft> …        apply the person's decision on a pending category

The catalog (.memex/catalog.jsonl) is append-only; the last line for a raw file wins. raw/ is never written back to.
A visual companion shares its transcript's classification.
"""

from __future__ import annotations

from memexos.catalog.classify import ensure, lines, view  # noqa: F401
