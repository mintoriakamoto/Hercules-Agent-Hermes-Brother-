# Mem0 Memory Provider

Server-side LLM fact extraction with semantic search and hybrid multi-signal retrieval via the Mem0 Platform v3 API.

## Requirements

- `pip install mem0ai`
- Mem0 API key from [app.mem0.ai](https://app.mem0.ai)

## Setup

```bash
hercules memory setup    # select "mem0"
```

Or manually:
```bash
hercules config set memory.provider mem0
echo "MEM0_API_KEY=your-key" >> ~/.hercules/.env
```

## Config

Behavioral settings live in `$HERCULES_HOME/mem0.json` (set them via `hercules memory setup`). Only the secret `MEM0_API_KEY` belongs in `~/.hercules/.env`.

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `platform` | `platform` (Mem0 Cloud) or `oss` (self-managed, in-process) |
| `host` | — | Self-hosted Mem0 server URL (the Docker dashboard). When set, connects over HTTP with `X-API-Key`. Don't combine with `mode: oss` |
| `user_id` | `hercules-user` | User identifier on Mem0 |
| `agent_id` | `hercules` | Agent identifier |
| `rerank` | `false` | Rerank search results for relevance (platform mode only) |

The plugin has three connection modes:

- **Platform** — Mem0's hosted cloud (`api.mem0.ai`). Set `MEM0_API_KEY`. (default)
- **Self-hosted dashboard** — a Mem0 server you run yourself via Docker. Set `host`. See below.
- **OSS** — run Mem0 in-process with your own LLM + vector store. Set `mode: oss`. See below.

## Self-Hosted Dashboard (Server) Mode

Connect the plugin to a standalone Mem0 server you run yourself — the Docker-shipped Mem0 dashboard/server with its own REST API. Unlike OSS mode (which runs `mem0ai` in-process with your own vector store), here the plugin just talks HTTP to your server.

1. Run the Mem0 server (FastAPI + pgvector) from its Docker image and note its URL and `ADMIN_API_KEY`.
2. Point the plugin at it — via the setup wizard:
   ```bash
   hercules memory setup    # select "mem0" → "Self-hosted server"
   # Or non-interactive:
   hercules memory setup mem0 --mode selfhosted --host http://localhost:8888 --api-key your-admin-api-key
   ```
   or via env vars:
   ```bash
   echo "MEM0_HOST=http://localhost:8888" >> ~/.hercules/.env
   echo "MEM0_API_KEY=your-admin-api-key" >> ~/.hercules/.env
   ```
   or in `$HERCULES_HOME/mem0.json`:
   ```json
   {
     "host": "http://localhost:8888",
     "api_key": "your-admin-api-key"
   }
   ```
3. Start a fresh Hercules session and call `mem0_search` — it connects to your server.

The plugin authenticates with `X-API-Key` and uses the server's `/search` and `/memories` routes. `api_key` is optional — omit it only for servers running with `AUTH_DISABLED`.

> Setting `host` routes to the self-hosted server automatically. Don't set `mode: oss` — OSS takes precedence and ignores `host`.

## OSS (Self-Hosted) Mode

Run Mem0 locally with your own LLM, embedder, and vector store. This is the in-process SDK mode. To instead connect to a Mem0 server you run via Docker, see [Self-Hosted Dashboard (Server) Mode](#self-hosted-dashboard-server-mode) above.

### Interactive Setup

```bash
hercules memory setup
# Select "mem0" → "Open Source (self-hosted)"
# Follow prompts for LLM, embedder, and vector store
```

### Agent-Driven Setup (Flags)

```bash
hercules memory setup mem0 --mode oss \
  --oss-llm openai --oss-llm-key sk-... \
  --oss-vector qdrant
```

### Supported Providers

| Component | Providers |
|-----------|-----------|
| LLM | openai, ollama |
| Embedder | openai, ollama |
| Vector Store | qdrant (local/server), pgvector |

### Flags Reference

| Flag | Description |
|------|-------------|
| `--mode` | `platform` or `oss` |
| `--oss-llm` | LLM provider (default: openai) |
| `--oss-llm-key` | LLM API key |
| `--oss-embedder` | Embedder provider (default: openai) |
| `--oss-vector` | Vector store (default: qdrant) |
| `--oss-vector-path` | Qdrant local path |
| `--user-id` | User identifier |

## Switching Modes

### Platform to OSS

```bash
hercules memory setup mem0 --mode oss --oss-llm-key sk-...
```

Or edit `$HERCULES_HOME/mem0.json` directly:
```json
{
  "mode": "oss",
  "oss": {
    "llm": {"provider": "openai", "config": {"model": "gpt-5-mini"}},
    "embedder": {"provider": "openai", "config": {"model": "text-embedding-3-small"}},
    "vector_store": {"provider": "qdrant", "config": {"path": "~/.hercules/mem0_qdrant"}}
  }
}
```

### OSS to Platform

```bash
hercules memory setup mem0 --mode platform --api-key sk-...
```

### Dry Run (preview without writing)

```bash
hercules memory setup mem0 --mode oss --oss-llm-key sk-... --dry-run
```

## Tools

| Tool | Description |
|------|-------------|
| `mem0_search` | Semantic search by meaning |
| `mem0_add` | Store a fact verbatim (no LLM extraction) |
| `mem0_update` | Update a memory's text by ID |
| `mem0_delete` | Delete a memory by ID |

## Troubleshooting

### "Mem0 temporarily unavailable"

Circuit breaker tripped after 5 consecutive failures. Resets after 2 minutes.

- **Platform mode**: Check API key and internet connectivity.
- **OSS mode**: Check that your vector store (qdrant/pgvector) is running.

### OSS: Qdrant connection refused

```bash
# If using local Qdrant, check the storage path is writable:
ls -la ~/.hercules/mem0_qdrant

# If using Qdrant server, check it's reachable:
curl http://localhost:6333/healthz
```

### OSS: PGVector connection refused

```bash
# Verify PostgreSQL is running and accepting connections:
pg_isready -h localhost -p 5432
```

### OSS: Ollama not reachable

```bash
# Check Ollama is running:
curl http://localhost:11434/api/tags
```

### Memories not appearing

- `mem0_add` stores verbatim (no extraction). Use `sync_turn` for LLM extraction.
- Search uses semantic matching — try broader queries.
- Check `user_id` matches between sessions (`$HERCULES_HOME/mem0.json`).
