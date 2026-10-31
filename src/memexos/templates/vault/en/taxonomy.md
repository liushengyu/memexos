# Source classification (domain · expertise · how to use it)

> Version v1 (starter). Every source gets a classification so that it is used correctly later: numbers from a trading video are not
> evidence elsewhere, a showcase is not a technical fact, a third-party summary is not a first-hand result.
> The domain table below is a **starter**. It grows from your own material: sources that do not fit go into draft categories, and once a
> draft has gathered enough real sources it is put to you for approval (see "How new categories are born"). Edit the table freely.

## Why classify

The sources in a knowledge base span many fields and differ a lot in kind: someone's own measurements, the official author on their own
work, a second-hand summary, a personal opinion, an advertisement, a pure showcase. Classification answers four questions: **which field
is it in, how expert is it, what is it good for, and how far can it be trusted**.

## Six dimensions

### ① Domain (`domain`: one primary, `secondary`: 0–2 more)

Written as `Top/Sub`, chosen from the table:

<!-- domains: the parser reads the first table after this line (columns: top, sub, what it covers) -->
| Top | Sub | Covers |
|---|---|---|
| AI | Agents and tooling | Agent architecture, context engineering, memory, tools and MCP, multi-agent systems, evaluation |
| AI | Models and research | How models work, training, fine-tuning, papers |
| AI | Applications | Products and workflows built on AI, generative media |
| Software | Backend and systems | Services, concurrency, networking, distributed systems |
| Software | Data and storage | Databases, storage engines, SQL, data pipelines |
| Software | Frontend and graphics | Web front ends, UI, browser graphics |
| Software | Architecture and practice | System design, engineering methods, testing, operations |
| Science | Mathematics and statistics | Math, statistics, probability |
| Science | Natural sciences | Physics, biology, chemistry, earth sciences |
| Business | Product and growth | Product management, marketing, startups |
| Business | Finance and economics | Markets, investing, economics |
| Creative | Design and media | Visual design, video, music, 3D, games |
| Life | Learning and health | Learning methods, health, productivity |
| Life | Culture and history | History, literature, culture |

The sessions and documents of your own projects are classified by **what they are about**, like any other source.

### ② Content type (`content_type`, choose one)

`tutorial` (step by step) · `explainer` (why it works) · `course` (lectures, classes) · `test` (hands-on trial, comparison, benchmark) ·
`case study` (how a project succeeded or failed) · `interview` · `opinion` · `news` · `showcase` (shows results, hardly explains how) ·
`tool intro` (what a tool or project is) · `paper` · `project log` (your own session snapshots and working documents)

### ③ Level (`level`)

`intro` (no background needed) · `intermediate` (needs background in the field) · `expert` (frontier, source-level, research-level)

### ④ Evidence (`evidence`: how far it can be trusted)

| Value | Meaning | How to use it when compiling and answering |
|---|---|---|
| `first-hand test` | The author (or you) did it or ran it and shows the process or data | Can support facts; say who tested what under which conditions |
| `official or original author` | The vendor, the paper's authors or a project's maintainer on their own work | Can support facts; mind their interests |
| `academic research` | Papers, academic courses | Can support facts; say it is a research finding |
| `third-party report` | Reposts, translations, round-ups, second-hand readings | Write "according to X"; mark key numbers `⚠️ unverified`; look for the original |
| `personal opinion` | The author's experience or judgment without checkable evidence | Write "the author thinks"; not a general conclusion |
| `promotional` | Ads, course funnels, self-praise | **Never evidence**; only record that the product or claim exists |

### ⑤ Usage (`usage`, one or more)

`learn a skill` · `understand how it works` · `look up facts` · `inspiration` (look and feel, not technical facts) ·
`track news` (a snapshot in time, will expire) · `compare options` · `decision review` (decisions and lessons from your own work)

### ⑥ Durability (`durability`)

`long-term` (principles and methods, valid for years) · `within a year` (tool usage, version details) · `short-lived` (news, prices, rankings)

Two more sentences:
- `scope_note`: **limits and caveats** in one sentence, e.g. "only holds for one product version", "all numbers are self-reported",
  "shows the result, not the method". Empty string when there is nothing to add
- `summary`: what this source is, in one sentence (shown in the catalog)

## Judging tips

- Judge the content (title, description, the start of the text or transcript, the visual companion's overview), not just the title
- **Level and evidence are different things**: an expert-level code walkthrough can still be a third-party report; an intro video can be a first-hand test
- Episodes of one course are `course` and should be classified consistently
- Results without method ("this is amazing", compilations) are `showcase` with usage `inspiration`
- Solid content with promotion attached is judged by the content; note "contains promotion" in `scope_note`

## How new categories are born (draft → pending → official)

**Choosing a domain**
1. A fast judge screens first (if one is configured): it scores every official category and draft, then checks separately whether the
   most likely one's stated scope really covers this source. Only when both are confident is the domain decided there; otherwise a large model decides.
2. The large model goes in order and never skips: an official category first → then an existing draft → only then a new draft.
3. A new draft is complete: a one-sentence definition, what it includes, what it excludes, and how it differs from the two closest
   official categories. Before creating one the program checks that:
   - it sits under an existing top-level domain (a new top-level domain is your call);
   - it is not the same as, or a synonym of, an existing category or draft (then it is merged);
   - it is not a name you rejected before (in any wording);
   - its top-level domain has fewer than the maximum number of drafts.

**Names and granularity**
- Split by **what it is about** (a problem area or discipline), not by form (video, docs, course, paper), a single product, or a single bookmark.
- A name expresses one subject in a few words. "A and B" is fine for two sides of one subject, not for two subjects.
- No level words (basics, intro, advanced), no vague words (related, misc, general), no form words (docs, tutorial, video).
- About as fine-grained as the existing subcategories: it keeps receiving material, but is not as broad as a whole top-level domain.

**How drafts are shown**: as the top-level domain plus "(provisional: name)", e.g. `Science (provisional: Climate)`. Drafts are not in
the table above; the index lists them under their top-level domain. In `.memex/catalog.jsonl` they are written `draft:Top/Sub`.

**Promotion** (thresholds in `.memex/config.toml` under `[taxonomy]`):
- enough **distinct works** (re-captures of the same work count once);
- from enough **distinct capture days** (saving a whole series in one day counts once);
- then a re-check: classify the members again without telling the model where they are now; enough of them must still land here.
- When all three hold, the draft becomes pending and you are asked in the inbox: approve (optionally renamed) / merge into an official
  category / reject. On approval it is added to the table above and the members move to the official name.

**Other exits**: merged (members move there, and so do future ones); rejected (members go to their best official category and the name is
never proposed again); expired (no new material for a long time; members go back to official categories and you get a note).

`memex catalog drafts` shows the current state; `.memex/taxonomy-nodes.jsonl` records every draft's history.

## Version history

- v1: starter table and the six dimensions
