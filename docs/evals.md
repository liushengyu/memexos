# Evaluations

Two checks keep a knowledge base honest. **Gate 2** is the write side: every compile is reviewed, sentence by
sentence, against its sources by a second model before it is published (see `docs/architecture.md`). The
**retrieval evaluation** is the read side: can any agent that uses the knowledge base find the right content and use
it correctly? Written right plus found and used right is the whole loop.

## Two levels

| Level | Question | How | Cost |
|---|---|---|---|
| **L1, the search tool** | Given a question, does `memex search` put the page you need near the top? | Every question runs through every search mode; the rank of the first right page is recorded | Local; no model calls (unless your reranker is a model) |
| **L2, agents using the base** | Using only the command line interface, does an agent answer correctly, cite the right pages, say "not in the base" when that is true, and respect how reliable a source is? | Each agent answers in an empty directory, as if you asked from another project; another model grades the answers | One agent run per question per agent, plus grading |

L1 says whether the tool is good; L2 says whether the whole interface works for agents. Run L2 with **at least two
different agents**: the knowledge base should not depend on one agent's habits, and a gap between agents shows where
it does.

## Questions

| Type | Share | Example | Tests |
|---|---|---|---|
| `fact` | ~35% | "what did bush say the memex desk would store stuff on?" | finding one fact on one page |
| `synth` | ~20% | "how did the memex idea get from bush to the web?" | combining several pages |
| `usage` | ~15% | "can I quote the 10x speed-up from that vendor post as a measured result?" | reading a source's classification: evidence strength, scope, durability |
| `negative` | ~10% | a related-looking question the base truly cannot answer | saying "not in the knowledge base" instead of inventing |
| `real` | ~20% | questions you actually asked in past agent sessions | real use |

Each question carries a gold standard: `gold_pages` (the wiki pages needed), `gold_raw` (their sources),
`key_facts` (the points a correct answer contains, one checkable sentence each), `answerable`, `use_rule` (usage
questions) and `domain`. Questions must read like a person asking, never copying a page title or sentence.

## Writing and checking questions

Questions are written by one model run and checked by another, independent run. Both use the `evals.role` models
(the reviewer by default, so the model that writes the wiki does not grade itself).

```
memex eval retrieval cover          one question for each page that has none, then check (nightly: evals.cover_per_night pages)
memex eval retrieval mine           questions you asked in past sessions (candidates for `real`)
memex eval retrieval gen --n 40     questions by quota, including real and negative ones
memex eval retrieval usage --n 10   "correct use" questions only
memex eval retrieval verify         check the latest candidates; the kept ones join the bank
```

The checker opens every gold page and confirms each key fact is there, searches for negative questions' answers with
several phrasings, and drops ambiguous questions and questions that copy page text. Hard questions are kept on
purpose.

The bank lives in the vault at `.memex/evals/retrieval/questions.jsonl` and is committed with it. The knowledge base
changes every day: before scoring, a question whose gold page no longer exists is marked stale and not scored.

## Scoring

```
memex eval retrieval l1 [--modes keyword,fast,auto]
memex eval retrieval pool           judge the pooled top results page by page
memex eval retrieval l2 [--n 20] [--agents claude-code,codex] [--same-as RUN]
memex eval retrieval status
```

L1, per search mode and per question type:

- **hit@k** (k = 1, 3, 10): is any right page (or its raw source) among the first k results?
- **recall@10**: the share of gold pages in the top 10
- **MRR**: the mean of 1 / rank of the first right page
- **time** per search

A gold standard written by one model misses other pages that also answer the question. `pool` collects the top
results of recent L1 runs and has a model judge each (question, page) pair as `answers`, `partial` or `no`
(`.memex/evals/retrieval/qrels.jsonl`). From then on, a page judged `answers` counts as right and a gold page judged
`no` stops counting; gold pages of synthesis questions judged `partial` still count, because each holds part of the
answer.

L2, per agent: correct, partly correct, wrong, honest "not there", missed an existing answer, invented; whether it
searched the base at all; whether it opened a right page (which separates "did not find" from "found but used
badly"); whether its citations hold; time and cost. Use `--same-as` to rerun the same questions after a change and
compare.

## Tool use

`memex eval tooluse run [--n 20] [--agents claude-code,codex] [--no-tasks] [--same-as RUN]` asks a different question
from L2: not only whether the answer was right, but whether the agent called the right commands with the right
arguments, and how directly it got there. Each agent works as in real use (`memex` on the PATH, the text of
`memex help` in its context, no direct access to the knowledge base's files); in an evaluation run memex records every
call it receives, with its arguments and exit status, and commands that would change something are only recorded.

Every scenario has a reference flow: the calls that must happen (command, argument rules such as "--kw given, in the
pages' language", result rules such as "a right page in the top 5"), whether their order matters, and the fewest calls
a capable agent needs. Questions from the retrieval bank get a reference flow by type (a fact: search, then open a right
page); task scenarios in `.memex/evals/tooluse/scenarios.jsonl` describe other jobs (save a link, report a page, list
sources by evidence, keep a conclusion, check status) and spell out their own.

Scores per agent and per scenario type: done right; reference calls made; calls with correct arguments; calls made
against the fewest needed (efficiency = fewest ÷ made, for the scenarios done right); failed and repeated calls; the call
that first reached a right page; other shell commands; time and tokens. `tooluse-report.md` lists every detour with the
reference and each call the agent made. `memex eval tooluse report [RUN]` scores a saved run again after the rules
change, and `--same-as` reruns the same scenarios to compare before and after a change.

## Reports

Each run writes to `<state>/evals/retrieval/<time>/`: `report.md` for L1 (overall, by type, and typical misses: what
was asked, what should have been found, what came back), `summary.json` (shown on the console's Quality page) and
`l2-report.md` for L2. Rerun L1 after every change that affects retrieval: index settings, chunking, the reranker, the
query rules in the vault's `AGENTS.md`, or the read commands.
