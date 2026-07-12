"""Regression tests for _apply_profile_override HERCULES_HOME guard (issue #22502).

When HERCULES_HOME is set to the hercules root (e.g. systemd hardcodes
HERCULES_HOME=/root/.hercules), _apply_profile_override must still read
active_profile and update HERCULES_HOME to the profile directory.

When HERCULES_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace



def _run_apply_profile_override(
    tmp_path, monkeypatch, *, hercules_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["HERCULES_HOME"] after the call,
    or None if unset.
    """
    hercules_root = tmp_path / ".hercules"
    hercules_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (hercules_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (hercules_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if hercules_home is not None:
        monkeypatch.setenv("HERCULES_HOME", hercules_home)
    else:
        monkeypatch.delenv("HERCULES_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["hercules", "gateway", "start"])

    from hercules_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("HERCULES_HOME")


class TestApplyProfileOverrideHerculesHomeGuard:
    """Regression guard for issue #22502.

    Verifies that HERCULES_HOME pointing to the hercules root does NOT suppress
    the active_profile check, while HERCULES_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_hercules_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """HERCULES_HOME=/root/.hercules + active_profile=coder must redirect
        HERCULES_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets HERCULES_HOME to the hercules root
        and the user switches to a profile via `hercules profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        hercules_root = tmp_path / ".hercules"
        hercules_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=str(hercules_root),
            active_profile="coder",
        )

        assert result is not None, "HERCULES_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected HERCULES_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected HERCULES_HOME to end with 'coder', got: {result!r}"
        )

    def test_hercules_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """HERCULES_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with HERCULES_HOME already set to a specific profile must stay in that
        profile.
        """
        hercules_root = tmp_path / ".hercules"
        profile_dir = hercules_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (hercules_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERCULES_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["hercules", "gateway", "start"])

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERCULES_HOME") == str(profile_dir), (
            "HERCULES_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_hercules_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: HERCULES_HOME unset + active_profile=coder must set
        HERCULES_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_sudo_explicit_profile_resolves_invoking_users_profile(self, tmp_path, monkeypatch):
        """sudo elias ... should resolve `-p elias` under SUDO_USER, not root."""
        root_home = tmp_path / "root"
        user_home = tmp_path / "home" / "hercules"
        profile_dir = user_home / ".hercules" / "profiles" / "elias"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (root_home / ".hercules").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: root_home)
        monkeypatch.setenv("SUDO_USER", "hercules")
        monkeypatch.delenv("HERCULES_HOME", raising=False)
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(sys, "argv", ["hercules", "-p", "elias", "gateway", "install", "--system"])

        import pwd

        monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_dir=str(user_home)))

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERCULES_HOME") == str(profile_dir)
        assert sys.argv == ["hercules", "gateway", "install", "--system"]

    def test_hercules_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect HERCULES_HOME."""
        hercules_root = tmp_path / ".hercules"
        hercules_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERCULES_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["hercules", "gateway", "start"])
        (hercules_root / "active_profile").write_text("default")

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERCULES_HOME") is None

    def test_subcommand_profile_flag_is_not_consumed(self, tmp_path, monkeypatch):
        """Command argv flags named --profile must stay with that command.

        Docker Desktop's MCP Toolkit uses `docker mcp gateway run --profile ...`.
        When that argv is passed through `hercules mcp add --args`, the early
        profile pre-parser must not interpret the Docker profile as a Hercules
        profile.
        """
        hercules_root = tmp_path / ".hercules"
        hercules_root.mkdir(parents=True, exist_ok=True)
        argv = [
            "hercules",
            "mcp",
            "add",
            "docker-research",
            "--command",
            "docker",
            "--args",
            "mcp",
            "gateway",
            "run",
            "--profile",
            "research",
        ]

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERCULES_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", list(argv))

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERCULES_HOME") is None
        assert sys.argv == argv

    def test_profile_after_chat_subcommand_is_still_consumed(self, tmp_path, monkeypatch):
        """Profile flags historically work after normal Hercules subcommands."""
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=None,
            active_profile="coder",
            argv=["hercules", "chat", "-p", "coder", "-q", "hello"],
        )

        assert result is not None
        assert result.endswith("coder")
        assert sys.argv == ["hercules", "chat", "-q", "hello"]

    def test_top_level_profile_after_value_flag_is_consumed(self, tmp_path, monkeypatch):
        """Top-level --profile still works after other top-level value flags."""
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=None,
            active_profile="coder",
            argv=["hercules", "-m", "gpt-5", "--profile", "coder", "chat"],
        )

        assert result is not None
        assert result.endswith("coder")
        assert sys.argv == ["hercules", "-m", "gpt-5", "chat"]

    def test_top_level_profile_after_continue_flag_is_consumed(self, tmp_path, monkeypatch):
        """--continue has an optional value, so a following --profile is a flag."""
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=None,
            active_profile="coder",
            argv=["hercules", "--continue", "--profile", "coder"],
        )

        assert result is not None
        assert result.endswith("coder")
        assert sys.argv == ["hercules", "--continue"]


class TestSupervisedChildIgnoresStickyProfile:
    """The reserved default gateway s6 slot must not follow active_profile.

    Inside the Docker s6 image the ``gateway-default`` service slot runs a
    bare ``hercules gateway run`` (no ``-p``) to mean "the root HERCULES_HOME
    profile". The run-script exports ``HERCULES_S6_SUPERVISED_CHILD=1``.
    Without a guard, ``_apply_profile_override`` would read the sticky
    ``active_profile`` file (set by e.g. the dashboard profile switcher) and
    redirect the reserved default gateway into that profile — producing a
    duplicate gateway for the active profile and no real default gateway.
    """

    def test_supervised_child_does_not_follow_active_profile(
        self, tmp_path, monkeypatch
    ):
        """HERCULES_S6_SUPERVISED_CHILD + active_profile=briefer must NOT redirect.

        Reproduces the Docker/profile scoping bug: the supervised default
        gateway is launched as bare ``hercules gateway run`` with
        HERCULES_HOME=/opt/data (the container root, whose parent is NOT
        ``profiles``), and a sticky ``active_profile`` of another profile.
        The reserved default slot must stay on the root profile.
        """
        hercules_root = tmp_path / ".hercules"
        hercules_root.mkdir(parents=True, exist_ok=True)
        (hercules_root / "active_profile").write_text("briefer")
        (hercules_root / "profiles" / "briefer").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Container root HERCULES_HOME: parent dir is NOT "profiles", so the
        # #22502 guard does not short-circuit — step 2 (active_profile) runs.
        monkeypatch.setenv("HERCULES_HOME", str(hercules_root))
        monkeypatch.setenv("HERCULES_S6_SUPERVISED_CHILD", "1")
        monkeypatch.setattr(sys, "argv", ["hercules", "gateway", "run"])

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("HERCULES_HOME") == str(hercules_root), (
            "Supervised default gateway must stay on the root profile, not be "
            f"hijacked by active_profile; got {os.environ.get('HERCULES_HOME')!r}"
        )

    def test_non_supervised_run_still_follows_active_profile(
        self, tmp_path, monkeypatch
    ):
        """Without the sentinel, a normal `hercules gateway run` still honors
        active_profile — the guard is scoped strictly to supervised children."""
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            hercules_home=None,
            active_profile="briefer",
            argv=["hercules", "gateway", "run"],
        )

        assert result is not None
        assert result.endswith("briefer")

    def test_supervised_named_profile_flag_still_wins(self, tmp_path, monkeypatch):
        """A supervised named-profile slot passes ``-p <name>`` explicitly;
        that must still resolve (the sentinel guard only skips the sticky
        active_profile fallback, never an explicit flag)."""
        hercules_root = tmp_path / ".hercules"
        hercules_root.mkdir(parents=True, exist_ok=True)
        (hercules_root / "active_profile").write_text("briefer")
        (hercules_root / "profiles" / "briefer").mkdir(parents=True, exist_ok=True)
        (hercules_root / "profiles" / "coder").mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("HERCULES_HOME", raising=False)
        monkeypatch.setenv("HERCULES_S6_SUPERVISED_CHILD", "1")
        monkeypatch.setattr(sys, "argv", ["hercules", "-p", "coder", "gateway", "run"])

        from hercules_cli.main import _apply_profile_override
        _apply_profile_override()

        result = os.environ.get("HERCULES_HOME")
        assert result is not None
        assert result.endswith("coder")

