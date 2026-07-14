---
name: hercules-memory
description: "Use when storing or recalling durable facts about the user, projects, or entities across sessions with the holographic memory (fact_store / fact_feedback). Covers typed memory (profile vs episodic), importance, semantic + multi-hop graph recall, reflection, and provenance."
version: 1.0.0
author: Hercules Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hercules:
    tags: [memory, fact-store, recall, holographic, reflection, knowledge-graph]
    related_skills: [hercules-agent]
---

# Hercules Holographic Memory

## Overview

`fact_store` is Hercules's durable, cross-session memory: a fact database with
semantic (meaning-based) recall, an entity graph, self-maintaining
consolidation, and reflection. It is separate from the always-on `memory`
tool — use `memory` for a short always-in-context scratch, `fact_store` for
**deep recall and structured reasoning** over everything the agent has learned.

The store is smart on its own (dedups, supersedes contradictions, decays stale
facts, scores importance), so your job is mostly: **write the right things**,
and **recall before you assume**.

## When to Use

- Before answering anything about the user, their projects, preferences, or
  people/tools they've mentioned — **recall first** (`probe` / `graph` /
  `search`), never answer from a guess.
- After learning something durable — a preference, a decision, an identity
  fact, a project convention — **store it** so the next session has it.
- When the user asks "what do you know about X", "why do you think Y", or
  "what have we decided" — use `graph` / `why` / `search`.

**Don't use for:** transient within-turn scratch (that's the `memory` tool),
or secrets/credentials (never store those).

## Actions cheat-sheet

| Goal | Action | Key args |
|------|--------|----------|
| Remember a fact | `add` | `content`, `category`, `fact_type`, `importance` |
| Meaning-aware lookup | `search` | `query` |
| Everything about one entity | `probe` | `entity` |
| Entity + all connected facts | `graph` | `entity` (or `query`), `hops` |
| Facts spanning several entities | `reason` | `entities` |
| Structural neighbors of an entity | `related` | `entity` |
| Synthesize insights now | `reflect` | — |
| Show an insight's evidence | `why` | `fact_id` |
| Find conflicting claims | `contradict` | — |
| Browse / edit / delete | `list` / `update` / `remove` | `fact_id`, `trust_delta` |

## Writing facts well

**Store atomic, self-contained, third-person facts.** One idea per fact, no
dangling pronouns. "The user deploys production to Fly.io" — not "they use it".
The store dedups and supersedes on its own, so don't hedge or pre-search before
a normal `add`; just write the clean fact.

**Choose the type — this is the highest-leverage decision:**

- `fact_type: profile` → durable identity and stable preferences (name, role,
  tech stack, communication style). Profile facts are **injected into context
  every turn**, so keep them few and truly stable. Use for "the user prefers
  concise answers", not "the user is debugging a timeout today".
- `fact_type: episodic` (default) → everything else; recalled on demand.

**Set importance (1–10)** when it isn't average. 10 = core identity / critical
constraint ("never deploy on Fridays"); 5 = default; 1 = trivia. Importance
weights retrieval, so a critical fact outranks incidental ones.

```
fact_store(action="add",
           content="The user's team standardizes deployments on Fly.io",
           category="project", fact_type="profile", importance=8)
```

## Recalling: match the question to the action

- **A specific person/thing** → `probe` (all facts about that entity).
- **"…and everything connected to it"** → `graph` — multi-hop associative
  recall. `hops=2` reaches an entity's facts and the facts of entities they
  co-occur with ("who else works on the project the user mentioned?").
- **A fuzzy topic / natural-language question** → `search`. It matches by
  meaning, so a query sharing no words with the stored fact still recalls it.
- **A relationship between several entities** → `reason`.

Always recall before asserting a remembered fact. If recall returns nothing,
say you don't have it — don't invent.

## Reflection & provenance

The store reflects automatically at session end (folding recent episodic facts
into durable insights). You rarely call it, but:

- `reflect` — force synthesis now, e.g. after a long fact-heavy session.
- `why` with a `fact_id` — when the user challenges a belief ("why do you think
  I prefer X?"), fetch the **evidence facts** the insight was derived from and
  cite them. Insights are trustworthy precisely because they're traceable.

## Trust feedback

After a recalled fact actually helped (or misled) you, rate it so the store
learns:

```
fact_feedback(action="helpful", fact_id=<id>)     # or action="unhelpful"
```

Helpful ratings raise a fact's trust (and ranking); unhelpful lower it. Do this
when a fact materially shaped a good answer — it's how the store gets sharper
over time.

## Common Pitfalls

1. **Answering from memory without recalling.** Guessing a stored fact instead
   of `probe`/`search`-ing first. Always recall, then answer.
2. **Over-using `profile`.** Every profile fact costs context every turn. Reserve
   it for stable identity/preferences; let situational facts be episodic.
3. **Storing raw turns.** Dumping a whole message as a "fact". Extract the
   atomic, third-person claim instead.
4. **Re-storing known facts manually.** The store dedups; don't pre-search and
   branch — just `add` and let it consolidate.
5. **Ignoring contradictions.** When the user corrects a fact, `add` the new
   one — the store supersedes the old automatically; don't leave both.
6. **Never rating.** Without `fact_feedback`, trust never adapts. Rate facts
   that clearly helped.

## Verification Checklist

- [ ] Recalled (`probe`/`graph`/`search`) before answering anything about the
      user or their world.
- [ ] Stored durable takeaways as atomic, third-person facts with the right
      `fact_type` and a deliberate `importance` when non-average.
- [ ] Used `graph` (not just `probe`) when the question spans connections.
- [ ] Cited evidence via `why` when asked to justify a remembered belief.
- [ ] Rated genuinely helpful recalled facts with `fact_feedback`.
