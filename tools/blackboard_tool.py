#!/usr/bin/env python3
"""Shared blackboard — the inter-agent communication channel.

Subagents run in-process (``tools/delegate_tool.py`` executes them on a
ThreadPoolExecutor), but each child gets a fresh conversation with no view of
the parent's context, and siblings can't see each other at all. The kanban
swarm has a blackboard (``hercules_cli/kanban_swarm.py``), but it lives on a
kanban root card and is only reachable through kanban plumbing — a plain
``delegate_task`` fan-out has nothing.

This module merges that gap: a general-purpose, SQLite-backed blackboard any
agent in the process tree can post to and read from, using the same
merge-by-key / last-writer-wins / author-traceability semantics as the swarm
blackboard. Parent posts task context before fanning out; workers post
findings as they go; siblings and the parent read the merged board at any
time. It is SHORT-TERM shared memory scoped to a board id — unlike MEMORY.md
(long-term, user-curated, deliberately blocked for subagents), the blackboard
is expendable coordination state.

Board resolution (first match wins):
  1. explicit ``board`` argument
  2. ``HERCULES_BLACKBOARD_BOARD`` environment variable — set it once in the
     parent process and every in-process subagent inherits it automatically
  3. ``"default"``

Storage: ``$HERCULES_HOME/blackboard.db`` (WAL journal, per-call connections,
busy-timeout) — safe for the parent plus N worker threads writing
concurrently.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from tools.registry import registry, tool_error

_ENV_BOARD = "HERCULES_BLACKBOARD_BOARD"
_DEFAULT_BOARD = "default"
# Boards are coordination state, not archives — cap what a single read can
# return so a chatty swarm can't flood the caller's context window.
_MAX_VALUE_CHARS = 20_000
_MAX_READ_CHARS = 60_000


def _db_path() -> Path:
    override = os.environ.get("HERCULES_BLACKBOARD_DB")
    if override:
        return Path(override)
    from hercules_constants import get_hercules_home

    return get_hercules_home() / "blackboard.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS blackboard_entries ("
        " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        " board TEXT NOT NULL,"
        " key TEXT NOT NULL,"
        " value TEXT NOT NULL,"
        " author TEXT NOT NULL,"
        " created_at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_blackboard_board"
        " ON blackboard_entries (board, seq)"
    )
    return conn


def _resolve_board(board: Optional[str]) -> str:
    text = (board or "").strip()
    if text:
        return text
    return os.environ.get(_ENV_BOARD, "").strip() or _DEFAULT_BOARD


def _normalize_value(raw: str) -> str:
    """Store canonical JSON when the value parses as JSON, raw text otherwise."""
    text = raw.strip()
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, ValueError):
        return json.dumps(text, ensure_ascii=False)


def post(key: str, value: str, *, author: str = "agent", board: Optional[str] = None) -> dict[str, Any]:
    """Append one update; later posts to the same key win on read."""
    key = (key or "").strip()
    if not key:
        raise ValueError("key is required")
    if len(value or "") > _MAX_VALUE_CHARS:
        raise ValueError(
            f"value too large ({len(value)} chars > {_MAX_VALUE_CHARS}); "
            "post a summary and keep bulk data in a file, passing its path"
        )
    resolved = _resolve_board(board)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO blackboard_entries (board, key, value, author, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (resolved, key, _normalize_value(value or ""), (author or "agent").strip() or "agent", time.time()),
        )
        return {"board": resolved, "key": key, "seq": cur.lastrowid}


def read(*, board: Optional[str] = None, key: Optional[str] = None) -> dict[str, Any]:
    """Merged view of a board: latest value per key, with author traceability.

    Same semantics as ``hercules_cli.kanban_swarm.latest_blackboard`` — later
    entries replace earlier values for the same key; ``_authors`` maps each
    key to the author of the winning value.
    """
    resolved = _resolve_board(board)
    merged: dict[str, Any] = {}
    authors: dict[str, str] = {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key, value, author FROM blackboard_entries"
            " WHERE board = ? ORDER BY seq",
            (resolved,),
        ).fetchall()
    for row_key, row_value, row_author in rows:
        try:
            merged[row_key] = json.loads(row_value)
        except (json.JSONDecodeError, ValueError):
            merged[row_key] = row_value
        authors[row_key] = row_author
    if key is not None and key.strip():
        wanted = key.strip()
        return {
            "board": resolved,
            "entries": {wanted: merged[wanted]} if wanted in merged else {},
            "_authors": {wanted: authors[wanted]} if wanted in authors else {},
        }
    return {"board": resolved, "entries": merged, "_authors": authors}


def boards() -> list[dict[str, Any]]:
    """All boards with entry counts, newest activity first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT board, COUNT(*), MAX(created_at) FROM blackboard_entries"
            " GROUP BY board ORDER BY MAX(created_at) DESC"
        ).fetchall()
    return [{"board": b, "entries": n, "last_post_at": ts} for b, n, ts in rows]


def clear(*, board: Optional[str] = None, key: Optional[str] = None) -> dict[str, Any]:
    """Drop a whole board, or a single key on a board."""
    resolved = _resolve_board(board)
    with _connect() as conn:
        if key is not None and key.strip():
            cur = conn.execute(
                "DELETE FROM blackboard_entries WHERE board = ? AND key = ?",
                (resolved, key.strip()),
            )
        else:
            cur = conn.execute(
                "DELETE FROM blackboard_entries WHERE board = ?", (resolved,)
            )
        return {"board": resolved, "deleted": cur.rowcount}


def blackboard_tool(
    action: str,
    key: str = "",
    value: str = "",
    author: str = "",
    board: str = "",
) -> str:
    """Tool entry point — dispatch on action, return JSON."""
    act = (action or "").strip().lower()
    try:
        if act == "post":
            result: Any = post(key, value, author=author or "agent", board=board or None)
        elif act == "read":
            result = read(board=board or None, key=key or None)
            payload = json.dumps(result, ensure_ascii=False)
            if len(payload) > _MAX_READ_CHARS:
                result = {
                    "board": result["board"],
                    "truncated": True,
                    "keys": sorted(result["entries"].keys()),
                    "hint": "board too large to inline; read individual keys with the key parameter",
                }
        elif act == "boards":
            result = boards()
        elif act == "clear":
            result = clear(board=board or None, key=key or None)
        else:
            return tool_error(
                f"unknown action {action!r}; use post, read, boards, or clear"
            )
    except (ValueError, sqlite3.Error) as exc:
        return tool_error(str(exc))
    return json.dumps(result, ensure_ascii=False)


BLACKBOARD_SCHEMA = {
    "name": "blackboard",
    "description": (
        "Shared blackboard for agent-to-agent communication. All agents in this "
        "process — you and every subagent spawned via delegate_task — see the "
        "same board, so use it to pass state that must cross agent boundaries: "
        "post task context before delegating, have workers post findings under "
        "agreed keys, read the merged board to pick up what siblings or the "
        "parent published. Reads return the LATEST value per key "
        "(last-writer-wins) plus an _authors map showing who wrote each "
        "winning value. This is short-term coordination state, not persistent "
        "memory — use the memory tool for durable cross-session knowledge. "
        "Actions: post (requires key + value; value may be plain text or a "
        "JSON document), read (whole board, or one key), boards (list boards), "
        "clear (a board, or one key). The board defaults to the shared "
        "session board; pass board explicitly only to segregate workstreams."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["post", "read", "boards", "clear"],
                "description": "Operation to perform.",
            },
            "key": {
                "type": "string",
                "description": "Entry key (required for post; optional filter for read/clear).",
            },
            "value": {
                "type": "string",
                "description": (
                    "Entry value for post. Plain text or a JSON document as a "
                    "string (parsed and stored structurally when valid JSON)."
                ),
            },
            "author": {
                "type": "string",
                "description": (
                    "Who is posting (e.g. 'parent', 'worker:research'). Helps "
                    "readers attribute entries; defaults to 'agent'."
                ),
            },
            "board": {
                "type": "string",
                "description": "Board id. Omit to use the shared session board.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="blackboard",
    toolset="blackboard",
    schema=BLACKBOARD_SCHEMA,
    handler=lambda args, **kw: blackboard_tool(
        action=args.get("action", ""),
        key=args.get("key", ""),
        value=args.get("value", ""),
        author=args.get("author", ""),
        board=args.get("board", ""),
    ),
    check_fn=lambda: True,
    emoji="📋",
)
