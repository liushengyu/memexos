$spec

---

## Your task: write evaluation questions for this knowledge base

The current directory is the knowledge base: `index.md` is the table of contents, `wiki/` holds the compiled pages,
`raw/` the original sources, `.memex/catalog.jsonl` the classification of the sources.
Write $n questions with this quota: $quota.

- Read `index.md` first, then open real pages. **Every gold page of an answerable question must be a page you opened
  and read that really contains the answer.** Every key fact must be findable on the gold pages.
- Spread the questions over domains and page kinds (concepts, entities, sources, topics); do not cluster.
- `usage` questions test whether the reader respects the source's classification (evidence, domain, durability in
  `.memex/catalog.jsonl` and the source pages' frontmatter); write the correct use in use_rule.
- `negative` questions: related-looking, but the answer is really absent (search `wiki/` and `raw/` with several
  phrasings to confirm). gold_pages may name the page most likely to be mistaken for the answer; answerable=false.
- `real` questions: pick from `$mined` (one JSON object per line: questions the owner asked in past sessions) those
  related to this knowledge base, cleaned of context. answerable=true with a gold standard when the base answers them,
  false when it does not but the owner would plausibly look here.

Domains of the sources (counts):
$domains

Read only; change nothing. Output strictly by the JSON schema: {"items": [...]}.
