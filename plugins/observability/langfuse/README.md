# Langfuse Observability Plugin

This plugin ships bundled with Hercules but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
hercules tools  # → Langfuse Observability

# Manual
pip install langfuse
hercules plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.hercules/.env` (or via `hercules tools`):

```bash
HERCULES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERCULES_LANGFUSE_SECRET_KEY=sk-lf-...
HERCULES_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
hercules plugins list                 # observability/langfuse should show "enabled"
hercules chat -q "hello"              # then check Langfuse for a "Hercules turn" trace
```

## Optional tuning

```bash
HERCULES_LANGFUSE_ENV=production       # environment tag
HERCULES_LANGFUSE_RELEASE=v1.0.0       # release tag
HERCULES_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
HERCULES_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
HERCULES_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
hercules plugins disable observability/langfuse
```
