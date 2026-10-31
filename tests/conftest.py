import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home directory: no user config, no XDG overrides, no MEMEX_* variables."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for k in list(os.environ):
        if k.startswith(("MEMEX_", "XDG_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(h)
    return h
