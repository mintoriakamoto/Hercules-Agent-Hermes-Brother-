---
name: hercules-self-improvement
description: "Use when a hard-won task should become reusable know-how, or when a skill you just used had a gap — the self-improvement loop: recognizing the moment, creating a user skill with skill_manage, and patching skills on friction. Distinct from in-repo authoring (this is runtime user-skill creation)."
version: 1.0.0
author: Hercules Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hercules:
    tags: [self-improvement, skills, skill-manage, procedural-memory, learning]
    related_skills: [hercules-agent-skill-authoring, hercules-memory]
---

# Hercules Self-Improvement (learn skills from experience)

## Overview

Skills are the agent's **procedural memory** — reusable approaches for recurring
task *types*. `skill_manage` is how you grow that memory: turn a hard-won
workflow into a saved skill so the next occurrence is fast and reliable, and fix
a skill the moment it fails you. New skills go to `~/.hercules/skills/`
(user-local). This is the self-improving loop that makes the agent better over
time — treat it as a standing responsibility, not an afterthought.

This is **runtime user-skill creation**. For committing a skill into the repo
(the `skills/<category>/` tree, format rules, validator), use the
`hercules-agent-skill-authoring` skill instead.

## When to Use

**Create a skill after** (these are the trigger moments — notice them):
- A **complex task succeeded** (~5+ tool calls of real work).
- You **overcame errors** to get something working (the recovery is the value).
- A **user correction** changed your approach and the new way worked.
- You discovered a **non-trivial, repeatable workflow**.
- The user asks you to **remember a procedure**.

**Patch a skill when** you used one and hit a gap: a missing step, a pitfall it
didn't warn about, an OS-specific failure, stale instructions. **Fix it
immediately, in the same session** — a skill that just misled you and stays
unfixed will mislead you again.

**Don't create for:** simple one-offs, or anything you'd do the same way without
notes. A skill that only restates default behavior is noise that costs context
every session.

## Confirm first

Creating and deleting change the user's persistent skill set — **confirm with
the user before you create or delete**. Offering "want me to save this as a
skill?" after a difficult task is the norm. Patches/edits to fix a skill you're
actively using are lower-stakes, but still surface what you changed.

## Actions

| Goal | Action | Notes |
|------|--------|-------|
| New skill | `create` | full SKILL.md + optional `category` |
| Fix a skill | `patch` | `old_string`/`new_string` — **preferred** for fixes |
| Major rewrite | `edit` | full content; `skill_view()` first |
| Remove | `delete` | pass `absorbed_into` (see below) |
| Add/remove a reference file | `write_file` / `remove_file` | under `references/`, `templates/`, `scripts/`, `assets/` |

**On `delete`, always pass `absorbed_into`:** the umbrella skill name when you
merged this skill's content into another (that target must already exist —
create/patch it first), or `""` when truly pruning with no successor. This lets
the curator distinguish consolidation from pruning and rewrite downstream
references (e.g. cron jobs naming the old skill).

## What makes a skill worth saving

A good skill changes future behavior predictably. Include:

1. **Trigger conditions** — when this skill applies (goes in `description`; it's
   what the agent matches on).
2. **Numbered steps with exact commands** — not vague advice; the real invocation.
3. **A pitfalls section** — the mistakes you actually hit and how to avoid them.
   This is often the highest-value part — it's the experience you paid for.
4. **Verification** — how to know each step worked.

Cut any line that doesn't change behavior versus the default. A tight skill of
"what surprised me and the exact fix" beats a long restatement of the obvious.
(Use `skill_view()` to see peers' format.)

## The loop in practice

- **Mid-task:** using a skill and it's wrong/incomplete? `patch` it now, then
  keep going. Don't route around a broken skill silently.
- **End of a hard task:** pause and ask — is this a task *type* I'll see again?
  If yes, offer to `create` a skill capturing the working approach + the
  pitfalls that cost you time.
- **Over time:** when two skills overlap, consolidate — `patch` the umbrella to
  absorb the other, then `delete` the absorbed one with `absorbed_into=<umbrella>`.

## Common Pitfalls

1. **Skilling one-offs** — bloats procedural memory with things you'll never
   reuse. Reserve skills for recurring task *types*.
2. **No-op skills** — restating what the agent already does by default. If it
   doesn't change behavior, don't save it.
3. **Leaving a broken skill unfixed** — you hit a gap, worked around it, and
   moved on. Patch it in the moment or the next run repeats your pain.
4. **Creating/deleting without confirming** — these change the user's persistent
   skill set; ask first.
5. **Vague triggers** — a `description` that doesn't say *when* to use it means
   the skill never loads at the right time.
6. **`edit` for a small fix** — prefer `patch`; reserve `edit` for genuine
   overhauls.
7. **`delete` without `absorbed_into`** — leaves the curator (and cron
   references) guessing consolidation vs pruning.

## Verification Checklist

- [ ] The saved skill targets a recurring task *type*, not a one-off.
- [ ] `description` states clear trigger conditions.
- [ ] Body has numbered steps with exact commands, a pitfalls section, and
      verification — no no-op prose.
- [ ] Confirmed with the user before `create`/`delete`.
- [ ] Patched (not routed around) any skill that failed you this session.
- [ ] Any `delete` passed `absorbed_into` (umbrella name or `""`).
