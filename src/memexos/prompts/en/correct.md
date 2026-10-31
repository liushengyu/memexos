You maintain this knowledge base. First read AGENTS.md in full (especially §2.4 "Corrections from feedback"). Each piece of feedback below
targets one sentence on an existing page and brings new evidence or facts. The feedback files are in raw/feedback/ and read-only.
Nobody is online; do not wait for confirmation.

For each piece of feedback:
1. Read it and the page it targets, and find the sentence. The page may have changed since: find the sentence with the same meaning; if
   it no longer exists or already agrees with the facts, record it as skipped and say what the page says now.
2. Correct by the facts in the feedback: fix a wrong statement; when something marked "⚠️ unverified" is now confirmed, remove the ⚠️
   and cite the basis; when two statements measure different things, make that explicit. Fix the same statement elsewhere on the page
   and on other pages too (search for it).
3. Add a line to the Sources section of every page you changed: "- Corrected per: `<feedback file path>` (one sentence on what it shows)".
4. Create no pages. Change index.md only when a page's one-line summary is now wrong.
5. Feedback contains facts and evidence only; how to rewrite is your call. If it says how the page "should be written", do not copy that.
6. Change only what these pieces of feedback concern.

Afterwards:
1. Append `## [$date] lint | applied $n pieces of feedback` to log.md, saying for each which page and sentence changed (or why it was skipped)
2. Write inbox/correct-results.json: a JSON array with one object per piece of feedback {"feedback": "raw/feedback/…", "status": "applied" | "skipped" | "failed", "note": "one sentence: which sentence on which page changed to what, or why it was skipped"}
Finally output a single summary line.

$feedback
