$spec

---

## Your task: one question for each page below

The current directory is the knowledge base. Below are $n pages. **Write one question per page**, answered by that page:
- concept, entity and source pages get `fact` questions; topic pages (`wiki/topics/`) get `synth` questions (other
  pages they link to may join gold_pages, after you confirm they exist and are relevant)
- word it like a person asking; do not copy the title or sentences from the page
- the question must have one answer; if the same core content is also on other pages (a source page and its concept
  page, for example), add those pages to gold_pages
- key_facts: 2–3 points the answer must contain, each findable on the gold pages
- gold_raw: the raw paths the page cites; domain: from the page's frontmatter where it has one
- skip pages too thin for a meaningful question (placeholders of one or two sentences)

$docs

Read only; change nothing. Output strictly by the JSON schema: {"items": [...]}.
