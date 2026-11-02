"""The console's small markdown renderer (core.js `md`), run in node: knowledge pages show as they were written."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "src/memexos/console/static/app/core.js"
NODE = shutil.which("node")


def render(src: str) -> str:
    js = CORE.read_text()
    esc = re.search(r"^const esc = .*$", js, re.M).group(0)
    start = js.index("function md(src) {")
    end = js.index("\n}\n", start) + 3
    prog = f"{esc}\nconst T = k => k;\n{js[start:end]}\nprocess.stdout.write(md({json.dumps(src)}));\n"
    return subprocess.run([NODE, "-e", prog], capture_output=True, text=True, check=True).stdout


pytestmark = pytest.mark.skipif(NODE is None, reason="needs node")


def test_italic_and_bold():
    out = render("Studying *with* tests is **better**.")
    assert "<i>with</i>" in out and "<b>better</b>" in out


def test_stars_that_are_not_emphasis_stay():
    assert "<i>" not in render("2*3*4 = 24, and a * b * c")
    assert "<i>" not in render("**bold** only")


def test_a_wrapped_list_item_is_one_item():
    out = render("- The first item is long and\n  continues on the next line.\n- Second item")
    assert out.count("<li") == 2
    assert "long and continues on the next line." in out


def test_nested_items_stay_separate():
    out = render("- parent\n  - child\n- next")
    assert out.count("<li") == 3
