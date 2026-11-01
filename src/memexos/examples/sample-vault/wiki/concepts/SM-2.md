---
title: SM-2
type: concept
sources: 1
---

# SM-2

SM-2 is the scheduling algorithm Piotr Woźniak wrote for SuperMemo in 1987; it schedules every flashcard individually
([[How spaced repetition works]]).

- After each review the answer is graded from 0 to 5.
- Each card has an ease factor that starts at 2.5 and never goes below 1.3; good grades raise it, poor grades lower it.
- Intervals: 1 day, then 6 days, then each interval is the previous one multiplied by the ease factor.
- A grade below 3 counts as a failure and the card starts again from the first interval.

[[Anki]] schedules cards with a variant of SM-2 by default. See also [[Spaced repetition]].

## Sources

- `raw/2026-01-05-web-example-how-spaced-repetition-works.md`
