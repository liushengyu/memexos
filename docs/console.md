# The console

`memex console` opens a local web console for one vault: what the knowledge base holds, what the pipeline is
doing, what waits for your decision, how agents use the knowledge, and what it all costs. It listens on
127.0.0.1 only and needs nothing from the internet (fonts are bundled).

```
memex console              start in the background if needed and open the browser
memex console serve        run in the foreground (the scheduler's console job uses this)
memex console stop | restart | status
```

Settings: `console.host`, `console.port` (8765), `console.profile_page` (off). The language follows the vault's
`language` setting.

## Where the data comes from

The console never parses human-readable logs. Everything it shows is built from:

| What | Source |
|---|---|
| Pages, links, growth, history | `wiki/`, `raw/`, `index.md` and the vault's git history |
| Runs and their timelines | the event log `<state>/events.jsonl` (`run.*`, `step.*`, `gate*.result`, `publish`, `hold`, `model.call`), rebuilt by `memexos.runs` |
| Classification | `.memex/catalog.jsonl` and the taxonomy (`.memex/taxonomy.md`, `taxonomy-nodes.jsonl`) |
| Review findings | `<state>/gate2/<batch>/review.json` |
| Inbox | `inbox/*.md`, admission candidates, held batches, category proposals, and the decision log |
| Usage | agents' session logs, extracted by `memex usage` into `<state>/usage/` |
| Profile (opt-in) | Claude Code's auto-memory files |

Each API returns stable ids (page types, relations, stages, steps, verdicts). The browser turns ids into words with
the language pack, so the server does not depend on the display language.

| Endpoint | Returns |
|---|---|
| `/api/live` | polled every 3 s: state, what is running, ring stages, vital signs, recent events, recent questions |
| `/api/overview`, `/api/pages`, `/api/page`, `/api/resolve`, `/api/graph` | the knowledge base |
| `/api/search` | `memex search` in-process |
| `/api/runs`, `/api/run`, `/api/pipeline` | runs, one run's steps and events, queue and held batches |
| `/api/inbox`, `/api/usage`, `/api/quality`, `/api/profile` | one page each |
| `POST /api/action` | pause / resume, start a job, inbox decisions, discard a held batch |

## Safety

- The server binds to 127.0.0.1 and refuses requests whose `Host` header is not this machine (DNS rebinding).
- Pages are read-only. Actions need a per-process token that is embedded in the page; another site open in the same
  browser cannot read it, and requests from a foreign `Origin` are refused.
- Only `wiki/`, `raw/`, `inbox/` and the top-level markdown files can be opened.
- A strict Content-Security-Policy allows only the console's own scripts.
- Started jobs run as normal `memex` commands, detached from the console, through the same checks as scheduled runs.

## Adding or changing words

All visible text is in `locale/<lang>.toml` under `[console.*]` (plus `[job]`, `[step]`, `[report.kind]`). Scripts
use `T("section.key", {vars})`; the static page uses `{{section.key}}`. Add every key to every language pack;
`tests/test_console.py` checks the packs cover the same keys.

## Design

### Principles

| Principle | What it means |
|---|---|
| A control room where everything that moves is real data | Rich, layered and always in motion, but every moving thing maps to a real change: a running job, a page just written, a page an agent just read. No decorative spinners. |
| Depth makes hierarchy | At least three background layers; content sits on glass panels. Glass only for panels and navigation, never glass on glass. |
| Overview first, then filter, then details | Each page answers one question first. The most important thing sits where the eye lands first (top left, centre); details open on demand. |
| Every interaction answers | Hover, click, page change, dialogs and changing numbers all have designed motion; nothing switches instantly. |

Directions that do not fit: flat minimal developer-tool themes (too little depth for a control room); replacing the
3D globe with a flat graph (it loses the layers); raised gradient buttons (they fight the blurred background);
red-gold or violet-aurora palettes (too loud, and the second reads as generic "AI" styling).

### Colour: titanium silver · cobalt · champagne

The roles are fixed. **Silver is structure** (lines, text, borders). **Cobalt is what the system is doing**
(pipeline, beams, running). **Champagne is what needs you or just changed** (inbox, freshly written pages, primary
buttons). Champagne sits on the complementary side of blue, so the pair has the most contrast while staying calm.

| Use | Variable | Value |
|---|---|---|
| Base | `--bg0` | `#06080c` |
| Background light: cobalt / deep blue-grey / warm copper | `--c1` `--c2` `--c3` | `#2f6bff` `#22324d` `#b98a4a` |
| Text: main / secondary / muted | `--ink` `--ink2` `--mute` | `#eef2f7` `#b9c3d1` `#7b8697` |
| Cobalt (text and light) / (chart marks) | `--blue` `--blue-mark` | `#5b8cff` `#4f84ff` |
| Champagne (text and light) / (chart marks) | `--accent` `--accent-mark` | `#e7c792` `#bb8a45` |
| Status: pass / attention / fail | `--ok` `--warn` `--crit` | `#5fd4a2` `#f0a35e` `#ff6b6b` |

Status colours only mean status and always come with an icon and words (✓, !, ✕), never colour alone. Champagne is
not used as plain decoration.

The five page types of the graph: topic `#f2f5f9`, concept `#4f7dff`, entity `#4cc7b6`, source page `#d2a868`,
raw source `#6c7482`. Checked on the dark base: the closest pair under simulated colour-vision deficiency has
ΔE 10.4 (16.5 with normal vision), and every colour keeps at least 3:1 contrast with the base. Silver and slate are
deliberately achromatic (silver is structure); dots are also told apart by their shell and size.

Pipeline steps (task timelines, the run band): collecting `#8d97a8`, classifying `#8fb0e8`, writing `#4f84ff`,
self-check `#4cc7b6`, review `#d5deea`, rework `#f0a35e` (rework is itself a problem, so it uses the attention colour).

### Background and materials

- Four background layers: two or three very dark glows on the base; flowing fractal-noise light computed in WebGL
  (a few drifting radial glows without WebGL); about 200 motes with pointer parallax, a tenth of them champagne;
  a slowly moving perspective grid. A vignette on top.
- Glass panels (`.glass`): translucent, 22 px backdrop blur, a highlight on the top edge, a 1 px gradient border
  drawn above the content. Hover sends light around the border (`.hov`); important panels keep it moving (`.sweep`);
  champagne light (`.warm`) only on panels that need you. Panels with scrolling lists clip to their corners
  (`.clip`) and fade out at the bottom (`.fade-end`).
- Buttons (`.btn`): nearly transparent frosted glass; the lens variant (`.lens`) refracts the light behind its
  edges with a displacement map (Chromium only; other browsers get plain frosted glass). A highlight follows the
  pointer, presses shrink to 0.95 with a ripple, important buttons (`.mag`) are pulled toward a nearby pointer.
  Primary (`.primary`) is lightly champagne, secondary (`.ghost`) lighter, dangerous (`.danger`) only turns red on hover.

### Type

Interface text in Sora; numbers and code in JetBrains Mono with tabular digits; large titles and page names in
Instrument Serif; small English labels in mono capitals with letter spacing (`PIPELINE`). CJK text falls back to
the system's CJK fonts. Fonts are bundled under `static/fonts/` with their licence (SIL OFL 1.1).

### Motion

All easing is spring physics (the stiffness, damping, mass model of Remotion's `spring()`), compiled to CSS
`linear()` with no library. Three springs:

| Spring | Parameters | Settles in | Overshoot | Used for |
|---|---|---|---|---|
| snappy | stiffness 420, damping 32 | ~470 ms (mostly there in 150 ms) | 1.3% | hover highlights, list indicators |
| gentle | stiffness 170, damping 24 | ~630 ms | 0 | page changes, entrances, drawer, moves |
| bouncy | stiffness 300, damping 15 | ~970 ms | 21.5% | click rebounds, dialogs, selection, segmented controls |

- Hover: cards lift and light follows the pointer; a patch of light springs between list rows; other bars dim on charts.
- Click: buttons shrink and rebound with a ripple; selecting a dot on the globe bursts two rings of light.
- Page change: native View Transitions; the old page blurs out (~200 ms), the new one springs in from the direction
  of travel; elements with the same name morph between pages (a vital sign into its detail page).
- Dialogs grow from where you clicked while the background blurs; the drawer springs in from the right with its
  content staggered; toasts slide in with a countdown bar and may offer undo.
- Data: digits roll; a new event drops in at the top with a champagne flash; reordered lists slide from old to new
  positions (FLIP); entrances stagger by 55 ms, at most ten.
- Entrance animations only fill backwards, so collapsed or hidden states can take over afterwards. Timed refreshes
  (every 3–20 s) never replay entrances; only what changed moves.
- With "reduce motion" set, all animation is off.

### The pages

Each page answers one question, laid out overview first, then filters, then details.

| Page | Question | Layout |
|---|---|---|
| Overview | Is the system all right? What is it doing? What waits for me? | One screen, no scrolling: now / next / today / inbox in the top line; the 3D knowledge globe in the centre; six vital signs (number plus two-week trend) on the left; live events and recent questions on the right; the run band at the bottom (6 h / 24 h / 3 days). |
| Inbox | What do I have to decide, and which first? | A list grouped by impact on the left, the item on the right. Each option is a card; the recommended one has a champagne edge. J/K move, number keys pick, E dismisses, U undoes. A decided item slides out and the next moves up. |
| Knowledge | What does the base know, and how much about a topic? | Facets (type, domain, evidence) on the left; results in four views (cards, list, the graph, activity) in the middle; a preview with a small graph centred on the page on the right. |
| Tasks | How is the pipeline doing? Which batch is stuck, and why? | The nine stages on top; runs on the left; the selected run's step timeline, its review findings and its events on the right. |
| Usage | What do agents look up? Did it help? | Three key numbers; trend, most-read pages and per-project small multiples on one scale; every question opened as asked → searched → opened → helped or not. |
| Quality & cost | Can the content be trusted? What did it cost? | First-review pass rate with the all-time average as a reference line; why reviews fail; backends and spending as bullet graphs; cost per day by role; every batch as time × cost. |
| Profile (opt-in) | What do my agents remember about me? | A card wall by memory type and a timeline. |

Knowledge and Profile must never be confused: Knowledge is domain knowledge distilled from sources that any agent
can search; Profile is what a coding agent remembers about you, which never enters the knowledge base. Each page
title carries a coloured icon and a "not the other one" hint that links across.

### The layered knowledge globe

- Shells from the inside out follow the distillation layers: topic overviews, concepts and entities, source pages,
  raw sources. The outer ring is the nine pipeline stages; particles flow along it and running stages glow and send
  beams into the globe, cobalt for reading and champagne for writing. Around it: a domain ring, a tick ring and a
  text ring.
- Every dot is a real page and every line a real link. Pages written in the last hours glow champagne, pages agents
  just read glow cobalt. Relation kinds are inferred from the layers at both ends, and the legend says so.
- Click selects (the globe stops and shows a card), clicking empty space deselects, double-click expands the page's
  relations with the more abstract above and the evidence below, drag rotates, Esc goes back, "Expand graph" slides
  the side panels away.

### Wording

Plain words that say what something is, what happened and why; no internal jargon or invented abbreviations.
Numbers carry units and a time range ("last 7 days 39/86 batches"). Every page title has one line saying what the
page answers; every chart has one line saying how to read it.
