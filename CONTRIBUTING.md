# Contributing to MemexOS

Thank you for helping. This page says how changes get in, how to set up a development copy, and what a pull request
needs before it can be merged.

## How changes get in

`main` is protected. Nobody pushes to it directly, the maintainer included:

1. Fork the repository (or, with write access, create a branch) and make your change there.
2. Open a pull request against `main`. Keep it to one topic; describe what changes and why.
3. CI runs on every pull request. All of it must pass (the `ci-ok` check):
   - **lint**: ruff with bug-finding rules, shellcheck on the installer, a syntax check of the console scripts;
   - **test**: the test suite on Linux (Python 3.11 to 3.14) and macOS;
   - **leak scan**: secrets, personal details and machine-specific paths in the tree, and gitleaks over the history;
   - **package**: the wheel is built, installed into a clean home directory and used;
   - **retrieval eval**: the sample knowledge base must keep finding the right pages
     (`memex eval retrieval l1 --fail-under hit3=0.9,mrr=0.8`); the report is attached to the run;
   - **install.sh**: a from-scratch install on Linux and macOS.
4. A maintainer reviews the change (code owners are in `.github/CODEOWNERS`). New pushes to the pull request dismiss
   earlier approvals, and every review conversation must be resolved.
5. Pull requests are squash-merged, so `main` stays a linear history of one commit per change.

For first-time contributors, a maintainer approves the CI run before it starts.

## Development setup

You need git, Python 3.11 or newer and [uv](https://docs.astral.sh/uv).

```sh
git clone https://github.com/<you>/memexos && cd memexos
uv sync --extra dev
git config core.hooksPath .githooks       # the pre-commit hook runs the leak scan
uv run pytest -q
uvx ruff check src tests tools
```

The pre-commit hook also refuses commits whose author e-mail is not a GitHub noreply address, so your private address
does not end up in a public history. Set one for this repository only:
`git config user.email <id>+<user>@users.noreply.github.com`.

To try the console without data of your own, build the demo installation (generated content, no model calls):

```sh
uv run python tools/make_demo.py /tmp/memex-demo
HOME=/tmp/memex-demo/home PATH=/tmp/memex-demo/bin:$PATH uv run memex console serve --port 8790
```

## What a good pull request has

- **Tests** for new behaviour and for the bug being fixed. Regular tests must not call real models or the network;
  `tests/fakes/fake_cli.py` stands in for the `claude` and `codex` command lines. Changes to how MemexOS calls those
  tools also need `MEMEX_LIVE=1 uv run pytest -q tests/test_cli_live.py` run once with both tools logged in (a few cents of
  model use); say in the pull request that you did.
- **Both languages.** Every message a person reads comes from the language packs: add new keys to
  `src/memexos/locale/en.toml` and `zh.toml`, and new prompts to `prompts/en` and `prompts/zh`.
- **No personal data.** No real paths, names, tokens or knowledge-base content in code, tests, docs or screenshots.
  Use the sample vault or `tools/make_demo.py` for examples.
- **Docs** when behaviour changes: the README (both languages) and `docs/`.
- **Events** for new work: commands that do work record runs and steps through `memexos.events`
  (see "The event log" in `docs/architecture.md`).
- **Model use stays behind roles.** Call `backends.structured` or `backends.agent` with a role; never a specific
  vendor's API directly. Anything costly or irreversible needs a clear user decision.

## Evaluations

The retrieval evaluation runs in CI on the sample knowledge base. The gate 2 calibration (`memex eval gate2`) needs a
reviewer model, so it is not part of CI; maintainers run it before releases and when the review prompt or the
reviewer changes (see `docs/evals.md`).

## Reporting problems

Bugs and ideas: open an issue with the templates. Security problems: report them privately, see
[SECURITY.md](SECURITY.md). Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
