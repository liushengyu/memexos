This is a knowledge base: a wiki that agents compile from the sources its owner collects (raw/). After each compile, the writing agent
leaves items for the owner in the inbox, but far too many to read. Triage the items below one by one: pick out only what truly needs the
owner's own decision, and phrase those so they can be understood at a glance and answered with one click.

Five kinds:
- decide: only the owner can settle it, because it needs their intent, preference or trade-off (which project something belongs to, whether
  to split or merge pages, whether to change a classification rule, whether to pursue a direction), or facts only they can see (their own
  accounts, what they did, their plans), or it is costly and irreversible if wrong. Anything that can be settled from the knowledge base's
  raw sources and pages, or from public sources, is NOT decide.
- agent: an agent can settle and fix it: check a statement against raw, correct or mark what the source does not support, close a ⚠️ that
  later sources answered, apply a low-risk suggestion the report already spells out, add links. Mark where the evidence comes from:
  - only files already in the knowledge base: needs_web and needs_local both false
  - public material must be collected first (web pages, papers, repository READMEs, official docs, videos): needs_web true
  - evidence on this machine first (project repositories, run logs, configuration, read-only commands such as wc, ls, git log, or reading
    the frames of a video in the knowledge base): needs_local true. These have factual answers; they need no decision from the owner
- system: a problem with the pipeline or a tool itself that needs a code or configuration change, or an experiment with side effects.
  Handled by whoever maintains the system; it needs no knowledge decision from the owner. When an item mixes agent work and system work,
  classify by the main part and put only the agent part in agent_task.
- fyi: just information; the writer already took the default action.
- done: the text already says it is solved, closed, or answered by a later batch.

Two other kinds of things have their own channels; do not ask about them here: proposals for new categories (the classifier presents them)
and uncertain intake candidates (listed separately). A report that merely mentions them is fyi.

Judge only from the text below; do not open files.

Every item needs every field (empty string, empty array, -1 or false when not applicable):
- id: copied as is
- why: one sentence on why this kind
- decide only:
  - question: one plain sentence the owner can understand without having read the report; no "item N", "this batch", "compiler's inference"
  - context: two or three sentences: which page or project, what the two sides say, why the owner must decide
  - options: 2–4 options; label short ("merge into one page", "keep both"); effect: what changes in the knowledge base if chosen. No "decide later" (the interface has it)
  - recommend: index (from 0) of the recommended option, -1 for no preference; recommend_why: one sentence
- agent only: agent_task: one sentence on the result to reach and when it counts as done (no commands); plus needs_web and needs_local
- pages: wiki page names involved (without [[ ]]), else an empty array

Items (neighbouring items of one report may refer to each other):
