You maintain this knowledge base. First read AGENTS.md in full.
Below are $n items from the inbox. Items marked [THE OWNER DECIDED] carry a decision the owner made in the console; carry it out.
Items marked [FOR YOU] were judged to be things you can do yourself. Nobody is online; do not wait for confirmation.

For each item:
1. First check whether it still applies: pages may have changed since. If it no longer applies or is already done, record it as skipped and say what the page says now.
2. [THE OWNER DECIDED]: do what was decided; do not substitute your own judgment. If the decision conflicts with the sources, follow the sources and explain the conflict in the result.
3. [FOR YOU]: do the task. Verify only against raw/ sources and existing pages (search them yourself); when settling it would need outside material, do not guess: record skipped and say what is missing.
4. Follow AGENTS.md: do not touch raw/; write only wiki/, index.md, log.md, inbox/; every statement needs a source; add links; when closing a ⚠️ cite the basis.
5. Change only what these items concern.

Afterwards:
1. Append `## [$date] lint | inbox: $n items` to log.md
2. Append to inbox/$date-inbox-log.md (create it with the heading "# Inbox log — $date" if missing): for each item "the item → what was done (which page changed to what / skipped and why)", in plain words, with page names
3. Write $results: a JSON array with one object per item {"id": "...", "status": "applied" | "skipped" | "failed", "note": "one-sentence result"}, ids copied as is
Finally output a single summary line.

$items
