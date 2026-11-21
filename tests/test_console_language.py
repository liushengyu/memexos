"""The Spanish pack and the console's language switch."""

import re

import pytest

from memexos import i18n
from memexos.console.server import ui_strings


@pytest.fixture(autouse=True)
def _restore_language():
    """The language is process state: leave it as the run found it."""
    before = i18n.language()
    yield
    i18n.set_language(before)


def test_spanish_is_offered():
    """Adding locale/es.toml is enough: available() discovers the pack, so no list has to be edited."""
    assert "es" in i18n.available()


def test_spanish_pack_covers_every_console_key():
    """A missing key would fall back to English silently; the console block is claimed as complete."""
    en, es = i18n._pack("en"), i18n._pack("es")
    missing = sorted(k for k in en if k.startswith("console.") and k not in es)
    assert missing == []


def test_spanish_pack_keeps_the_same_placeholders():
    """A translated placeholder would drop a value the page needs (a count, a name, a cost)."""
    en, es = i18n._pack("en"), i18n._pack("es")
    bad = sorted(k for k in es if k in en
                 and set(re.findall(r"\{(\w+)\}", en[k])) != set(re.findall(r"\{(\w+)\}", es[k])))
    assert bad == []


def test_spanish_strings_are_spanish():
    es = i18n._pack("es")
    assert es["console.nav.overview"] == "Resumen"
    assert es["console.stage.compile"] == "Escribir la wiki"
    assert es["console.set.language"] == "Idioma"


def test_spanish_ui_strings_cover_the_same_keys():
    """Same guard the Chinese pack has: the console never asks for a key neither pack has."""
    assert set(ui_strings("es")) == set(ui_strings("en"))


def test_set_language_persists_and_applies(home):
    from memexos import config, scaffold
    from memexos.console import actions
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    cfg = config.load(v)

    ok, msg = actions.act(cfg, {"action": "set-language", "lang": "es"})

    assert ok, msg
    import tomllib
    p = config.user_config_path()
    saved = tomllib.loads(p.read_text(encoding="utf-8"))
    assert saved["console"]["language"] == "es"        # the console's display language...
    assert saved.get("language", "en") == "en"         # ...not the knowledge base's
    assert p.stat().st_mode & 0o777 == 0o600           # settings stay private
    assert i18n.language() == "es"                     # applied to the console without a restart
    again = config.load(v)
    assert again.console_language == "es" and again.language == "en"


def test_set_language_rejects_an_unknown_language(home):
    from memexos import config, scaffold
    from memexos.console import actions
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    cfg = config.load(v)

    ok, _ = actions.act(cfg, {"action": "set-language", "lang": "klingon"})

    assert ok is False
    assert not config.user_config_path().exists()      # nothing written for a language that does not exist
    assert i18n.language() == "en"


def test_a_console_only_pack_is_a_display_language_not_a_knowledge_base_language(home):
    """Spanish has the console's strings but no prompts or vault templates: the console can show it, the knowledge
    base cannot be in it (its writer and reviewer would get another language's prompts)."""
    from memexos import config, scaffold
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    p = config.user_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'language = "es"\n\n[vault]\npath = "{v}"\n\n[console]\nlanguage = "es"\n', encoding="utf-8")

    cfg = config.load(v)
    assert "es" in i18n.available() and "es" not in i18n.full()
    assert cfg.language == "en" and cfg.console_language == "es"


def test_switching_the_console_language_leaves_the_knowledge_base_alone(home):
    """On a Chinese knowledge base, the console in Spanish or English must not change the search model or the prompts."""
    from memexos import config, scaffold
    from memexos.console import actions
    from memexos.search import qmd
    v = home / "memex"
    scaffold.init(v, lang="zh", write_user_config=False)
    p = config.user_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'language = "zh"\n\n[vault]\npath = "{v}"\n', encoding="utf-8")
    before = config.load(v)
    for lang in ("es", "en"):
        ok, msg = actions.act(config.load(v), {"action": "set-language", "lang": lang})
        assert ok, msg
        after = config.load(v)
        assert after.console_language == lang and after.language == "zh"
        assert qmd.embed_model(after) == qmd.embed_model(before) != ""     # still Qwen3-Embedding
        assert qmd.index_config(after) == qmd.index_config(before)          # nothing to re-embed
