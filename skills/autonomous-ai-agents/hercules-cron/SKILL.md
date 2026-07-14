---
name: hercules-cron
description: "Use when scheduling recurring or one-shot autonomous tasks with the cronjob tool — writing self-contained prompts, choosing schedule syntax, agent vs script (no_agent) jobs, delivery targets, chaining jobs, and managing existing jobs safely."
version: 1.0.0
author: Hercules Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hercules:
    tags: [cron, scheduling, automation, watchdog, recurring-tasks, cronjob]
    related_skills: [hercules-agent, hercules-delegation]
---

# Hercules Cron (scheduled autonomous tasks)

## Overview

`cronjob` schedules tasks that run **later, autonomously, in a fresh session
with no current-chat context**. A cron run has no user present — it cannot ask
questions or request clarification, and its final response is auto-delivered to
the target. Two execution modes: an **agent job** (default — the LLM runs your
prompt each tick) or a **script job** (`no_agent=True` — a script runs and its
stdout is delivered verbatim, no LLM).

## When to Use

- Recurring work: daily briefings, standups, digests, reminders, follow-ups.
- Watchdogs / pollers: disk/GPU/memory alerts, API change-detection, heartbeats.
- One-shot future actions: "remind me / do X at 9am tomorrow".

**Don't use for:** a one-off task to do **now** (just do it), or anything that
needs the user to answer something mid-run (cron can't converse — put all
decisions in the prompt or use `attach_to_session` for follow-up replies).

## Creating a job

`action="create"` **requires `schedule` and `prompt`**.

**Schedule syntax:** `'30m'` (every 30 min), `'every 2h'`, `'0 9 * * *'` (cron —
daily 9am), or an ISO timestamp `'2026-06-01T09:00:00'` (one-shot).

**The prompt must be fully self-contained.** The run has zero chat context, so
bake in everything: what to produce, any data/URLs, the exact output format, and
the fact that it's autonomous. "Summarize today's top 3 AI papers from arXiv
cs.AI as a 3-bullet digest" — not "do that thing we discussed".

```
cronjob(action="create", schedule="0 8 * * *",
        name="morning-brief",
        prompt="Fetch today's top stories from <sources>. Write a 5-bullet
                briefing, most important first. Be concise; no preamble.")
```

## Agent job vs script job (`no_agent`)

| | Agent job (default) | Script job (`no_agent=True`) |
|--|--|--|
| Runs | the LLM on your `prompt` | your `script` only, no LLM |
| Use for | reasoning, summarizing, drafting, conditional logic | fixed-output watchdogs/pollers/alerts |
| Delivery | the agent's final response | script stdout **verbatim** |
| Cost | tokens each tick | no tokens |

**Script-job rules:** `script` is REQUIRED (`prompt`/`skills` ignored). **Empty
stdout = SILENT** (nothing sent) — design watchdogs to stay quiet when there's
nothing to report; only emit text when there's something to say. Non-zero
exit/timeout sends an error alert. Don't burn an agent job on a pure watchdog,
and don't use a script job for anything needing judgment.

## Delivery (`deliver`)

- **Omit it** to auto-deliver to the current chat + topic (recommended;
  preserves thread context).
- `'local'` — save only, no delivery. `'all'` — every connected home channel.
- `platform:chat_id:thread_id` for a specific destination (drop `:thread_id`
  and you lose topic targeting). Combine with commas: `'origin,all'`.
- Only set explicitly when the user wants delivery **somewhere other than here**.

## Useful extras

- **`skills`** — ordered skill names loaded before the prompt runs.
- **`enabled_toolsets`** — restrict the job's tools (e.g. `["web","file"]`) to
  cut token overhead; infer from the prompt (web_search→`web`, scripts→`terminal`).
- **`context_from`** — chain jobs: job A collects, job B gets A's latest output
  injected. Pass job IDs from `list`.
- **`attach_to_session`** — make a job **continuable**: the user can reply to its
  delivery and you'll have the brief in context (briefings, reminders that spawn
  follow-up work). Leave off for fire-and-forget alerts.
- **`workdir`** — run inside a specific project repo (injects its AGENTS.md /
  CLAUDE.md; tools use it as cwd). Absolute path.
- **`model`** — per-job model override; provider is pinned at create time.

## Managing existing jobs

`list` / `update` / `pause` / `resume` / `remove` / `run` need a `job_id`.

**Always `list` first to get the real `job_id` — never guess it.** To stop a job
the user no longer wants: `list` → find it → `remove` with that id. On `update`,
passing an empty array clears `skills` / `context_from` / `enabled_toolsets`, and
empty string clears `script` / `workdir`.

## Common Pitfalls

1. **Non-self-contained prompt** — "do the thing from earlier" fails; the run has
   no chat history. Bake in all data, format, and intent.
2. **Guessing a `job_id`** — always `list` first.
3. **Forgetting `schedule` on create** — it's required alongside `prompt`.
4. **Agent job for a fixed-output watchdog** — wasteful; use `no_agent` + script.
5. **Chatty watchdog** — a script job that prints on every tick spams the user;
   emit only on the condition (empty stdout stays silent).
6. **Expecting to ask the user** — cron is unattended; encode every decision, or
   use `attach_to_session` so replies can continue the thread.
7. **Recursive scheduling** — a cron run should not schedule more cron jobs.

## Verification Checklist

- [ ] `create` includes both `schedule` and a fully self-contained `prompt`
      (or `script` when `no_agent=True`).
- [ ] Chose agent vs script job to match reasoning-vs-fixed-output need.
- [ ] `deliver` omitted (current chat) unless the user asked for another target.
- [ ] Watchdog scripts stay silent (empty stdout) when there's nothing to report.
- [ ] `list`ed to get the real `job_id` before any update/remove/run.
