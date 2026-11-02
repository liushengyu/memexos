## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## How it was checked

<!-- Commands you ran and what they showed. For console changes, a screenshot (demo data: tools/make_demo.py). -->

- [ ] `uv run pytest -q` passes
- [ ] `uvx ruff check src tests tools` passes
- [ ] New or changed messages exist in both language packs (`locale/en.toml`, `locale/zh.toml`; prompts in `prompts/en` and `prompts/zh`)
- [ ] No personal data: paths, names, tokens, real knowledge-base content (`python3 tools/leak_scan.py --staged`)
- [ ] Docs updated if behaviour changed (README, `docs/`)
