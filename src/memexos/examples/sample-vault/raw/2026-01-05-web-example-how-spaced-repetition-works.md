---
source_type: web
platform: example
title: How spaced repetition works
author: Sample author
url: https://example.com/articles/how-spaced-repetition-works
published: 2025-11-20
captured: 2026-01-05
fetched_via: sample
---

# How spaced repetition works

Most of what we read is gone within days. Spaced repetition is the simplest reliable way to keep it: you review a
fact just before you would forget it, and every successful review pushes the next one further into the future.

## The forgetting curve

In 1885 the German psychologist Hermann Ebbinghaus published *Über das Gedächtnis* (Memory), the first systematic
experiments on forgetting. He served as his own subject and memorised lists of nonsense syllables (consonant, vowel,
consonant), so that meaning would not help him, and measured how much effort it took to relearn a list after a delay.

His results trace what is now called the forgetting curve: retention drops steeply in the first hours and the first
day, then the decline slows down. He also found that relearning is faster than the first learning ("savings"), and
that reviews spread over several days hold better than the same number of reviews crammed into one session. That
second finding is known as the spacing effect.

## Boxes: the Leitner system

In 1972 the German science journalist Sebastian Leitner described a way to apply this with paper flashcards and a few
boxes. Every card starts in the first box. When you answer a card correctly it moves up one box; when you get it wrong
it goes back to the first box. Lower boxes are reviewed often (the first box every day), higher boxes less and less
often. Cards you know drift towards rarely reviewed boxes, while difficult cards keep coming back.

## An algorithm: SM-2

In 1987 Piotr Woźniak wrote SuperMemo, and with it an algorithm called SM-2 that schedules each card on its own:

- after each review you grade your answer from 0 to 5;
- every card has an ease factor, starting at 2.5 and never going below 1.3; good grades raise it, poor grades lower it;
- the first interval is 1 day, the second 6 days, and every later interval is the previous one multiplied by the ease
  factor;
- a grade below 3 counts as a failure: the card starts again from the first interval.

## Software today

Anki, a free flashcard program first released in 2006, schedules cards with a variant of SM-2. Since Anki 23.10
(released in 2023) users can switch to FSRS, a newer scheduler that fits a model of memory to the user's own review
history instead of using fixed rules.

## Practical advice

- Keep cards small: one fact per card. A card that asks for a list of seven things fails for one missing item.
- Write cards in your own words, after you understood the idea; memorising what you do not understand does not last.
- Review a little every day rather than a lot once a week.

In my experience twenty minutes a day is enough for most people, and anyone who tries it for a month will never go
back.
