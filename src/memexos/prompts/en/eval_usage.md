$spec

---

## Your task: $n "correct use" questions

These test whether an agent reads the **classification** of a source (in `.memex/catalog.jsonl` and the source pages'
frontmatter: `evidence`, `content_type`, `durability`, `domain`, `use_note`; `memex catalog list --help` lists
filters) to decide **whether it may be used this way**. A good question can only be answered correctly with the
classification; the facts alone lead to an answer that goes too far. For example:
- a third-party report or a promotional piece states a number confidently → ask whether it can go into a report as a
  measured result
- a showcase demonstrates a result without the method → ask how to reproduce it
- a short-lived source (one version, one moment) → ask whether it is still true
- a source from another domain → ask whether its conclusion transfers
- ask whether the base has first-hand measurements of X, which needs filtering sources by evidence

Find suitable source pages first and **check that the page text does not state the limitation itself** (if it does,
the question does not test the classification; pick another). gold_pages: those source pages; key_facts: the points
of the correct answer; use_rule: the correct use. Cover different domains. Read only. Output strictly by the JSON
schema: {"items": [...]}, every type is usage.
