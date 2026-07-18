---
sidebar_position: 4
title: "Recommended Settings"
description: "Optimal config.yaml profiles for cloud API providers and local LLMs — context, compression, caching, timeouts, and tool-output tuning"
---

# Recommended Settings

Hercules' defaults are already tuned for the common case: a cloud API model
with a large context window. This page collects the settings worth changing
per setup — every key and value here comes from `cli-config.yaml.example`
and the [Configuration](./configuration.md) reference, where each option is
documented in full.

**The golden rule: don't set what Hercules can detect.** `context_length`,
`max_tokens`, and the auxiliary models are auto-detected from your provider.
Manual values are only for the cases below where detection can't work
(mostly local servers).

## Cloud API via OpenRouter (the recommended default)

The defaults are near-optimal out of the box. The high-value additions are
routing preferences and a longer prompt-cache TTL:

```yaml
model:
  provider: "auto"
  base_url: "https://openrouter.ai/api/v1"
  # context_length / max_tokens: leave unset — auto-detected

provider_routing:
  sort: "throughput"          # route to fastest providers ("price" is default)
  # data_collection: "deny"   # exclude providers that may store your data
  # require_parameters: true  # only providers supporting all request params

prompt_caching:
  cache_ttl: "1h"   # default "5m"; use "1h" for sessions with pauses between turns
```

- `sort: "throughput"` matters for agent workloads — long tool-calling turns
  amplify slow providers. (Appending `:nitro` to a model name is the same
  shortcut per-model.)
- Prompt caching activates automatically for Claude via OpenRouter or native
  Anthropic; the `1h` TTL keeps the cached prefix alive across the pauses
  typical of messaging-gateway use, so you keep the cache-read discount.
- Leave `compression:` and `auxiliary:` alone — defaults (trigger at 50% of
  the window, cheap Gemini Flash summarizer auto-selected) are the tuned
  values.

## Direct API (Anthropic / OpenAI)

Same as above minus the `openrouter:` block. The one thing worth adding is
per-model timeouts if you use extended thinking, where a single call can
legitimately run for minutes:

```yaml
providers:
  anthropic:
    request_timeout_seconds: 30     # fast-fail ordinary cloud requests
    models:
      claude-opus-4.6:
        timeout_seconds: 600        # allow long extended-thinking calls
```

## Local LLMs (llama.cpp, Ollama, LM Studio, vLLM)

This is where manual tuning is genuinely required — a local server usually
can't report its context size over `/v1/models`, and small windows need
tighter budgets everywhere:

```yaml
model:
  context_length: 32768      # MUST match the server's real window (-c / num_ctx)

providers:
  ollama-local:              # your provider key
    request_timeout_seconds: 300   # cold model loads are slow
    stale_timeout_seconds: 900     # don't kill slow local generations

# Small-context models: shrink per-call context spend so single tool results
# don't eat the window. (Defaults: 100000 / 50000 / 2000.)
file_read_max_chars: 30000
tool_output:
  max_bytes: 20000
  max_lines: 500
```

Notes:

- `context_length` is the load-bearing one — it drives when compression
  triggers and how requests are validated. Wrong value = overflow errors or
  wasted window.
- Compression needs no change: for windows under 512K, Hercules automatically
  floors the trigger threshold at 0.75 so compaction doesn't fire with half a
  small window still free.
- Auxiliary side-tasks (compression summaries, web extraction) default to a
  cheap cloud model when you have any cloud key configured — that's usually
  the right call even with a local main model. To go fully offline, point
  them at your endpoint (text-only tasks work; vision needs a multimodal
  model):

  ```yaml
  auxiliary:
    compression:
      provider: "main"
    web_extract:
      provider: "main"
  ```

- Backend-side context tuning (KV quantization, flash attention, YaRN, cache
  reuse) lives in the server, not in Hercules — see
  [Maximizing context on llama.cpp](../integrations/providers.md#maximizing-context-on-llamacpp).

## Large-context models (200K+), heavy file work

The inverse of the local profile — let tools return more per call so the
agent needs fewer round-trips:

```yaml
file_read_max_chars: 200000
tool_output:
  max_bytes: 150000
  max_lines: 5000
```

## Long-running sessions (gateway, cron, always-on agents)

```yaml
compression:
  protect_first_n: 0    # keep ONLY the system prompt + rolling summary + tail
prompt_caching:
  cache_ttl: "1h"
```

`protect_first_n: 0` is documented as the cleanest configuration for
long-running sessions: by default the first 3 non-system messages are pinned
forever, which frames every future compaction around how the session
*started* rather than what it's doing now.

## Verifying your settings

```bash
hercules doctor      # config sanity check
/usage               # in-chat: current context spend and window
/insights --days 7   # token usage over time
```

If compression fires too often or too late, adjust `compression.threshold`
and re-check with `/usage` — the [Context Compression](./configuration.md#context-compression)
section documents the full mechanics.
