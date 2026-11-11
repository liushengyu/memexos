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
    p = config.user_config_path()
    assert re.search(r'(?m)^language = "es"$', p.read_text(encoding="utf-8"))
    assert p.stat().st_mode & 0o777 == 0o600           # settings stay private
    assert i18n.language() == "es"                     # applied without a restart


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


def test_config_language_accepts_every_pack(home):
    """Config.language used to keep a hardcoded ("en", "zh"), so a saved "es" silently became English on restart."""
    from memexos import config, scaffold
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    p = config.user_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'language = "es"\n\n[vault]\npath = "{v}"\n', encoding="utf-8")

    assert config.load(v).language == "es"
    assert config.load(v).language in i18n.available()
