"""hercules-memory-store — holographic memory plugin using MemoryProvider interface.

Registers as a MemoryProvider plugin, giving the agent structured fact storage
with entity resolution, trust scoring, and HRR-based compositional retrieval.

Original plugin by dusterbloom (PR #2351), adapted to the MemoryProvider ABC.

Retrieval blends four signals — FTS5 keyword match, Jaccard token overlap,
HRR compositional structure, and (when enabled) dense semantic embeddings —
weighted by trust and gently decayed by recency.

Config in $HERCULES_HOME/config.yaml (profile-scoped):
  plugins:
    hercules-memory-store:
      db_path: $HERCULES_HOME/memory_store.db   # omit to use the default
      auto_extract: false
      default_trust: 0.5
      min_trust_threshold: 0.3
      temporal_decay_half_life: 180   # days; 0 disables recency decay
      # Semantic embeddings (meaning-based recall, not just keywords). Auto-
      # enables when embedding_api_key_env / a base_url is set, or when
      # OPENAI_API_KEY is present. Omit everything to stay lexical-only.
      embedding_enabled: true         # tri-state: omit = auto, false = force off
      embedding_model: text-embedding-3-small
      embedding_dims: 1536
      embedding_base_url: ""          # e.g. a local vLLM/Ollama /v1 endpoint
      embedding_api_key_env: OPENAI_API_KEY
      # LLM curation: salience extraction (clean atomic facts from a session),
      # dedup + supersede-on-contradiction, and HyDE query expansion. Auto-
      # enables like embeddings; omit to stay heuristic-only.
      curation_enabled: true          # tri-state: omit = auto, false = force off
      curation_model: gpt-4o-mini
      curation_base_url: ""
      curation_api_key_env: OPENAI_API_KEY
      profile_inject_limit: 15        # durable 'profile' facts injected each turn
      # Reflection: at session end, fold recent observations into higher-order
      # insights promoted to durable 'profile' memory (with provenance links).
      # Auto-on when curation is enabled; also runnable via fact_store(reflect).
      auto_reflect: true
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from .store import MemoryStore
from .retrieval import FactRetriever
from hercules_cli.config import cfg_get

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hebbian co-activation reinforcement
# ---------------------------------------------------------------------------
# Facts retrieved together in one recall are "co-activated". If the agent then
# rates one of them helpful, the *association* is what proved useful — so the
# facts recalled alongside it get a small, sub-helpful trust nudge. Over time
# clusters of mutually-useful facts self-strengthen, so a query that lands on
# one of them surfaces the whole cluster. This is memory learning which
# associations pay off from outcomes, not just which individual facts do.
#
# Fully in-memory and session-scoped: no schema change, no persistence, and it
# rides on the existing trust column via store.update_fact(trust_delta=...).
_CO_ACTIVATION_DELTA = 0.02       # sub-helpful nudge (direct helpful is 0.05)
_CO_ACTIVATION_MEMORY = 12        # recall episodes retained for reinforcement
_CO_ACTIVATION_MAX_FANOUT = 12    # cap co-boosted facts per feedback event
_ASSOCIATION_DELTA = 0.15         # durable Hebbian edge strengthened per event


# ---------------------------------------------------------------------------
# Tool schemas (unchanged from original PR)
# ---------------------------------------------------------------------------

FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. "
        "Use alongside the memory tool — memory for always-on context, "
        "fact_store for deep recall and compositional queries.\n\n"
        "ACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember. Auto-dedups "
        "and supersedes contradicted facts. Set fact_type='profile' for durable "
        "identity/preferences (always in context), importance 1-10 for weight.\n"
        "• search — Meaning-aware lookup ('editor config', 'deploy process'); "
        "recalls by semantics, not just keywords.\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n"
        "• related — What connects to an entity? Structural adjacency.\n"
        "• graph — Multi-hop associative recall: facts about an entity AND "
        "everything connected to it (set hops, default 2).\n"
        "• spread — Spreading activation: facts that have proven useful "
        "TOGETHER with a seed (fact_id, or query/entity resolved to its best "
        "match), ranked by learned association strength. Recalls the cluster, "
        "not just the keyword match.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities.\n"
        "• reflect — Synthesize durable insights from recent facts now.\n"
        "• why — Show the evidence facts an insight was derived from (fact_id).\n"
        "• contradict — Memory hygiene: find conflicting claims.\n"
        "• update/remove/list — CRUD operations.\n\n"
        "IMPORTANT: Before answering questions about the user, ALWAYS probe, "
        "graph, or reason first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "search", "probe", "related", "reason", "contradict", "update", "remove", "list", "reflect", "why", "graph", "spread"],
            },
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search'); also a seed for 'graph'."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'/'graph'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'/'why'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "fact_type": {"type": "string", "enum": ["profile", "episodic"], "description": "For 'add': 'profile' = durable identity/preferences injected every turn; 'episodic' (default) = recalled on demand."},
            "importance": {"type": "integer", "description": "For 'add': 1-10 retrieval weight (10 = core/critical, 5 = default, 1 = trivial)."},
            "hops": {"type": "integer", "description": "For 'graph': association depth to traverse (default 2)."},
            "min_facts": {"type": "integer", "description": "For 'reflect': minimum new observations required before synthesizing insights (default 3)."},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_delta": {"type": "number", "description": "Trust adjustment for 'update'."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "min_strength": {"type": "number", "description": "For 'spread': minimum learned association strength (default: 0.0)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": (
        "Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
        "This trains the memory — good facts rise, bad facts sink."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["helpful", "unhelpful"]},
            "fact_id": {"type": "integer", "description": "The fact ID to rate."},
        },
        "required": ["action", "fact_id"],
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_plugin_config() -> dict:
    from hercules_constants import get_hercules_home
    config_path = get_hercules_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        return cfg_get(all_config, "plugins", "hercules-memory-store", default={}) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = None
        self._retriever = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))
        # Recent recall episodes (each a frozenset of co-activated fact_ids),
        # for Hebbian co-activation reinforcement on positive feedback.
        self._last_recall: deque = deque(maxlen=_CO_ACTIVATION_MEMORY)

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hercules_home):
        """Write config to config.yaml under plugins.hercules-memory-store."""
        from pathlib import Path
        config_path = Path(hercules_home) / "config.yaml"
        try:
            import yaml
            existing = {}
            if config_path.exists():
                with open(config_path, encoding="utf-8-sig") as f:
                    existing = yaml.safe_load(f) or {}
            existing.setdefault("plugins", {})
            existing["plugins"]["hercules-memory-store"] = values
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, default_flow_style=False)
        except Exception:
            pass

    def get_config_schema(self):
        from hercules_constants import display_hercules_home
        _default_db = f"{display_hercules_home()}/memory_store.db"
        return [
            {"key": "db_path", "description": "SQLite database path", "default": _default_db},
            {"key": "auto_extract", "description": "Auto-extract facts at session end", "default": "false", "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": "0.5"},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": "1024"},
        ]

    def initialize(self, session_id: str, **kwargs) -> None:
        from hercules_constants import get_hercules_home
        _hercules_home = str(get_hercules_home())
        _default_db = _hercules_home + "/memory_store.db"
        db_path = self._config.get("db_path", _default_db)
        # Expand $HERCULES_HOME in user-supplied paths so config values like
        # "$HERCULES_HOME/memory_store.db" or "~/.hercules/memory_store.db" both
        # resolve to the active profile's directory.
        if isinstance(db_path, str):
            db_path = db_path.replace("$HERCULES_HOME", _hercules_home)
            db_path = db_path.replace("${HERCULES_HOME}", _hercules_home)
        default_trust = float(self._config.get("default_trust", 0.5))
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        hrr_weight = float(self._config.get("hrr_weight", 0.3))
        # Recency: gentle temporal decay so stale facts fade below fresh ones.
        # Default 180-day half-life (was 0/off) — old but still-trusted facts
        # keep ~70% weight after a year, so nothing is lost, just deprioritized.
        temporal_decay = int(self._config.get("temporal_decay_half_life", 180))

        # Optional semantic embedder (auto-enables when an embedding backend is
        # configured or OPENAI_API_KEY is present; graceful lexical fallback
        # otherwise). Turns retrieval from keyword-match into meaning-match.
        embedder = None
        try:
            from .embeddings import Embedder

            embedder = Embedder.from_config(self._config)
            if getattr(embedder, "enabled", False):
                logger.info("holographic memory: semantic embeddings enabled (model=%s)", embedder.model)
        except Exception as exc:
            logger.debug("holographic memory: embedder init skipped: %s", exc)
            embedder = None

        # Optional curation LLM: salience extraction, dedup/supersede
        # reconciliation, and HyDE query expansion. Graceful no-op when off.
        self._llm = None
        try:
            from .llm import MemoryLLM

            self._llm = MemoryLLM.from_config(self._config)
            if getattr(self._llm, "enabled", False):
                logger.info("holographic memory: LLM curation enabled (model=%s)", self._llm.model)
        except Exception as exc:
            logger.debug("holographic memory: LLM init skipped: %s", exc)
            self._llm = None

        self._store = MemoryStore(
            db_path=db_path, default_trust=default_trust, hrr_dim=hrr_dim, embedder=embedder
        )
        self._retriever = FactRetriever(
            store=self._store,
            temporal_decay_half_life=temporal_decay,
            hrr_weight=hrr_weight,
            hrr_dim=hrr_dim,
            embedder=embedder,
        )
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store._conn.execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
        except Exception:
            total = 0
        if total == 0:
            return (
                "# Holographic Memory\n"
                "Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                "Use fact_feedback to rate facts after using them (trains trust scores)."
            )
        block = (
            f"# Holographic Memory\n"
            f"Active. {total} facts stored with entity resolution and trust scoring.\n"
            f"Use fact_store to search, probe entities, reason across entities, or add facts.\n"
            f"Use fact_feedback to rate facts after using them (trains trust scores)."
        )
        # Always-on durable context: 'profile' facts (identity, stable
        # preferences) are injected every turn rather than waiting to be
        # recalled, since they apply regardless of the current query.
        try:
            profile = self._store.list_profile_facts(limit=int(self._config.get("profile_inject_limit", 15)))
        except Exception:
            profile = []
        if profile:
            lines = "\n".join(f"- {p.get('content', '')}" for p in profile)
            block += "\n\n## Known profile (durable)\n" + lines
        return block

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        try:
            results = self._retriever.search(query, min_trust=self._min_trust, limit=5)
            # HyDE: a vague message ("what did we decide?") often shares little
            # with the stored fact. When curation is on, also search an
            # LLM-rewritten query and merge, so recall doesn't hinge on phrasing.
            llm = getattr(self, "_llm", None)
            if llm is not None and getattr(llm, "enabled", False):
                try:
                    expanded = llm.expand_query(query)
                except Exception:
                    expanded = None
                if expanded and expanded.strip().lower() != query.strip().lower():
                    extra = self._retriever.search(expanded, min_trust=self._min_trust, limit=5)
                    seen = {r.get("fact_id") for r in results}
                    for r in extra:
                        if r.get("fact_id") not in seen:
                            seen.add(r.get("fact_id"))
                            results.append(r)
                    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)
                    results = results[:5]
            if not results:
                return ""
            lines = []
            for r in results:
                trust = r.get("trust_score", r.get("trust", 0))
                lines.append(f"- [{trust:.1f}] {r.get('content', '')}")
            return "## Holographic Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Holographic memory stores explicit facts via tools, not auto-sync.
        # The on_session_end hook handles auto-extraction if configured.
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "fact_store":
            return self._handle_fact_store(args)
        elif tool_name == "fact_feedback":
            return self._handle_fact_feedback(args)
        return tool_error(f"Unknown tool: {tool_name}")

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._store:
            return
        if self._config.get("auto_extract", False) and messages:
            self._auto_extract_facts(messages)
        # Reflect after extracting: fold recent observations into higher-order
        # insights. Auto-on when a curation LLM is available (opt out with
        # auto_reflect: false); a no-op without the LLM.
        llm = getattr(self, "_llm", None)
        auto_reflect = self._config.get("auto_reflect", True)
        if auto_reflect and llm is not None and getattr(llm, "enabled", False):
            try:
                self.reflect()
            except Exception as exc:
                logger.debug("Reflection failed: %s", exc)

    def reflect(self, min_facts: int = 5, max_facts: int = 40) -> dict:
        """Synthesize higher-order insights from recent unreflected observations.

        Reads live episodic facts not yet reflected on, asks the LLM to
        generalize them into durable insights, promotes each insight to a
        high-importance 'profile' fact with provenance links to its evidence,
        and marks the sources reflected. Returns a summary dict. Safe no-op when
        the LLM is unavailable or there isn't enough new experience yet.
        """
        result = {"insights": 0, "sources_considered": 0}
        store = self._store
        llm = getattr(self, "_llm", None)
        if store is None or llm is None or not getattr(llm, "enabled", False):
            return result
        facts = store.select_unreflected_facts(limit=max_facts)
        if len(facts) < max(1, min_facts):
            return result  # not enough new experience to generalize yet
        result["sources_considered"] = len(facts)
        candidates = [{"fact_id": f["fact_id"], "content": f["content"]} for f in facts]
        try:
            insights = llm.reflect(candidates)
        except Exception as exc:
            logger.debug("LLM reflect failed: %s", exc)
            insights = None
        # Mark the window reflected regardless, so a barren pass doesn't re-run
        # on the same facts every session.
        store.mark_reflected([f["fact_id"] for f in facts])
        if not insights:
            return result
        valid_ids = {f["fact_id"] for f in facts}
        made = 0
        for ins in insights:
            sources = [s for s in ins.get("source_ids", []) if s in valid_ids]
            try:
                store.add_derived_fact(
                    ins["content"],
                    category=ins.get("category", "insight"),
                    source_ids=sources,
                    importance=int(ins.get("importance", 8)),
                )
                made += 1
            except Exception:
                continue
        result["insights"] = made
        if made:
            logger.info("Reflection: synthesized %d insight(s) from %d observations", made, len(facts))
        return result

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Mirror built-in memory writes as facts."""
        if action == "add" and self._store and content:
            try:
                category = "user_pref" if target == "user" else "general"
                self._store.add_fact(content, category=category)
            except Exception as e:
                logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        # Release the shared SQLite connection deterministically on the
        # caller's thread. Dropping the reference alone leaves fd finalization
        # to GC, which keeps the connection (and its write lock) alive on a
        # long-running gateway and prolongs the "database is locked" contention
        # this store's shared-connection refcounting is meant to eliminate.
        # close() is idempotent and refcount-guarded, so siblings stay safe.
        if self._store is not None:
            try:
                self._store.close()
            except Exception as e:
                logger.debug("Holographic shutdown close() failed: %s", e)
        self._store = None
        self._retriever = None

    # -- Tool handlers -------------------------------------------------------

    def _handle_fact_store(self, args: dict) -> str:
        try:
            action = args["action"]
            store = self._store
            retriever = self._retriever

            if action == "add":
                # Curated add: semantic dedup + supersede-on-contradiction so
                # the store self-maintains instead of accumulating duplicates
                # and stale facts. Falls back to a plain insert when no
                # embedder/LLM is available.
                result = store.add_fact_curated(
                    args["content"],
                    category=args.get("category", "general"),
                    tags=args.get("tags", ""),
                    fact_type=str(args.get("fact_type", "episodic")),
                    reconciler=getattr(self, "_llm", None),
                )
                status = {
                    "duplicate": "already_known",
                    "update": "updated",
                    "new": "added",
                }.get(result["action"], "added")
                out = {"fact_id": result["fact_id"], "status": status}
                if result.get("superseded"):
                    out["superseded"] = result["superseded"]
                return json.dumps(out)

            elif action == "search":
                results = retriever.search(
                    args["query"],
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", self._min_trust)),
                    limit=int(args.get("limit", 10)),
                )
                self._note_co_activation(results)
                return json.dumps({"results": results, "count": len(results)})

            elif action == "probe":
                results = retriever.probe(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                self._note_co_activation(results)
                return json.dumps({"results": results, "count": len(results)})

            elif action == "related":
                results = retriever.related(
                    args["entity"],
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                self._note_co_activation(results)
                return json.dumps({"results": results, "count": len(results)})

            elif action == "reason":
                entities = args.get("entities", [])
                if not entities:
                    return tool_error("reason requires 'entities' list")
                results = retriever.reason(
                    entities,
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                self._note_co_activation(results)
                return json.dumps({"results": results, "count": len(results)})

            elif action == "contradict":
                results = retriever.contradict(
                    category=args.get("category"),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"results": results, "count": len(results)})

            elif action == "update":
                updated = store.update_fact(
                    int(args["fact_id"]),
                    content=args.get("content"),
                    trust_delta=float(args["trust_delta"]) if "trust_delta" in args else None,
                    tags=args.get("tags"),
                    category=args.get("category"),
                )
                return json.dumps({"updated": updated})

            elif action == "remove":
                removed = store.remove_fact(int(args["fact_id"]))
                return json.dumps({"removed": removed})

            elif action == "list":
                facts = store.list_facts(
                    category=args.get("category"),
                    min_trust=float(args.get("min_trust", 0.0)),
                    limit=int(args.get("limit", 10)),
                )
                return json.dumps({"facts": facts, "count": len(facts)})

            elif action == "reflect":
                # Synthesize durable insights from recent observations now.
                summary = self.reflect(min_facts=int(args.get("min_facts", 3)))
                return json.dumps({"status": "reflected", **summary})

            elif action == "why":
                # Provenance: the evidence facts an insight was synthesized from.
                sources = store.get_fact_sources(int(args["fact_id"]))
                return json.dumps({"sources": sources, "count": len(sources)})

            elif action == "graph":
                # Associative multi-hop recall over the entity graph.
                seed = args.get("entity") or args.get("query", "")
                results = retriever.graph_search(
                    seed,
                    hops=int(args.get("hops", 2)),
                    limit=int(args.get("limit", 20)),
                )
                self._note_co_activation(results)
                return json.dumps({"facts": results, "count": len(results)})

            elif action == "spread":
                # Spreading activation over durable Hebbian edges: surface the
                # facts that have proven useful *together* with a seed fact,
                # ranked by learned association strength. The seed can be an
                # explicit fact_id, or a query/entity we resolve to its best
                # match first (so the caller need not know fact ids).
                seed_id = args.get("fact_id")
                if seed_id is None:
                    probe_text = args.get("query") or args.get("entity") or ""
                    if not probe_text:
                        return tool_error("spread requires 'fact_id', 'query', or 'entity'")
                    hits = retriever.search(
                        probe_text,
                        min_trust=float(args.get("min_trust", self._min_trust)),
                        limit=1,
                    )
                    if not hits:
                        return json.dumps({"seed": None, "facts": [], "count": 0})
                    seed_id = hits[0]["fact_id"]
                results = store.get_associations(
                    int(seed_id),
                    limit=int(args.get("limit", 10)),
                    min_strength=float(args.get("min_strength", 0.0)),
                )
                return json.dumps(
                    {"seed": int(seed_id), "facts": results, "count": len(results)}
                )

            else:
                return tool_error(f"Unknown action: {action}")

        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    def _handle_fact_feedback(self, args: dict) -> str:
        try:
            fact_id = int(args["fact_id"])
            helpful = args["action"] == "helpful"
            result = self._store.record_feedback(fact_id, helpful=helpful)
            # Hebbian reinforcement: a helpful rating means the association that
            # surfaced this fact paid off, so nudge the facts co-recalled with
            # it. Only on positive feedback — we never propagate a penalty.
            if helpful:
                co_activated = self._reinforce_co_activated(fact_id)
                if co_activated:
                    result = {
                        **result,
                        "co_activated": co_activated,
                        "co_activation_delta": _CO_ACTIVATION_DELTA,
                    }
            return json.dumps(result)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except Exception as exc:
            return tool_error(str(exc))

    # -- Hebbian co-activation reinforcement ---------------------------------

    def _note_co_activation(self, results: list) -> None:
        """Record a recall episode of co-activated fact_ids.

        Facts returned together form an association worth reinforcing later if
        the recall proves helpful. Needs at least two distinct facts to be an
        association. Best-effort — never lets bookkeeping break a recall.
        """
        episodes = getattr(self, "_last_recall", None)
        if episodes is None:
            return
        try:
            ids: list[int] = []
            seen: set[int] = set()
            for r in results or []:
                fid = r.get("fact_id") if isinstance(r, dict) else None
                if isinstance(fid, int) and fid not in seen:
                    seen.add(fid)
                    ids.append(fid)
            if len(ids) >= 2:
                episodes.append(frozenset(ids))
        except Exception as exc:
            logger.debug("co-activation capture skipped: %s", exc)

    def _reinforce_co_activated(self, fact_id: int) -> list:
        """Nudge the trust of facts co-recalled with a helpfully-rated fact.

        Returns the fact_ids that were reinforced (bounded, deterministic).
        """
        episodes = getattr(self, "_last_recall", None)
        store = self._store
        if not episodes or store is None:
            return []
        partners: set[int] = set()
        for episode in episodes:
            if fact_id in episode:
                partners |= episode
        partners.discard(fact_id)
        if not partners:
            return []
        boosted: list[int] = []
        for fid in sorted(partners)[:_CO_ACTIVATION_MAX_FANOUT]:
            try:
                if store.update_fact(fid, trust_delta=_CO_ACTIVATION_DELTA):
                    boosted.append(fid)
                    # Also lay down a durable association edge so the learned
                    # cluster survives the session and can be recalled via
                    # spreading activation, not just via a warmer trust score.
                    store.reinforce_association(fact_id, fid, delta=_ASSOCIATION_DELTA)
            except Exception as exc:
                logger.debug("co-activation reinforce skipped for %s: %s", fid, exc)
        return boosted

    # -- Auto-extraction (on_session_end) ------------------------------------

    def _llm_extract_facts(self, messages: list, llm) -> bool:
        """LLM salience extraction → curated writes. Returns True on success.

        Builds a compact transcript, asks the LLM for durable atomic facts, and
        stores each through ``add_fact_curated`` so extraction, dedup, and
        supersede all compose. Returns False (caller falls back to regex) if the
        LLM yields nothing usable.
        """
        try:
            parts = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    parts.append(f"{role}: {content.strip()}")
            if not parts:
                return False
            transcript = "\n".join(parts)[-6000:]
            facts = llm.extract_facts(transcript)
            if facts is None:
                return False  # LLM unavailable/unparseable → let regex try
            stored = 0
            for f in facts:
                try:
                    res = self._store.add_fact_curated(
                        f["content"],
                        category=f.get("category", "general"),
                        fact_type=f.get("fact_type", "episodic"),
                        reconciler=llm,
                        importance=int(f.get("importance", 5)),
                    )
                    if res.get("action") in ("new", "update"):
                        stored += 1
                except Exception:
                    continue
            logger.info("LLM salience: extracted %d facts (%d new/updated)", len(facts), stored)
            return True
        except Exception as exc:
            logger.debug("LLM salience extraction failed: %s", exc)
            return False

    def _auto_extract_facts(self, messages: list) -> None:
        # Preferred path: LLM-gated salience extraction — clean, atomic,
        # typed facts curated into the store (dedup + supersede). Falls back to
        # the regex heuristics below when no curation LLM is configured.
        llm = getattr(self, "_llm", None)
        if llm is not None and getattr(llm, "enabled", False):
            if self._llm_extract_facts(messages, llm):
                return

        _PREF_PATTERNS = [
            re.compile(r'\bI\s+(?:prefer|like|love|use|want|need)\s+(.+)', re.IGNORECASE),
            re.compile(r'\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)', re.IGNORECASE),
            re.compile(r'\bI\s+(?:always|never|usually)\s+(.+)', re.IGNORECASE),
        ]
        _DECISION_PATTERNS = [
            re.compile(r'\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)', re.IGNORECASE),
            re.compile(r'\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)', re.IGNORECASE),
        ]

        extracted = 0
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 10:
                continue

            for pattern in _PREF_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="user_pref")
                        extracted += 1
                    except Exception:
                        pass
                    break

            for pattern in _DECISION_PATTERNS:
                if pattern.search(content):
                    try:
                        self._store.add_fact(content[:400], category="project")
                        extracted += 1
                    except Exception:
                        pass
                    break

        if extracted:
            logger.info("Auto-extracted %d facts from conversation", extracted)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    config = _load_plugin_config()
    provider = HolographicMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
