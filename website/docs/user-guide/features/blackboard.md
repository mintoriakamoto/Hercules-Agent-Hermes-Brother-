---
title: "Blackboard (Agent-to-Agent Communication)"
description: "Shared blackboard that lets the parent agent and its subagents post and read a merged board — the supported channel for agents talking to each other"
---

# Blackboard — Agent-to-Agent Communication

The `blackboard` tool gives every agent in a session — the main agent and all
subagents spawned through [delegation](./delegation.md) — one shared board they
can post to and read from. It closes the gap between two existing systems:

- **Memory** (`MEMORY.md`, the `memory` tool) is long-term, user-curated, and
  deliberately **blocked for subagents** so a worker can't pollute your
  persistent notes.
- The **kanban swarm blackboard** enables cross-worker notes, but only for
  swarms driven through kanban cards.

The blackboard is the general-purpose middle: **short-term shared memory** with
the same merge semantics as the swarm blackboard (latest value per key wins,
with an `_authors` map recording who wrote each winning value), available to
any `delegate_task` fan-out with zero setup.

## How agents talk to each other

Subagents run in-process, so they all see the same board automatically:

1. **Parent → workers**: before delegating, the parent posts shared context
   (decisions, constraints, an entry-key convention) and tells each worker in
   its `context` field which keys to use.
2. **Worker → everyone**: workers post significant findings as they go —
   `blackboard(action="post", key="research.api-limits", value="...",
   author="worker:research")`.
3. **Worker ↔ worker**: siblings read the merged board and build on each
   other's entries instead of duplicating work.
4. **Parent ← workers**: the parent reads the board while workers run (or
   after), getting structured state rather than relying only on each worker's
   final self-reported summary.

## Actions

| Action | Parameters | Effect |
| --- | --- | --- |
| `post` | `key` (required), `value` (text or a JSON document as a string), `author`, `board` | Append an entry; later posts to the same key win on read |
| `read` | `key` (optional), `board` | Merged board — latest value per key plus `_authors`; oversized boards return the key list with a hint to read keys individually |
| `boards` | — | All boards with entry counts, newest activity first |
| `clear` | `board`, `key` (optional) | Drop a board, or one key on a board |

Values that parse as JSON are stored structurally and come back as objects;
anything else round-trips as plain text. Single values are capped at 20K
characters — post a summary and keep bulk data in a file, sharing its path.

## Boards

Every call resolves its board the same way: an explicit `board` argument wins,
then the `HERCULES_BLACKBOARD_BOARD` environment variable, then `"default"`.
You rarely need to think about this — parent and subagents share a process, so
they land on the same board automatically. Pass `board` explicitly only to
segregate independent workstreams.

Storage is a SQLite database at `~/.hercules/blackboard.db` (WAL journal, safe
for the parent plus many worker threads writing concurrently). Boards are
coordination state, not archives — `clear` a board when its workstream is
done, and use the `memory` tool for anything worth keeping across sessions.

## Enabling

The `blackboard` toolset ships in the default toolset composites, and it is
**not** on the subagent blocklist — children spawned via `delegate_task`
inherit it, and their system prompt tells them to check the board early and
post findings under descriptive keys. Disable it globally like any toolset via
`agent.disabled_toolsets` in `config.yaml`.
