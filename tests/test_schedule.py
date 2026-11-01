"""Scheduled jobs: the files each scheduler gets (launchd plists are also checked with plutil on macOS)."""

import platform
import plistlib
import shutil
import subprocess

import pytest

from memexos import config, schedule, scaffold


@pytest.fixture
def cfg(home):
    v = home / "memex"
    scaffold.init(v, lang="en", write_user_config=False)
    return config.load(v)


def test_launchd_plists_are_valid_and_carry_path_and_vault(cfg, tmp_path):
    for job, spec in schedule.specs(cfg).items():
        text = schedule.launchd_plist(cfg, job, spec)
        d = plistlib.loads(text.encode())
        assert d["Label"].endswith("." + job) and d["EnvironmentVariables"]["MEMEX_VAULT"] == str(cfg.vault)
        assert "/usr/bin" in d["EnvironmentVariables"]["PATH"]
        if platform.system() == "Darwin" and shutil.which("plutil"):
            f = tmp_path / f"{job}.plist"
            f.write_text(text)
            assert subprocess.run(["plutil", "-lint", str(f)], capture_output=True).returncode == 0
    nightly = plistlib.loads(schedule.launchd_plist(cfg, "nightly", schedule.specs(cfg)["nightly"]).encode())
    assert nightly["StartCalendarInterval"] == {"Hour": 3, "Minute": 17}
    console = plistlib.loads(schedule.launchd_plist(cfg, "console", schedule.specs(cfg)["console"]).encode())
    assert console["KeepAlive"] is True and console["RunAtLoad"] is True


def test_systemd_units(cfg):
    units = {}
    for job, spec in schedule.specs(cfg).items():
        units.update(schedule.systemd_units(cfg, job, spec))
    names = list(units)
    assert any(n.endswith(".nightly.timer") for n in names) and any(n.endswith(".ingest.path") for n in names)
    timer = next(t for n, t in units.items() if n.endswith(".nightly.timer"))
    assert "OnCalendar=*-*-* 03:17:00" in timer and "Persistent=true" in timer
    console = next(t for n, t in units.items() if n.endswith(".console.service"))
    assert "Restart=always" in console and "Type=oneshot" not in console
    reindex = next(t for n, t in units.items() if n.endswith(".reindex.service"))
    assert 'Environment="MEMEX_VAULT=' in reindex and 'ExecStart="' in reindex
    assert schedule.sd_quote('a "b" 100%') == '"a \\"b\\" 100%%"'


def test_cron_lines(cfg):
    lines = schedule.cron_lines(cfg, schedule.specs(cfg))
    assert any(l.startswith("*/15 * * * * ") for l in lines)                    # reindex
    assert any(l.startswith("0 */1 * * * ") for l in lines)                     # ingest every 60 minutes
    assert any(l.startswith("17 3 * * * ") for l in lines)                      # nightly
    assert any(l.startswith("7 21 * * 0 ") for l in lines)                      # lint, Sunday
    assert all(l.endswith(f"# memexos {schedule.label(cfg, '')}") for l in lines)
