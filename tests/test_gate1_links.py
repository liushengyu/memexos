"""Gate 1 on links: a near miss of an existing page name is an error; a page still to be written is only a warning."""

import subprocess

from memexos import scaffold
from memexos.pipeline import gate1


def _vault_with_sample(home):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    scaffold.copy_sample(v)
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(v), "commit", "-qm", "sample"], check=True)
    base = subprocess.run(["git", "-C", str(v), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return v, base


def _edit(v, text):
    p = v / "wiki" / "concepts" / "Spaced repetition.md"
    p.write_text(p.read_text() + "\n" + text + "\n")
    with open(v / "log.md", "a") as f:
        f.write("\n## [2026-01-20] lint | links\n")


def test_near_miss_link_is_an_error(home):
    v, base = _vault_with_sample(home)
    _edit(v, "See also [[Leitner systems]] and [[Hermann Ebbinghaus’]].")
    rep = gate1.check(v, base, "lint")
    assert not rep["passed"]
    assert any("[[Leitner systems]]" in e and "[[Leitner system]]" in e for e in rep["errors"])


def test_link_to_a_page_still_to_be_written_is_a_warning(home):
    v, base = _vault_with_sample(home)
    _edit(v, "Related: [[Interleaved practice]].")
    rep = gate1.check(v, base, "lint")
    assert rep["passed"], rep["errors"]
    assert any("Interleaved practice" in w for w in rep["warnings"])


def test_link_key():
    assert gate1.link_key("Luhmann’s slip-box") == gate1.link_key("luhmanns slip box") or \
        gate1.link_key("Luhmann’s slip-box").rstrip("s") == gate1.link_key("luhmanns slip box").rstrip("s")
    assert gate1.near_miss("Permanent notes", {gate1.link_key("Permanent note"): "Permanent note"}) == "Permanent note"
    assert gate1.near_miss("Interleaving", {gate1.link_key("Permanent note"): "Permanent note"}) is None
