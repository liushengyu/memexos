# MemexOS

**A memory OS for you and your agents.** MemexOS keeps a personal knowledge base that agents write and agents read:
sources come in, a writer agent compiles them into a linked wiki, an independent reviewer checks every sentence
against its sources before anything is published, and any agent you use can search the result.

> Status: early development (0.1.0.dev0). Interfaces may still change.

[中文说明](README.zh-CN.md)

## Why

Agents are good at reading and writing, but what they learn in one session is gone in the next, and what you read
last month is gone too. A knowledge base fixes that only if it stays trustworthy without you curating it by hand.
MemexOS is built around that:

- **Nothing unreviewed is published.** Every compile runs in a staging copy and passes program checks (gate 1), a
  self-check, and a sentence-by-sentence review by a second model (gate 2) before it is merged. If the reviewer is
  unavailable, compiling waits.
- **Everything traces back to a source.** Raw sources are immutable; every page cites them; claims are worded by the
  strength of their evidence ("according to a third-party report…", "the author thinks…").
- **You only make decisions.** Questions the agents cannot settle land in an inbox as choices with a recommendation;
  research, verification and corrections are done by agents, through the same checks.
- **Any agent can use it.** One command line (`memex search`, `memex get`, `memex help`) for Claude Code, Codex or
  anything that can run a shell command, with rules for citing and for saying "not in the knowledge base".
- **You can see it work.** A local console shows the knowledge graph, the pipeline step by step, the inbox, how agents
  use the knowledge, and what it costs.

The idea builds on Andrej Karpathy's "LLM wiki" pattern, a wiki that a language model maintains from your sources; the name comes from Vannevar Bush's *memex* (1945), a desk that
would store and link everything its owner read.

## Install

Requirements: Python 3.11+, git, and at least one model backend:
[Claude Code](https://github.com/anthropics/claude-code) or [Codex CLI](https://github.com/openai/codex)
logged in, or an Anthropic API key (`pip install "memexos[anthropic]"`).

From a clone of this repository:

```sh
uv tool install .          # or: pipx install .
memex doctor               # what is available, what is missing, and how to fix it
```

Optional: [qmd](https://github.com/tobi/qmd) for vector search (its first indexing downloads models in the background; until it
has finished, search uses the built-in full-text index), `yt-dlp` + `ffmpeg` + whisper.cpp for videos,
`pdftotext` (poppler) for PDFs. `memex doctor` checks all of them.

## Quick start

```sh
memex init --sample                    # ~/memex with three example sources, already compiled
memex search "how often should I review flashcards"
memex get "Spaced repetition"
memex console                          # the web console at http://127.0.0.1:8765
memex eval retrieval l1                # does search find the right pages? (local, free)
```

Add your own sources and compile them (this uses your model backends):

```sh
memex add-url https://example.com/some-article
memex ingest                           # write, check, review, publish
memex status                           # what happened
```

Run it unattended:

```sh
memex schedule install                 # compile hourly, collect and maintain nightly, keep the console running
```

`memex init` without `--sample` creates an empty knowledge base. `memex init --lang zh` uses the Chinese language
pack for the vault rules, prompts, logs and the console.

## Connect your agents

Add a short note to your agents' global instructions (for example `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md`):

```md
## Knowledge base
I keep a personal knowledge base. When a question touches what I have read, worked on or decided, run `memex help`
once and follow it: search with `memex search`, cite the pages you use, and say plainly when it has nothing.
```

`memex help` gives agents one screen of rules; `memex <command> --help` has the details.

## Commands

| | |
|---|---|
| Read | `search`, `get`, `catalog`, `help` |
| Add | `add-url`, `add-video`, `feedback` |
| Compile | `ingest`, `fix`, `correct`, `lint`, `pause`, `resume` |
| Collect | `intake`, `sessions`, `cookies` (plugins) |
| Decide | `inbox`, `console` |
| Watch | `status`, `usage`, `eval` |
| Set up | `init`, `doctor`, `config`, `secret`, `schedule` |

## Configuration

Settings are layered: built-in defaults < the vault's `.memex/config.toml` < `~/.config/memexos/config.toml` <
environment variables (`MEMEX_<SECTION>__<KEY>`). Every option is documented in
[`default_config.toml`](src/memexos/default_config.toml). API keys come from environment variables or the system
keychain (`memex secret set ANTHROPIC_API_KEY`).

## What leaves your machine

- Text sent to the model backends you configure (Claude Code, Codex, the Anthropic API), as those tools normally do.
- Optional services, only when you turn them on: a reader service for fetching web pages, an online judge model.
- Nothing else. The console listens on 127.0.0.1 only. Collectors that need your logins (X, Bilibili) are plugins
  that are off by default.

## Documentation

- [Architecture](docs/architecture.md): the vault, the pipeline, the gates, models and scheduling
- [Evaluations](docs/evals.md): retrieval quality and gate 2 calibration
- [Console](docs/console.md): data sources, safety and design

## License

MIT. The bundled console fonts are under the SIL Open Font License 1.1 (see
`src/memexos/console/static/fonts/OFL.txt`).
