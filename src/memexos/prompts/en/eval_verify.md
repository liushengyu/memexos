You are the **independent checker** of retrieval evaluation questions (another model wrote them). The current
directory is the knowledge base. Check each question below and decide whether to keep it:

- answerable questions: open every gold page and confirm each key fact is there. If a page is missing, a fact cannot
  be found, or the answer is really on another page, correct gold_pages / key_facts; if that cannot be fixed,
  keep=false. Ambiguous questions (several reasonable answers) or questions copying a page's sentence → keep=false.
- negative questions: search `wiki/` and `raw/` with several phrasings; if the answer is there, keep=false.
- usage questions: they test whether an agent **respects a source's limits** (evidence strength, durability, scope,
  goal versus result), stated in the page text or in the classification. Confirm the question invites an overreach
  (a report taken as a measurement, a goal as a result, one case as a rule, an old version as current) and that
  use_rule and key_facts are grounded in the page or the classification; if it invites no overreach, keep=false.
- do not go easy, and do not drop a question for being hard: hard questions are the point.

The questions (JSON, one per line):
$items

Read only. Output strictly by the schema {"results": [...]}, one per question with its id copied; gold_pages and
key_facts are the checked versions.
