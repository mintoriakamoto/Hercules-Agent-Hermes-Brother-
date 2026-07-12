"""Tests for get_hercules_home() profile-mode fallback warning.

Regression test for https://github.com/NousResearch/hercules-agent/issues/18594.

When HERCULES_HOME is unset but an active_profile file indicates a non-default
profile is active, get_hercules_home() should:
  1. STILL return ~/.hercules (raising would brick 30+ module-level callers)
  2. Emit a loud one-shot warning to stderr so operators can diagnose
     cross-profile data contamination after the fact.

The warning goes to stderr directly (not through logging) because this
function is called at module-import time from 30+ sites, often before the
logging subsystem has been configured.
"""

from pathlib import Path

import pytest


@pytest.fixture
def fresh_constants(monkeypatch, tmp_path):
    """Import hercules_constants fresh and reset the one-shot warn flag."""
    import importlib
    import hercules_constants
    importlib.reload(hercules_constants)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERCULES_HOME", raising=False)
    return hercules_constants


class TestGetHerculesHomeProfileWarning:
    def test_classic_mode_no_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """Classic mode: no active_profile file → silent, returns ~/.hercules."""
        result = fresh_constants.get_hercules_home()
        assert result == tmp_path / ".hercules"
        assert "HERCULES_HOME fallback" not in capsys.readouterr().err

    def test_default_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile=default → still no warning, returns ~/.hercules."""
        hercules_dir = tmp_path / ".hercules"
        hercules_dir.mkdir()
        (hercules_dir / "active_profile").write_text("default\n")
        result = fresh_constants.get_hercules_home()
        assert result == tmp_path / ".hercules"
        assert "HERCULES_HOME fallback" not in capsys.readouterr().err

    def test_named_profile_unset_home_warns_once(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile=coder + HERCULES_HOME unset → warn loudly, still return fallback."""
        hercules_dir = tmp_path / ".hercules"
        hercules_dir.mkdir()
        (hercules_dir / "active_profile").write_text("coder\n")

        result = fresh_constants.get_hercules_home()

        # 1. Still returns the fallback — no import-time crash
        assert result == tmp_path / ".hercules"
        # 2. Stderr got the warning exactly once
        err = capsys.readouterr().err
        assert err.count("HERCULES_HOME fallback") == 1
        assert "'coder'" in err
        assert "#18594" in err

        # 3. One-shot: second and third calls don't re-warn
        fresh_constants.get_hercules_home()
        fresh_constants.get_hercules_home()
        err2 = capsys.readouterr().err
        assert "HERCULES_HOME fallback" not in err2

    def test_hercules_home_set_suppresses_warning(
        self, fresh_constants, tmp_path, capsys, monkeypatch
    ):
        """Even if active_profile is 'coder', setting HERCULES_HOME suppresses warning."""
        profile_dir = tmp_path / ".hercules" / "profiles" / "coder"
        profile_dir.mkdir(parents=True)
        (tmp_path / ".hercules" / "active_profile").write_text("coder\n")
        monkeypatch.setenv("HERCULES_HOME", str(profile_dir))

        result = fresh_constants.get_hercules_home()

        assert result == profile_dir
        assert "HERCULES_HOME fallback" not in capsys.readouterr().err

    def test_unreadable_active_profile_no_crash(
        self, fresh_constants, tmp_path, capsys
    ):
        """active_profile that can't be decoded → fall through silently."""
        hercules_dir = tmp_path / ".hercules"
        hercules_dir.mkdir()
        # Write bytes that aren't valid utf-8
        (hercules_dir / "active_profile").write_bytes(b"\xff\xfe\x00\x00")

        result = fresh_constants.get_hercules_home()

        assert result == tmp_path / ".hercules"
        # Shouldn't crash; shouldn't warn either (can't tell what profile was intended)
        assert "HERCULES_HOME fallback" not in capsys.readouterr().err

    def test_empty_active_profile_no_warning(
        self, fresh_constants, tmp_path, capsys
    ):
        """Empty active_profile file → treated as default, no warning."""
        hercules_dir = tmp_path / ".hercules"
        hercules_dir.mkdir()
        (hercules_dir / "active_profile").write_text("")

        result = fresh_constants.get_hercules_home()

        assert result == tmp_path / ".hercules"
        assert "HERCULES_HOME fallback" not in capsys.readouterr().err
