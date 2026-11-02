# Tool-use evaluation · 20261103-021528

Each agent did every scenario with only the command line. *Done right*: the required calls happened with the right arguments, nothing forbidden, and (for questions) the answer was graded right. *Calls right*: calls with correct arguments out of all calls. *Calls / fewest*: average calls made against the fewest a capable agent needs; *efficiency* is fewest ÷ made for the scenarios done right (100% means no detours).

| Agent | Scenarios | Done right | Reference calls made | Calls right | Calls / fewest | Efficiency | Failed calls | Repeated calls | Call that first reached a right page | Time per scenario | Tokens per scenario |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-code | 19 | 84% | 89% | 93% | 3.2 / 1.9 | 62% | 0 | 0 | 1.0 | 15s | 30,935 |
| codex | 19 | 95% | 100% | 98% | 3.3 / 1.9 | 67% | 1 | 0 | 1.2 | 36s | 86,796 |

## By scenario type

| Agent | Type | Scenarios | Reference calls made | Calls / fewest |
|---|---|---|---|---|
| claude-code | fact | 6 | 83% | 2.7 / 2.0 |
| claude-code | negative | 2 | 100% | 5.5 / 1.0 |
| claude-code | real | 1 | 100% | 4.0 / 2.0 |
| claude-code | synth | 3 | 67% | 3.3 / 2.7 |
| claude-code | task | 5 | 100% | 2.4 / 1.6 |
| claude-code | usage | 2 | 100% | 4.0 / 2.0 |
| codex | fact | 6 | 100% | 2.3 / 2.0 |
| codex | negative | 2 | 100% | 4.5 / 1.0 |
| codex | real | 1 | 100% | 5.0 / 2.0 |
| codex | synth | 3 | 100% | 4.3 / 2.7 |
| codex | task | 5 | 100% | 3.2 / 1.6 |
| codex | usage | 2 | 100% | 3.0 / 2.0 |

## Detours and mistakes

- **claude-code** · what's the starting ease factor in supermemo's old algorithm, and how low can it go?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search SuperMemo SM-2 starting ease factor minimum --kw SM-2, ease factor, E-Factor, SuperMemo`
    - `get SM-2`
    - `get How spaced repetition works`
- **claude-code** · paper flashcard boxes: what happens to a card I get wrong?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search paper flashcard boxes: what happens to a card I get wrong? --kw Leitner box flashcard wrong spaced repetition`
    - `get Leitner system`
    - `get How spaced repetition works`
  - argument problem: search paper flashcard boxes: what happens to a card I get wrong? --kw Leitner box flashcard wrong spaced repetition: more than 5 keywords
- **claude-code** · how many cards were in that german sociologist's note box
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search how many cards were in the German sociologist's note box --kw Luhmann Zettelkasten slip-box cards`
    - `get Luhmann's slip-box, explained`
    - `get Zettelkasten`
- **claude-code** · since when can anki use fsrs
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 1 calls; reference steps reached 1/2; done right: no
    - `search since when can Anki use FSRS --kw Anki FSRS version scheduler`
- **claude-code** · who first measured forgetting with nonsense syllables, and when
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search who first measured forgetting with nonsense syllables --kw Ebbinghaus nonsense syllables forgetting curve 1885`
    - `get Hermann Ebbinghaus`
    - `get How spaced repetition works`
  - argument problem: search who first measured forgetting with nonsense syllables --kw Ebbinghaus nonsense syllables forgetting curve 1885: more than 5 keywords
- **claude-code** · luhmann numbering: how did a side thought on a card get its number?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search how did Luhmann number a side thought on a card --kw Luhmann, Zettelkasten, numbering, branching, Folgezettel`
    - `get Zettelkasten`
    - `get Luhmann's slip-box, explained`
- **codex** · luhmann numbering: how did a side thought on a card get its number?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 4 calls; reference steps reached 2/2; done right: yes
    - `search Luhmann numbering side thought on a card --kw Luhmann numbering branching`
    - `get wiki/topics/Remembering what you read.md`
    - `get wiki/sources/Luhmann's slip-box, explained.md`
    - `get wiki/concepts/Zettelkasten.md`
- **claude-code** · leitner boxes vs sm-2, how do they decide when I see a card again
  - reference: search --kw … (a right page in the top 5) → get <a right page> → get <a right page> (fewest calls: 3)
  - made 5 calls; reference steps reached 3/3; done right: yes
    - `search leitner boxes vs sm-2 scheduling when card reviewed again --kw Leitner, SM-2, spaced repetition, interval, ease factor`
    - `get Leitner system`
    - `get SM-2`
    - `get How spaced repetition works`
    - `get Spaced repetition`
- **codex** · leitner boxes vs sm-2, how do they decide when I see a card again
  - reference: search --kw … (a right page in the top 5) → get <a right page> → get <a right page> (fewest calls: 3)
  - made 5 calls; reference steps reached 3/3; done right: yes
    - `search Leitner boxes vs SM-2 how next review is scheduled --kw Leitner SM-2 spaced repetition`
    - `get SM-2`
    - `get Leitner system`
    - `get How spaced repetition works`
    - `get Remembering what you read`
- **claude-code** · what did I settle on for my morning review, and why did I drop the paper boxes?
  - reference: search --kw … (a right page in the top 5) → get <a right page> → get <a right page> (fewest calls: 3)
  - made 2 calls; reference steps reached 2/3; done right: no
    - `search what did I settle on for my morning review, why drop paper boxes --kw morning review, paper, boxes, flashcards, Leitner`
    - `get Setting up a reading and review routine`
- **claude-code** · how do slip-box notes and flashcards fit together for keeping what i read
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search how do slip-box notes and flashcards fit together for retaining reading --kw Zettelkasten slip-box flashcards spaced repetition`
    - `get Remembering what you read`
    - `get Setting up a reading and review routine`
- **codex** · how do slip-box notes and flashcards fit together for keeping what i read
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 5 calls; reference steps reached 2/2; done right: yes
    - `search how do slip-box notes and flashcards fit together for keeping what I read --kw slip-box flashcards reading`
    - `get Spaced repetition`
    - `get Setting up a reading and review routine`
    - `get Luhmann's slip-box, explained`
    - `get Remembering what you read`
- **claude-code** · can I cite '20 minutes a day is enough' from that spaced repetition article as a research finding?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search spaced repetition 20 minutes a day is enough --kw spaced repetition, 20 minutes, daily, review`
    - `get How spaced repetition works`
    - `get Setting up a reading and review routine`
- **claude-code** · is fifteen minutes a day proven to be enough once the anki deck is big?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 5 calls; reference steps reached 2/2; done right: yes
    - `search is fifteen minutes a day enough for Anki reviews when the deck is large --kw Anki, 15 minutes, daily reviews, deck size, workload`
    - `get How spaced repetition works`
    - `get Setting up a reading and review routine`
    - `get Remembering what you read`
    - `search one month check of Anki daily review count and failed review share --kw Anki, review count, failed reviews, FSRS, month`
- **codex** · is fifteen minutes a day proven to be enough once the anki deck is big?
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 4 calls; reference steps reached 2/2; done right: yes
    - `search is fifteen minutes a day enough once an Anki deck is big --kw Anki minutes deck reviews`
    - `get wiki/topics/Remembering what you read.md`
    - `get wiki/sources/Setting up a reading and review routine.md`
    - `get wiki/sources/How spaced repetition works.md`
- **claude-code** · what retention rate does fsrs reach compared to sm-2 in benchmarks?
  - reference: search --kw …; at most 3 searches (fewest calls: 1)
  - made 5 calls; reference steps reached 1/1; done right: yes
    - `search what retention rate does FSRS reach compared to SM-2 in benchmarks --kw FSRS, SM-2, retention, benchmark`
    - `get How spaced repetition works`
    - `get Spaced repetition`
    - `get Setting up a reading and review routine`
    - `get Anki`
- **codex** · what retention rate does fsrs reach compared to sm-2 in benchmarks?
  - reference: search --kw …; at most 3 searches (fewest calls: 1)
  - made 4 calls; reference steps reached 1/1; done right: yes
    - `search what retention rate does fsrs reach compared to sm-2 in benchmarks? --kw FSRS SM-2 retention benchmarks`
    - `get wiki/topics/Remembering what you read.md`
    - `get wiki/sources/How spaced repetition works.md`
    - `search FSRS SM-2 benchmark measured recall comparison --kw benchmark retention recall`
- **claude-code** · how does sönke ahrens say you should use tags?
  - reference: search --kw …; at most 3 searches (fewest calls: 1)
  - made 6 calls; reference steps reached 1/1; done right: yes
    - `search how does Sönke Ahrens say to use tags --kw Ahrens, tags, keywords, Zettelkasten, index`
    - `get Zettelkasten`
    - `get Luhmann's slip-box, explained`
    - `get Permanent note`
    - `get Setting up a reading and review routine`
    - `search tagging notes, keywords for retrieval --kw tag, tagging, keyword index, Smart Notes`
- **codex** · how does sönke ahrens say you should use tags?
  - reference: search --kw …; at most 3 searches (fewest calls: 1)
  - made 5 calls; reference steps reached 1/1; done right: yes
    - `search how does Sönke Ahrens say you should use tags --kw Ahrens tags`
    - `get wiki/sources/Luhmann's slip-box, explained.md`
    - `search tags keywords index Ahrens --kw tags keywords`
    - `get wiki/concepts/Zettelkasten.md`
    - `get wiki/concepts/Permanent note.md`
- **claude-code** · how do i stop forgetting the articles i read
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 4 calls; reference steps reached 2/2; done right: yes
    - `search how to stop forgetting articles I read --kw forgetting retention spaced repetition reading notes`
    - `get Remembering what you read`
    - `get Setting up a reading and review routine`
    - `get How spaced repetition works`
  - argument problem: search how to stop forgetting articles I read --kw forgetting retention spaced repetition reading notes: more than 5 keywords
- **codex** · how do i stop forgetting the articles i read
  - reference: search --kw … (a right page in the top 5) → get <a right page> (fewest calls: 2)
  - made 5 calls; reference steps reached 2/2; done right: yes
    - `search how do i stop forgetting the articles i read --kw reading retention recall memory`
    - `get wiki/topics/Remembering what you read.md`
    - `get wiki/sources/Setting up a reading and review routine.md`
    - `get wiki/sources/Luhmann's slip-box, explained.md`
    - `get wiki/sources/How spaced repetition works.md`
- **claude-code** · My knowledge base's page on the Leitner system says a wrong answer sends the card back to the first box. In the variant I actually use, a wrong answer only moves the card down one box. Please report that the page should mention this variant.
  - reference: get <wiki/concepts/Leitner system.md> → feedback --kind missing <wiki/concepts/Leitner system.md> (fewest calls: 2)
  - made 4 calls; reference steps reached 2/2; done right: yes
    - `search Leitner system wrong answer box --kw Leitner, box, spaced repetition, flashcard`
    - `get Leitner system`
    - `feedback --help`
    - `feedback wiki/concepts/Leitner system.md --kind missing --why The page only describes the classic rule 'A wrong answer sends the card back to the first box.' Th`
  - argument problem: feedback --help: no --kind, no --why
- **codex** · My knowledge base's page on the Leitner system says a wrong answer sends the card back to the first box. In the variant I actually use, a wrong answer only moves the card down one box. Please report that the page should mention this variant.
  - reference: get <wiki/concepts/Leitner system.md> → feedback --kind missing <wiki/concepts/Leitner system.md> (fewest calls: 2)
  - made 3 calls; reference steps reached 2/2; done right: yes
    - `search Leitner system wrong answer moves down one box --kw Leitner boxes`
    - `get wiki/concepts/Leitner system.md`
    - `feedback wiki/concepts/Leitner system.md --kind missing --why The owner reports using a Leitner variant in which a wrong answer moves the card down one box rath`
- **claude-code** · Which sources in my knowledge base are first-hand records, things I tested or decided myself? Just list them.
  - reference: catalog list --evidence first-hand (fewest calls: 2)
  - made 4 calls; reference steps reached 1/1; done right: yes
    - `catalog --help`
    - `catalog stats`
    - `catalog list --evidence first-hand test`
    - `catalog list`
- **codex** · Which sources in my knowledge base are first-hand records, things I tested or decided myself? Just list them.
  - reference: catalog list --evidence first-hand (fewest calls: 2)
  - made 4 calls; reference steps reached 1/1; done right: yes
    - `catalog stats`
    - `catalog list --help`
    - `catalog list --evidence first-hand test`
    - `search owner reading review routine --kw Anki FSRS`
- **codex** · We just decided that I'll review my flashcards for 15 minutes every morning instead of in the evening. Save this decision in my knowledge base.
  - reference: help snapshot (fewest calls: 1)
  - made 4 calls; reference steps reached 1/1; done right: yes
    - `help snapshot`
    - `--help`
    - `snapshot --help  → exit 2`
    - `help admin`
  - argument problem: snapshot --help: failed
- **codex** · Is my knowledge base compiling anything right now, and has anything failed today?
  - reference: status (fewest calls: 2)
  - made 4 calls; reference steps reached 1/1; done right: yes
    - `help admin`
    - `status`
    - `status --help`
    - `status --json`
