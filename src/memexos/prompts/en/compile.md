You maintain this knowledge base. First read AGENTS.md in full, then compile the raw files below following its §2.1 "Automatic mode"
(do them all in one go; nobody is online, so do not wait for confirmation):

$files

The classification of these sources (provided by the program; the standard is .memex/taxonomy.md). Write it into the source pages'
frontmatter and register each source under its domain in the index as AGENTS.md §2.1.4 describes, and word citations by evidence strength:
$classifications

Hard constraints:
1. Never modify or delete anything under raw/.
2. Write files only under wiki/, index.md, log.md and inbox/.
3. Do not settle questions that need a person (contradictions with existing content, uncertain classification, key claims marked
   ⚠️ unverified, proposed schema changes); collect them in inbox/$date-ingest.md. Do not create that file when there is nothing.
4. Append one ingest entry per file to log.md.

The program handles these cases; just do as follows and do not report them in the inbox:
- Sources marked "(not classified yet)": fill the frontmatter provisionally by .memex/taxonomy.md (the classifier fills in and syncs later).
- Transcripts whose frontmatter has coverage (only part was captured) or asr_gaps (stretches without text): say what is covered on
  the source page; this is not a system problem.

When done, output a single summary line: which files you processed, which pages you created or updated, and whether the inbox got new items.
