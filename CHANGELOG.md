# Changelog

All notable changes to Hercules Agent are documented here. This project
follows [Semantic Versioning](https://semver.org/).

## [1.0.0] — Hercules

The **1.0 milestone**: a full rebrand to **Hercules**, removal of the Nous
provider, a self-sufficient cron system, and a memory system rebuilt into a
generative-agents-grade engine.

### Breaking

- **Rebrand Hermes → Hercules (no compatibility shims).** Every identifier,
  environment variable (`HERMES_*` → `HERCULES_*`), the config directory
  (`~/.hermes` → `~/.hercules`), module names, the `hercules` launcher, Docker
  services, the TUI package, and the `hercules-agent` package name were
  renamed. **Existing installs must migrate** their env vars and config
  directory; the `hermes` command no longer exists.
- **Removed the Nous Portal provider entirely** — its OAuth/device-code auth,
  credential pool, credits tracking, subscription/managed-tool gating, portal
  onboarding, and the `nous` provider option. The auxiliary-model fallback is
  now `OpenRouter → main`. Codex, xAI, Qwen, Anthropic, Gemini, OpenRouter,
  and Kimi/Moonshot are unaffected.

### Added — Memory system (the headline)

The holographic memory went from a keyword-matching fact store to a
three-layer, self-maintaining, belief-forming system:

- **Semantic retrieval.** Dense embeddings (any OpenAI-compatible endpoint,
  incl. local vLLM/Ollama) with a **union recall path** — facts whose *meaning*
  matches surface even with zero keyword overlap. Blended with FTS5, Jaccard,
  and HRR, weighted by trust, importance, and recency decay. Optional **HyDE**
  query rewriting.
- **Self-curation.** Typed memory (**profile** = always-injected/durable vs
  **episodic** = on-demand), **LLM-gated salience** (clean atomic facts, not
  raw turns), and **self-maintaining consolidation** — semantic dedup plus
  **supersede-on-contradiction** so the store stays coherent as reality changes.
- **Reflection.** Periodic synthesis of recent observations into higher-order
  **insights** promoted to durable profile memory, with **importance scoring**
  and **provenance** (`fact_sources` + a `why` tool) — "why do you believe
  that?" walks the evidence chain.
- **Graph reasoning.** Multi-hop associative recall over the fact↔entity graph
  ("what do I know about X and everything connected to X").
- All backends are pluggable, auto-enabling, graceful (clean fallback when
  off), and covered by deterministic tests.

### Added / Changed — Cron

- The **built-in in-process scheduler** is now the definitive Hercules cron
  provider (no external service). The Nous-mediated Chronos provider was
  retired; its useful half — an **authenticated inbound fire-webhook** (generic
  JWT verifier, `cron.fire.*` config) — was promoted to core so any external
  scheduler can trigger jobs.

### Fixed / Hardened

- Closed a CI merge-gate bypass (a failed classifier could report green) and a
  workflow script-injection sink; hardened `.gitignore` and credential logging.
- Preserve large-integer precision in tool-arg coercion (snowflake IDs no
  longer corrupted).
- Trajectory compressor: fixed compressible-region collapse, an `AsyncOpenAI`
  client leak, and a retry-count edge case.
- Browser: Chrome fallback now injects the sandbox bypass (worked around a
  dead-end in the default Docker deployment) and surfaces real errors.
- Gateway: background watcher tasks are tracked for GC-safety and clean
  shutdown; the cron-fire task is GC-safe.
- Memory: fixed a stale HRR bank corruption on category change and a
  LIKE-wildcard entity-resolution bug.

### Changed — Branding

- New **Aegean Teal / Laurel** theme across the web dashboard, desktop app, and
  installer, replacing the inherited "Nous Blue".
