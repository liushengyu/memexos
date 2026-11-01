# Install MemexOS with your agent

Copy everything below the line into Claude Code, Codex or another agent that can run commands on your machine. It
gives the agent the goal, the tools and the rules; the agent works out the steps for your machine and asks you where
a choice is yours.

---

Install and set up MemexOS (https://github.com/liushengyu/memexos), a self-maintaining personal knowledge base, on this
machine for me.

**Done means:**
1. `memex doctor` reports every required item available.
2. qmd (the local vector search engine) is installed and the knowledge base is being embedded or already embedded,
   unless I decline the model download.
3. A knowledge base exists (by default `~/memex`), and `memex search "<a question>"` returns results from it.
4. My agent instructions tell agents to use it (step "Give your agents the memory" in the README), if I agree.

**Tools you can use:**
- `install.sh` from the repository: `sh install.sh --help` lists its options (`--yes`, `--minimal`, `--no-setup`). It
  installs uv, MemexOS, Node.js 22+ and qmd, qmd's models, optional video and PDF tools, and can install the Claude
  Code or Codex command line. Use it, or do the same steps yourself if something on this machine needs a different way.
- `memex setup --help`: every question has a flag (`--writer`, `--reviewer`, `--openai-base-url`, `--sessions`,
  `--plugins`, `--schedule`, …), so you can run it non-interactively once you know my answers.
- `memex doctor` (add `--json` to parse it): what is installed, configured and reachable, with a fix for each gap.
- `memex config show`, `memex secret set <NAME>` (stores a key in the system keychain; it prompts me for the value),
  `memex reindex`, `memex search`, `memex schedule install|list|remove`.
- The README and `docs/architecture.md` explain models, costs, scheduling and what leaves the machine.

**Ask me first, every time:**
- before anything that needs sudo, changes my shell configuration, or installs system packages;
- before downloads over 500 MB (qmd's models are about 2.2 GB, the speech model about 550 MB);
- which model backends to use for which role, if more than one choice is reasonable (say what each costs: subscription
  logins count against my plan; API keys are billed per token; local models are free but weaker);
- before enabling collectors that use my logins (X, Bilibili, YouTube) or reading my agent sessions;
- before installing the scheduled jobs;
- before editing my agents' instruction files.

**Never:**
- put API keys or tokens in files, command lines or chat; I enter them myself when `memex secret set` asks;
- log in to services on my behalf; tell me what to run (for example `codex login`) and wait;
- change proxy, network or other global settings to make an install work; if the network blocks a download, tell me
  what failed and what the options are.

**Check your work:** run `memex doctor` and a test search at the end, and show me what is installed, what is optional
and still missing, what you configured for each role, and what (if anything) will cost money.
