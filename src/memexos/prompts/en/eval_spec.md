# Retrieval evaluation: what a question is

The evaluation measures whether any agent using this knowledge base finds the right content and uses it correctly.
Each question has a gold standard:

- `type`: `fact` (one fact on one page), `synth` (needs several pages), `usage` (answering correctly depends on the
  source's classification: evidence strength, scope, durability), `negative` (looks related, but the knowledge base
  has no answer), `real` (a question the owner actually asked in a past session)
- `gold_pages`: the wiki pages needed to answer (vault-relative paths, e.g. `wiki/concepts/Prompt caching.md`)
- `gold_raw`: the raw sources those pages cite
- `key_facts`: the points a correct answer must contain, one checkable sentence each
- `answerable`: false only for negative questions (and real ones the base cannot answer)
- `use_rule`: for usage questions, how the source may be used (e.g. "must say this is a third-party report")
- `domain`: the domain of the question; `why`: one sentence on what it tests and why it is hard or easy

Questions must read like a person asking: casual wording, keywords only, abbreviations, mixed languages. Never copy a
page title or a sentence from the page, or the evaluation measures string matching instead of retrieval.
