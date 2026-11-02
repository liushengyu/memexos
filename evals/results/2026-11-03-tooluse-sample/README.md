# Tool use on the sample knowledge base · 2026-11-03

Claude Code and Codex each did the same 19 scenarios with only the `memex` command line: 14 questions from the
sample's question bank and the 5 task scenarios (save a link, report a page, list sources by evidence, keep a
conclusion, check status). Every call was recorded and scored against each scenario's reference flow
(see [docs/evals.md](../../../docs/evals.md#tool-use)).

| | Claude Code (claude-opus-5-5) | Codex (gpt-6-astra) |
|---|---|---|
| Done right (required calls, right arguments, nothing forbidden, answer graded right) | 16 / 19 (84%) | 18 / 19 (95%) |
| Calls with correct arguments | 93% | 98% |
| Calls made / fewest needed (average) | 3.2 / 1.9 | 3.3 / 1.9 |
| Efficiency (fewest ÷ made, scenarios done right) | 62% | 67% |
| Time per scenario | 15 s | 36 s |
| Tokens per scenario (cached input included) | about 31,000 | about 87,000 |

What stands out:

- Both agents searched before every answer, and neither invented an answer to the questions the knowledge base
  cannot answer (4 of 4 said so).
- Claude Code is faster and lighter, but often opens more pages than it needs: on single-fact questions it averaged
  2.7 calls where 2 suffice. Two of its three misses were right answers reached with too little reading (one straight
  from the search excerpt without opening the page, one synthesis question answered from a single page); the third was
  an answer graded only partly right.
- Codex completed more scenarios and made fewer argument mistakes, at more than twice the time and tokens. Its one
  miss was an answer graded partly right.

Read with care: 19 scenarios is a small sample (one scenario is about 5 points), so differences smaller than about
20 points are within noise. The full report, with every detour call by call, is in
[tooluse-report.md](tooluse-report.md); the numbers are in [tooluse-summary.json](tooluse-summary.json).

Reproduce (MemexOS at commit 92f8c0c, both command lines logged in):

```sh
memex init /tmp/sample --sample --no-user-config
MEMEX_VAULT=/tmp/sample memex reindex
MEMEX_VAULT=/tmp/sample memex eval tooluse run --n 14
```
