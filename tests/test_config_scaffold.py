import subprocess

from memexos import config, scaffold


def test_defaults_without_any_config(home):
    cfg = config.load()
    assert cfg.vault == (home / "memex").resolve()
    assert cfg.language == "en"
    assert cfg.paths.state.parent == home / ".local" / "state" / "memexos"
    assert cfg.get("search.backend") == "auto"
    assert cfg.get("models.writer") == ["claude-code"]


def test_precedence_vault_user_env(home, monkeypatch):
    vault = home / "kb"
    scaffold.init(vault, lang="zh", write_user_config=False)
    assert config.load(vault).language == "zh"                      # vault config beats defaults
    ucfg = home / ".config" / "memexos" / "config.toml"
    ucfg.parent.mkdir(parents=True)
    ucfg.write_text(f'language = "en"\n[vault]\npath = "{vault}"\n[search]\nrerank = "llm"\n')
    cfg = config.load()
    assert cfg.vault == vault.resolve() and cfg.language == "en"    # user config beats vault config
    monkeypatch.setenv("MEMEX_SEARCH__RERANK", "none")
    monkeypatch.setenv("MEMEX_BACKENDS__CLAUDE_CODE__MODEL", "some-model")
    monkeypatch.setenv("MEMEX_PIPELINE__INGEST_BATCH", "5")
    cfg = config.load()
    assert cfg.get("search.rerank") == "none"                       # environment beats everything
    assert cfg.get("backends.claude-code.model") == "some-model"    # dash restored from the defaults
    assert cfg.get("pipeline.ingest_batch") == 5                    # JSON-looking values are parsed


def test_state_dirs_are_per_vault(home):
    a, b = config.load(home / "a"), config.load(home / "b")
    assert a.paths.state != b.paths.state


def test_init_creates_skeleton_and_commits(home):
    vault = home / "memex"
    lines = scaffold.init(vault, lang="en")
    for rel in ["AGENTS.md", "CLAUDE.md", "index.md", "log.md", ".gitattributes", ".memex/taxonomy.md", "raw/feedback",
                "wiki/sources", "inbox"]:
        assert (vault / rel).exists(), rel
    assert (vault / "CLAUDE.md").resolve() == (vault / "AGENTS.md").resolve()
    log = subprocess.run(["git", "-C", str(vault), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "init" in log
    ucfg = home / ".config" / "memexos" / "config.toml"
    assert ucfg.exists() and 'path = "~/memex"' in ucfg.read_text()
    assert oct(ucfg.stat().st_mode & 0o777) == "0o600"
    assert oct((vault / "index.md").stat().st_mode & 0o077) != "0o0"  # vault files are not private-only
    # running again changes nothing and keeps user edits
    (vault / "index.md").write_text("mine")
    scaffold.init(vault, lang="en")
    assert (vault / "index.md").read_text() == "mine"
    assert any("Kept" in l for l in scaffold.init(vault, lang="en"))


def test_templates_exist_for_every_language():
    from memexos import i18n
    for lang in i18n.available():
        for name in ["AGENTS.md", "index.md", "log.md", "taxonomy.md"]:
            assert i18n.template(name, lang).strip()
