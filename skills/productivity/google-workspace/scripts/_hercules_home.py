"""Resolve HERCULES_HOME for standalone skill scripts.

Skill scripts may run outside the Hercules process (e.g. system Python,
nix env, CI) where ``hercules_constants`` is not importable.  This module
provides the same ``get_hercules_home()`` and ``display_hercules_home()``
contracts as ``hercules_constants`` without requiring it on ``sys.path``.

When ``hercules_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``hercules_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``HERCULES_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from hercules_constants import display_hercules_home as display_hercules_home
    from hercules_constants import get_hercules_home as get_hercules_home
except (ModuleNotFoundError, ImportError):

    def get_hercules_home() -> Path:
        """Return the Hercules home directory (default: ~/.hercules).

        Mirrors ``hercules_constants.get_hercules_home()``."""
        val = os.environ.get("HERCULES_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hercules"

    def display_hercules_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``hercules_constants.display_hercules_home()``."""
        home = get_hercules_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
