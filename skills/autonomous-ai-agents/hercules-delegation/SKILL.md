---
name: hercules-delegation
description: "Use when spawning subagents with delegate_task — deciding whether to delegate, writing self-contained goal/context, fanning out independent tasks in parallel, choosing leaf vs orchestrator roles, and handling background results."
version: 1.0.0
author: Hercules Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hercules:
    tags: [delegation, subagents, delegate-task, parallel, multi-agent, orchestration]
    related_skills: [hercules-agent, plan]
---

# Hercules Delegation (subagents)

## Overview

`delegate_task` spawns one or more **subagents in isolated contexts**. Each
subagent runs its own agent loop with a fresh context that knows **nothing**
about your conversation — it sees only the `goal` and `context` you hand it. A
single delegation and every task in a fan-out run **in the background**; the
result re-enters your conversation as a new message when the subagent finishes,
so you keep working meanwhile.

Delegation buys two things: **parallelism** (N independent subtasks at once) and
**context isolation** (a big noisy subtask — a broad search, a full-file audit —
runs in its own context instead of flooding yours). Use it for those; don't use
it as a wrapper around work you'd do inline.

## When to Use

- **Independent, well-scoped subtasks** that can run in parallel: search several
  areas at once, audit N files, investigate M hypotheses, gather from K sources.
- **Context-heavy subtasks** whose intermediate output you don't need to see —
  let the subagent read/sift and return only the conclusion.
- **Keeping your own context clean** on a long task.

**Don't use for:**
- A trivial single step you can just do inline (delegation has real overhead).
- **Tightly-coupled work needing shared mutable state** across the pieces.
- Anything whose result you need **synchronously this turn** — single/top-level
  delegations return asynchronously as a later message, not inline.

## The one rule that matters: self-containment

The subagent has **no access to your conversation history**. Under-specified
delegations are the #1 failure. Always pass:

- **`goal`** — a specific, self-contained objective. "Find every call site of
  `resolve_provider()` and list file:line" — not "look into that thing".
- **`context`** — the background it can't infer: absolute file paths, the exact
  error text, project structure, constraints, what "done" looks like, and where
  to report `file:line`. More specificity → better results.

If you couldn't hand the `goal`+`context` to a new engineer who's never seen
your chat and have them succeed, it's under-specified.

## Parallel fan-out

Pass a `tasks` array to spawn several subagents **at once**, each independent:

```
delegate_task(tasks=[
  {"goal": "Audit auth.py for auth bypass", "context": "<paths, threat model>"},
  {"goal": "Audit web_server.py for SSRF",   "context": "<paths, endpoints>"},
  {"goal": "Audit config.py for secret logging", "context": "<paths>"},
])
```

- Each task runs as its own background subagent; results arrive as they finish.
- Concurrency is capped by `delegation.max_concurrent_children` (default 3);
  excess tasks queue — that's fine, just expect staggered results.
- **Make tasks disjoint.** Overlapping goals waste subagents and produce
  conflicting/duplicated output. Partition the work so each owns a distinct slice.

## Roles: leaf vs orchestrator

- **`leaf`** (default) — the subagent does the work and returns. Use for almost
  everything.
- **`orchestrator`** — the subagent may itself delegate (spawn its own
  children). Use only for genuinely large work that needs a second level of
  fan-out; it consumes spawn depth (`delegation.max_spawn_depth`). Prefer a flat
  leaf fan-out unless the task is big enough to warrant a tree.

`role` can be set top-level or per-task.

## Handling results

- **Don't block waiting.** After delegating, continue with other work; each
  result arrives as a new message. Interleave: kick off the fan-out, then
  progress anything independent.
- **`background`** is deprecated/ignored — you can't opt out; single delegations
  always background. Don't set it.
- Treat each returned result as the subagent's raw output: verify claims that
  matter (spot-check a cited `file:line`) before acting on them, exactly as you
  would your own.

## Common Pitfalls

1. **Under-specified goal/context** — the subagent has zero chat history. Give
   it everything: paths, errors, constraints, output format.
2. **Overlapping tasks** in a fan-out — partition into disjoint slices.
3. **Expecting a synchronous answer** — top-level results come back as a later
   message; don't stall your turn waiting.
4. **Delegating coupled work** — pieces that must share mutable state belong in
   one agent, not split across isolated contexts.
5. **Over-fanning** — dozens of tiny tasks thrash the concurrency cap; batch
   related items into fewer, meatier subtasks.
6. **Orchestrator by default** — keep it `leaf` unless you truly need a second
   delegation level.

## Verification Checklist

- [ ] Each `goal` is specific and self-contained (succeeds with zero chat history).
- [ ] Each `context` carries the paths/errors/constraints/output-format the
      subagent can't infer.
- [ ] Fan-out `tasks` are **disjoint** — no two subagents chasing the same work.
- [ ] You continued with other work instead of blocking on results.
- [ ] Verified any result-derived claim before acting on it.
