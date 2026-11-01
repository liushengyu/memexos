---
title: "How spaced repetition works"
type: source
source_file: raw/2026-01-05-web-example-how-spaced-repetition-works.md
url: https://example.com/articles/how-spaced-repetition-works
source_type: web
platform: example
published: 2025-11-20
captured: 2026-01-05
domain: Life/Learning and health
secondary: []
content_type: explainer
level: intro
evidence: third-party report
usage: [understand how it works, learn a skill]
durability: long-term
use_note: "A popular explainer that retells research and software history second-hand; the closing advice on daily time is the author's opinion, not a finding."
---

# How spaced repetition works

An introductory article explaining why we forget, how spaced repetition counters it, and how the idea evolved from
paper boxes to scheduling algorithms.

## Key points

- **The forgetting curve.** According to the article, [[Hermann Ebbinghaus]] published the first systematic experiments
  on forgetting in 1885, using himself as the subject and lists of nonsense syllables; retention falls steeply in the
  first hours and the first day, then more slowly ([[Forgetting curve]]).
- **Savings and spacing.** Relearning is faster than first learning, and reviews spread over several days hold better
  than the same reviews in one session: the spacing effect.
- **[[Leitner system]] (1972).** Paper cards move up one box when answered correctly and go back to the first box when
  answered wrong; lower boxes are reviewed more often.
- **[[SM-2]] (1987).** Piotr Woźniak's algorithm for SuperMemo schedules each card individually with an ease factor
  (start 2.5, minimum 1.3) and intervals of 1 day, 6 days, then the previous interval times the ease factor; a grade
  below 3 restarts the card.
- **Software.** [[Anki]] (first released 2006) uses a variant of SM-2; since Anki 23.10 (2023) users can switch to FSRS,
  which fits a memory model to their own review history.
- **Advice.** One fact per card; cards in your own words after understanding; a little review every day.
- The author's claim that twenty minutes a day suffices for most people is a personal opinion with no evidence offered.

## Sources

- `raw/2026-01-05-web-example-how-spaced-repetition-works.md`
