You maintain this knowledge base. First read AGENTS.md in full. An independent reviewer checked recent compiles and found the $n
problems below; fix them one by one in the current directory (nobody is online; do not wait for confirmation):

$problems

How to fix each one is your call, but only in these ways:
- contradicts the source, or is neither in the source nor a reasonable inference: correct it to match the source, or delete it
- a reasonable generalization: keep it and end the sentence with "(compiler's inference)"
- inaccurate or overstated: tone it down to what the source says
- a missing key point: add it to the right page
- pages conflicting or duplicating each other: make them agree and link them
Read the raw source before changing anything; do not fix from memory. A page may have changed since the review: find the sentence as it
is now, and skip it if it is gone or already right. Change only what these problems concern; do not rewrite anything else.

Afterwards:
1. Append `## [$date] lint | fixed $n problems found by review` to log.md
2. Write inbox/$date-fixes.md for people: for each item "problem → what you did (changed to what / marked as inference / deleted / skipped and why)", in plain words, with page names
When done, output a single summary line.
