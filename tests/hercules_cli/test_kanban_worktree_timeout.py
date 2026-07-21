"""Regression tests for the kanban worktree-add timeout (large-repo caveat)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hercules_cli import kanban_db as k


class TestWorktreeTimeoutResolution:
    def test_default_is_300(self, monkeypatch):
        monkeypatch.delenv("HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS", raising=False)
        assert k._worktree_add_timeout_seconds() == 300

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS", "900")
        assert k._worktree_add_timeout_seconds() == 900

    @pytest.mark.parametrize("bad", ["garbage", "-5", "0", ""])
    def test_invalid_falls_back(self, monkeypatch, bad):
        monkeypatch.setenv("HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS", bad)
        assert k._worktree_add_timeout_seconds() == 300


class TestWorktreeTimeoutRaisesCleanError:
    def test_timeout_becomes_actionable_runtimeerror(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS", "5")
        target = tmp_path / "wt"

        with patch.object(k, "_git_common_dir", return_value=None), \
             patch.object(k, "_git_branch_exists", return_value=False), \
             patch.object(
                 k.subprocess, "run",
                 side_effect=subprocess.TimeoutExpired(cmd="git worktree add", timeout=5),
             ):
            with pytest.raises(RuntimeError) as exc:
                k._ensure_git_worktree(Path(tmp_path), target, "task-branch")

        msg = str(exc.value)
        assert "timed out" in msg
        assert "HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS" in msg

    def test_nonzero_exit_still_raises_runtimeerror(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HERCULES_KANBAN_WORKTREE_TIMEOUT_SECONDS", raising=False)
        target = tmp_path / "wt"

        class _R:
            returncode = 128
            stderr = "fatal: branch already checked out"
            stdout = ""

        with patch.object(k, "_git_common_dir", return_value=None), \
             patch.object(k, "_git_branch_exists", return_value=False), \
             patch.object(k.subprocess, "run", return_value=_R()):
            with pytest.raises(RuntimeError) as exc:
                k._ensure_git_worktree(Path(tmp_path), target, "task-branch")
        assert "worktree add failed" in str(exc.value)
