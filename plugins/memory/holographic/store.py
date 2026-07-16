"""
SQLite-backed fact store with entity resolution and trust scoring.
Single-user Hercules memory store plugin.
"""

import re
import sqlite3
import threading
from pathlib import Path

try:
    from . import holographic as hrr
except ImportError:
    import holographic as hrr  # type: ignore[no-redef]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hrr_vector      BLOB,
    embedding       BLOB,
    fact_type       TEXT DEFAULT 'episodic',
    importance      INTEGER DEFAULT 5,
    reflected       INTEGER DEFAULT 0,
    superseded_by   INTEGER
);

CREATE TABLE IF NOT EXISTS fact_sources (
    derived_fact_id INTEGER REFERENCES facts(fact_id),
    source_fact_id  INTEGER REFERENCES facts(fact_id),
    PRIMARY KEY (derived_fact_id, source_fact_id)
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    entity_type TEXT DEFAULT 'unknown',
    aliases     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_entities (
    fact_id   INTEGER REFERENCES facts(fact_id),
    entity_id INTEGER REFERENCES entities(entity_id),
    PRIMARY KEY (fact_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_facts_trust    ON facts(trust_score DESC);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_entities_name  ON entities(name);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, tags, content=facts, content_rowid=fact_id);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS memory_banks (
    bank_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_name  TEXT NOT NULL UNIQUE,
    vector     BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    fact_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Durable Hebbian association edges: facts that repeatedly prove useful
-- together accumulate strength here, so recall can "fire together" — surface
-- the whole learned cluster even when the query only matches one member.
-- Undirected: each edge is stored once with fact_a < fact_b (canonical order).
CREATE TABLE IF NOT EXISTS fact_associations (
    fact_a     INTEGER NOT NULL REFERENCES facts(fact_id),
    fact_b     INTEGER NOT NULL REFERENCES facts(fact_id),
    strength   REAL NOT NULL DEFAULT 0.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fact_a, fact_b)
);
CREATE INDEX IF NOT EXISTS idx_assoc_a ON fact_associations(fact_a, strength DESC);
CREATE INDEX IF NOT EXISTS idx_assoc_b ON fact_associations(fact_b, strength DESC);
"""

# Trust adjustment constants
_HELPFUL_DELTA   =  0.05
_UNHELPFUL_DELTA = -0.10
_TRUST_MIN       =  0.0
_TRUST_MAX       =  1.0

# Entity extraction patterns
_RE_CAPITALIZED  = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
_RE_DOUBLE_QUOTE = re.compile(r'"([^"]+)"')
_RE_SINGLE_QUOTE = re.compile(r"'([^']+)'")
_RE_AKA          = re.compile(
    r'(\w+(?:\s+\w+)*)\s+(?:aka|also known as)\s+(\w+(?:\s+\w+)*)',
    re.IGNORECASE,
)


def _clamp_trust(value: float) -> float:
    return max(_TRUST_MIN, min(_TRUST_MAX, value))


class MemoryStore:
    """SQLite-backed fact store with entity resolution and trust scoring."""

    # --- Process-wide shared connection registry -------------------------
    # SQLite permits only one writer at a time. Each MemoryStore instance used
    # to open its own connection guarded by its own RLock, so the several
    # providers that coexist in one process (the main agent plus every
    # delegate_task subagent) raced as independent WAL writers. Combined with
    # writes that were not rolled back on error, one connection could leave an
    # open write transaction that pinned the write lock and made every other
    # connection's write fail with "database is locked" for the full busy
    # timeout. All instances for the same database now share ONE connection and
    # ONE re-entrant lock, so access is fully serialized and cross-connection
    # contention is impossible. The shared connection is refcounted, so closing
    # one instance never tears the connection out from under a live sibling.
    _shared: dict = {}
    _shared_guard = threading.Lock()

    def __init__(
        self,
        db_path: "str | Path | None" = None,
        default_trust: float = 0.5,
        hrr_dim: int = 1024,
        embedder: "object | None" = None,
    ) -> None:
        if db_path is None:
            from hercules_constants import get_hercules_home
            db_path = str(get_hercules_home() / "memory_store.db")
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_trust = _clamp_trust(default_trust)
        self.hrr_dim = hrr_dim
        self._hrr_available = hrr._HAS_NUMPY
        # Optional semantic embedder. When None/disabled, the store behaves
        # exactly as before (lexical + HRR only).
        self.embedder = embedder

        # Acquire (or open) the process-wide shared connection for this DB.
        # resolve() (not just expanduser) so symlinked/relative paths to the
        # same file share ONE connection instead of silently reintroducing
        # the multi-writer contention this registry exists to prevent.
        try:
            self._key = str(self.db_path.resolve())
        except OSError:
            self._key = str(self.db_path)
        with MemoryStore._shared_guard:
            entry = MemoryStore._shared.get(self._key)
            if entry is None:
                conn = sqlite3.connect(
                    self._key,
                    check_same_thread=False,
                    timeout=10.0,
                    # Autocommit: every statement is its own transaction, so a
                    # write that raises mid-method can never leave a dangling
                    # transaction (and its write lock) open. The explicit
                    # commit() calls below become harmless no-ops.
                    isolation_level=None,
                )
                conn.row_factory = sqlite3.Row
                entry = {"conn": conn, "lock": threading.RLock(), "refs": 0, "ready": False}
                MemoryStore._shared[self._key] = entry
            entry["refs"] += 1
            self._entry = entry
            self._conn = entry["conn"]
            self._lock = entry["lock"]

        # Initialise the schema once per shared connection.
        with self._lock:
            if not self._entry["ready"]:
                self._init_db()
                self._entry["ready"] = True

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create tables, indexes, and triggers if they do not exist. Enable WAL mode."""
        # Use the shared WAL-fallback helper so memory_store.db degrades
        # gracefully on NFS/SMB/FUSE-mounted HERCULES_HOME (same issue as
        # state.db / kanban.db — see hercules_state._WAL_INCOMPAT_MARKERS).
        from hercules_state import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="memory_store.db (holographic)")
        self._conn.executescript(_SCHEMA)
        # Migrate: add newer columns if missing (safe for existing databases).
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(facts)").fetchall()}
        if "hrr_vector" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN hrr_vector BLOB")
        if "embedding" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")
        if "fact_type" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN fact_type TEXT DEFAULT 'episodic'")
        if "superseded_by" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN superseded_by INTEGER")
        if "importance" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN importance INTEGER DEFAULT 5")
        if "reflected" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN reflected INTEGER DEFAULT 0")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_fact(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
        fact_type: str = "episodic",
        importance: int = 5,
    ) -> int:
        """Insert a fact and return its fact_id.

        Deduplicates by content (UNIQUE constraint). On duplicate, returns
        the existing fact_id without modifying the row. Extracts entities from
        the content and links them to the fact. ``fact_type`` is "profile" for
        durable identity/preferences (always surfaced, never decayed) or
        "episodic" for everything else. ``importance`` (1–10) weights the fact
        in retrieval — significant facts outrank incidental ones.
        """
        fact_type = "profile" if fact_type == "profile" else "episodic"
        importance = max(1, min(10, int(importance)))
        with self._lock:
            content = content.strip()
            if not content:
                raise ValueError("content must not be empty")

            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO facts (content, category, tags, trust_score, fact_type, importance)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (content, category, tags, self.default_trust, fact_type, importance),
                )
                self._conn.commit()
                fact_id: int = cur.lastrowid  # type: ignore[assignment]
            except sqlite3.IntegrityError:
                # Duplicate content — return existing id
                row = self._conn.execute(
                    "SELECT fact_id FROM facts WHERE content = ?", (content,)
                ).fetchone()
                return int(row["fact_id"])

            # Entity extraction and linking
            for name in self._extract_entities(content):
                entity_id = self._resolve_entity(name)
                self._link_fact_entity(fact_id, entity_id)

            # Compute HRR vector after entity linking
            self._compute_hrr_vector(fact_id, content)
            # Compute the semantic embedding (no-op when no embedder).
            self._compute_embedding(fact_id, content)
            self._rebuild_bank(category)

            return fact_id

    def search_facts(
        self,
        query: str,
        category: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text search over facts using FTS5.

        Returns a list of fact dicts ordered by FTS5 rank, then trust_score
        descending. Also increments retrieval_count for matched facts.
        """
        with self._lock:
            query = query.strip()
            if not query:
                return []

            # FTS5 AND-joins tokens by default, which zeroes out recall on
            # natural-language queries. Reuse the retriever's sanitizer
            # (stopword drop + OR-join content tokens). Imported lazily to
            # avoid a store->retrieval import cycle.
            from plugins.memory.holographic.retrieval import FactRetriever

            match_query = FactRetriever._sanitize_fts_query(query)
            params: list = [match_query, min_trust]
            category_clause = ""
            if category is not None:
                category_clause = "AND f.category = ?"
                params.append(category)
            params.append(limit)

            sql = f"""
                SELECT f.fact_id, f.content, f.category, f.tags,
                       f.trust_score, f.retrieval_count, f.helpful_count,
                       f.created_at, f.updated_at
                FROM facts f
                JOIN facts_fts fts ON fts.rowid = f.fact_id
                WHERE facts_fts MATCH ?
                  AND f.trust_score >= ?
                  AND f.superseded_by IS NULL
                  {category_clause}
                ORDER BY fts.rank, f.trust_score DESC
                LIMIT ?
            """

            rows = self._conn.execute(sql, params).fetchall()
            results = [self._row_to_dict(r) for r in rows]

            if results:
                ids = [r["fact_id"] for r in results]
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"UPDATE facts SET retrieval_count = retrieval_count + 1 WHERE fact_id IN ({placeholders})",
                    ids,
                )
                self._conn.commit()

            return results

    def update_fact(
        self,
        fact_id: int,
        content: str | None = None,
        trust_delta: float | None = None,
        tags: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Partially update a fact. Trust is clamped to [0, 1].

        Returns True if the row existed, False otherwise.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, trust_score, category FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if row is None:
                return False
            old_category = row["category"]

            assignments: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list = []

            if content is not None:
                assignments.append("content = ?")
                params.append(content.strip())
            if tags is not None:
                assignments.append("tags = ?")
                params.append(tags)
            if category is not None:
                assignments.append("category = ?")
                params.append(category)
            if trust_delta is not None:
                new_trust = _clamp_trust(row["trust_score"] + trust_delta)
                assignments.append("trust_score = ?")
                params.append(new_trust)

            params.append(fact_id)
            self._conn.execute(
                f"UPDATE facts SET {', '.join(assignments)} WHERE fact_id = ?",
                params,
            )
            self._conn.commit()

            # If content changed, re-extract entities
            if content is not None:
                self._conn.execute(
                    "DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,)
                )
                for name in self._extract_entities(content):
                    entity_id = self._resolve_entity(name)
                    self._link_fact_entity(fact_id, entity_id)
                self._conn.commit()

            # Recompute HRR vector + semantic embedding if content changed
            if content is not None:
                self._compute_hrr_vector(fact_id, content)
                self._compute_embedding(fact_id, content)
            # Rebuild the bank for the fact's (possibly new) category. When the
            # category changed, the OLD category's bank must also be rebuilt —
            # otherwise it keeps this fact's vector even though the fact no
            # longer belongs to it, skewing that bank's compositional queries.
            new_category = category if category is not None else old_category
            self._rebuild_bank(new_category)
            if category is not None and category != old_category:
                self._rebuild_bank(old_category)

            return True

    def remove_fact(self, fact_id: int) -> bool:
        """Delete a fact and its entity links. Returns True if the row existed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, category FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                return False

            self._conn.execute(
                "DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,)
            )
            self._conn.execute(
                "DELETE FROM fact_associations WHERE fact_a = ? OR fact_b = ?",
                (fact_id, fact_id),
            )
            self._conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            self._conn.commit()
            self._rebuild_bank(row["category"])
            return True

    def list_facts(
        self,
        category: str | None = None,
        min_trust: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Browse facts ordered by trust_score descending.

        Optionally filter by category and minimum trust score.
        """
        with self._lock:
            params: list = [min_trust]
            category_clause = ""
            if category is not None:
                category_clause = "AND category = ?"
                params.append(category)
            params.append(limit)

            sql = f"""
                SELECT fact_id, content, category, tags, trust_score,
                       retrieval_count, helpful_count, created_at, updated_at,
                       fact_type
                FROM facts
                WHERE trust_score >= ?
                  AND superseded_by IS NULL
                  {category_clause}
                ORDER BY trust_score DESC
                LIMIT ?
            """
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def record_feedback(self, fact_id: int, helpful: bool) -> dict:
        """Record user feedback and adjust trust asymmetrically.

        helpful=True  -> trust += 0.05, helpful_count += 1
        helpful=False -> trust -= 0.10

        Returns a dict with fact_id, old_trust, new_trust, helpful_count.
        Raises KeyError if fact_id does not exist.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, trust_score, helpful_count FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"fact_id {fact_id} not found")

            old_trust: float = row["trust_score"]
            delta = _HELPFUL_DELTA if helpful else _UNHELPFUL_DELTA
            new_trust = _clamp_trust(old_trust + delta)

            helpful_increment = 1 if helpful else 0
            self._conn.execute(
                """
                UPDATE facts
                SET trust_score    = ?,
                    helpful_count  = helpful_count + ?,
                    updated_at     = CURRENT_TIMESTAMP
                WHERE fact_id = ?
                """,
                (new_trust, helpful_increment, fact_id),
            )
            self._conn.commit()

            return {
                "fact_id":      fact_id,
                "old_trust":    old_trust,
                "new_trust":    new_trust,
                "helpful_count": row["helpful_count"] + helpful_increment,
            }

    # ------------------------------------------------------------------
    # Durable Hebbian associations (spreading activation)
    # ------------------------------------------------------------------

    def reinforce_association(self, fact_id_a: int, fact_id_b: int, delta: float = 0.1) -> float:
        """Strengthen the undirected association between two facts.

        Strength accumulates (capped at 1.0) so a pair that keeps proving
        useful together rises above one-off co-occurrences. Self-edges and
        edges to non-existent facts are ignored. Returns the new strength
        (0.0 when the edge was rejected).
        """
        a, b = sorted((int(fact_id_a), int(fact_id_b)))
        if a == b:
            return 0.0
        with self._lock:
            present = self._conn.execute(
                "SELECT COUNT(*) FROM facts WHERE fact_id IN (?, ?)", (a, b)
            ).fetchone()[0]
            if present < 2:
                return 0.0
            self._conn.execute(
                """
                INSERT INTO fact_associations (fact_a, fact_b, strength, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(fact_a, fact_b) DO UPDATE SET
                    strength   = MIN(1.0, fact_associations.strength + excluded.strength),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (a, b, float(delta)),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT strength FROM fact_associations WHERE fact_a = ? AND fact_b = ?",
                (a, b),
            ).fetchone()
            return row["strength"] if row else 0.0

    def get_associations(
        self, fact_id: int, limit: int = 10, min_strength: float = 0.0
    ) -> list[dict]:
        """Facts associated with ``fact_id``, strongest edge first.

        Each result is a normal fact dict plus a ``strength`` field. Superseded
        facts are excluded so spreading activation never resurfaces retired
        claims.
        """
        fid = int(fact_id)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT CASE WHEN fact_a = ? THEN fact_b ELSE fact_a END AS other_id,
                       strength
                FROM fact_associations
                WHERE (fact_a = ? OR fact_b = ?) AND strength >= ?
                ORDER BY strength DESC, updated_at DESC
                LIMIT ?
                """,
                (fid, fid, fid, float(min_strength), int(limit)),
            ).fetchall()
            out: list[dict] = []
            for r in rows:
                fact = self._conn.execute(
                    """
                    SELECT fact_id, content, category, tags, trust_score,
                           retrieval_count, helpful_count, created_at, updated_at,
                           fact_type
                    FROM facts
                    WHERE fact_id = ? AND superseded_by IS NULL
                    """,
                    (r["other_id"],),
                ).fetchone()
                if fact is not None:
                    d = self._row_to_dict(fact)
                    d["strength"] = r["strength"]
                    out.append(d)
            return out

    # ------------------------------------------------------------------
    # Consolidation / typed memory
    # ------------------------------------------------------------------

    def supersede_fact(self, old_id: int, new_id: int) -> bool:
        """Mark ``old_id`` as superseded by ``new_id`` (reality changed).

        A superseded fact is excluded from all retrieval but kept on disk for
        audit/undo. Its trust is also dropped so any stale cached reference
        deprioritizes it. Returns True if the old fact existed.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, category FROM facts WHERE fact_id = ?", (old_id,)
            ).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "UPDATE facts SET superseded_by = ?, trust_score = MIN(trust_score, 0.1), "
                "updated_at = CURRENT_TIMESTAMP WHERE fact_id = ?",
                (new_id, old_id),
            )
            self._conn.commit()
            # Drop the retired fact out of its category's HRR bank.
            self._rebuild_bank(row["category"])
            return True

    def list_profile_facts(self, limit: int = 30) -> list[dict]:
        """Durable 'profile' facts, highest-trust first (for always-on context)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fact_id, content, category, tags, trust_score,
                       retrieval_count, helpful_count, created_at, updated_at, fact_type
                FROM facts
                WHERE fact_type = 'profile' AND superseded_by IS NULL
                ORDER BY trust_score DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def semantic_neighbors(
        self, content: str, k: int = 5, min_sim: float = 0.80
    ) -> list[dict]:
        """Return the ``k`` most semantically-similar live facts to ``content``.

        Powers consolidation: before adding a fact we check whether it
        duplicates or updates an existing one. Requires an active embedder;
        returns [] otherwise (callers then fall back to plain insert).
        """
        embedder = getattr(self, "embedder", None)
        if embedder is None or not getattr(embedder, "enabled", False):
            return []
        try:
            qvec = embedder.embed_one(content)
        except Exception:
            return []
        if not qvec:
            return []
        from .embeddings import bytes_to_vec, cosine

        with self._lock:
            rows = self._conn.execute(
                "SELECT fact_id, content, embedding FROM facts "
                "WHERE embedding IS NOT NULL AND superseded_by IS NULL"
            ).fetchall()
        scored = []
        for r in rows:
            sim = cosine(qvec, bytes_to_vec(r["embedding"]))
            if sim >= min_sim:
                scored.append((sim, {"fact_id": r["fact_id"], "content": r["content"], "similarity": sim}))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [d for _s, d in scored[:k]]

    def add_fact_curated(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
        fact_type: str = "episodic",
        reconciler: "object | None" = None,
        importance: int = 5,
    ) -> dict:
        """Add a fact with consolidation: dedup + supersede via ``reconciler``.

        Checks semantically-similar existing facts and asks ``reconciler`` (a
        ``MemoryLLM``-like object with ``.reconcile(new, candidates)``) whether
        the new fact is a duplicate (skip), an update (supersede the target), or
        independent (insert). Falls back to a plain insert when no embedder /
        reconciler is available. Returns
        ``{"action", "fact_id", "superseded": [ids]}``.
        """
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")

        neighbors = self.semantic_neighbors(content, k=5, min_sim=0.82)

        # Exact/near-exact duplicate short-circuit (no LLM needed).
        for n in neighbors:
            if n.get("similarity", 0.0) >= 0.985 or n["content"].strip().lower() == content.lower():
                return {"action": "duplicate", "fact_id": n["fact_id"], "superseded": []}

        decision = None
        if neighbors and reconciler is not None and getattr(reconciler, "enabled", False):
            try:
                decision = reconciler.reconcile(content, neighbors)
            except Exception:
                decision = None

        if decision and decision.get("action") == "duplicate":
            target = decision.get("target_fact_id") or (neighbors[0]["fact_id"] if neighbors else None)
            return {"action": "duplicate", "fact_id": target, "superseded": []}

        # Insert the new fact.
        new_id = self.add_fact(
            content, category=category, tags=tags, fact_type=fact_type, importance=importance
        )

        superseded: list[int] = []
        if decision and decision.get("action") == "update":
            target = decision.get("target_fact_id")
            valid_targets = {n["fact_id"] for n in neighbors}
            if target in valid_targets and target != new_id:
                if self.supersede_fact(target, new_id):
                    superseded.append(target)

        return {"action": "update" if superseded else "new", "fact_id": new_id, "superseded": superseded}

    # ------------------------------------------------------------------
    # Reflection / importance
    # ------------------------------------------------------------------

    def set_importance(self, fact_id: int, importance: int) -> bool:
        """Set a fact's importance (1–10). Returns True if the row existed."""
        importance = max(1, min(10, int(importance)))
        with self._lock:
            cur = self._conn.execute(
                "UPDATE facts SET importance = ? WHERE fact_id = ?", (importance, fact_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def select_unreflected_facts(self, limit: int = 40) -> list[dict]:
        """Live episodic facts not yet folded into a reflection.

        Ordered by importance then recency so a reflection pass reasons over the
        most significant recent experience first.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT fact_id, content, category, importance, created_at
                FROM facts
                WHERE reflected = 0
                  AND superseded_by IS NULL
                  AND fact_type = 'episodic'
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def mark_reflected(self, fact_ids: list) -> None:
        """Flag facts as already considered by a reflection pass."""
        if not fact_ids:
            return
        with self._lock:
            placeholders = ",".join("?" * len(fact_ids))
            self._conn.execute(
                f"UPDATE facts SET reflected = 1 WHERE fact_id IN ({placeholders})",
                list(fact_ids),
            )
            self._conn.commit()

    def add_derived_fact(
        self,
        content: str,
        category: str = "insight",
        source_ids: "list | None" = None,
        importance: int = 8,
    ) -> int:
        """Store a reflection-derived insight as a durable 'profile' fact and
        record its provenance (which source facts it was synthesized from).

        Returns the derived fact_id (or the existing id if the content already
        exists). Insights are high-importance by default — they are the store's
        considered beliefs, not raw observations.
        """
        fact_id = self.add_fact(
            content, category=category, fact_type="profile", importance=importance
        )
        if source_ids:
            with self._lock:
                for sid in source_ids:
                    try:
                        self._conn.execute(
                            "INSERT OR IGNORE INTO fact_sources (derived_fact_id, source_fact_id) "
                            "VALUES (?, ?)",
                            (fact_id, int(sid)),
                        )
                    except (ValueError, TypeError, sqlite3.Error):
                        continue
                self._conn.commit()
        return fact_id

    def get_fact_sources(self, fact_id: int) -> list[dict]:
        """Return the evidence facts a derived insight was synthesized from.

        Powers "why do you believe that?" — the provenance chain behind an
        insight.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT f.fact_id, f.content, f.category, f.created_at
                FROM fact_sources fs
                JOIN facts f ON f.fact_id = fs.source_fact_id
                WHERE fs.derived_fact_id = ?
                """,
                (fact_id,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Graph reasoning (multi-hop entity association)
    # ------------------------------------------------------------------

    def graph_recall(
        self,
        entity_names: list,
        hops: int = 2,
        limit: int = 20,
    ) -> list[dict]:
        """Associative recall over the fact↔entity graph.

        Starting from ``entity_names``, walk the ``fact_entities`` graph up to
        ``hops`` steps: a seed entity's facts, then the entities those facts
        co-mention, then *their* facts, and so on. Answers "what do I know about
        X and everything connected to X" — associative recall a flat store
        can't do. Each result carries a ``hop`` distance (1 = directly about a
        seed entity). Superseded facts are excluded; results are ordered closest
        (lowest hop) then most-trusted first.
        """
        hops = max(1, int(hops))
        with self._lock:
            seeds: set[int] = set()
            for name in entity_names:
                name = str(name or "").strip()
                if not name:
                    continue
                row = self._conn.execute(
                    "SELECT entity_id FROM entities WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
                if row is not None:
                    seeds.add(int(row["entity_id"]))
            if not seeds:
                return []

            visited_entities = set(seeds)
            frontier = set(seeds)
            collected: dict[int, dict] = {}

            for hop in range(1, hops + 1):
                if not frontier:
                    break
                fe_ph = ",".join("?" * len(frontier))
                rows = self._conn.execute(
                    f"""
                    SELECT DISTINCT f.fact_id, f.content, f.category, f.tags,
                           f.trust_score, f.importance
                    FROM facts f
                    JOIN fact_entities fe ON fe.fact_id = f.fact_id
                    WHERE fe.entity_id IN ({fe_ph}) AND f.superseded_by IS NULL
                    """,
                    list(frontier),
                ).fetchall()
                new_ids: list[int] = []
                for r in rows:
                    fid = int(r["fact_id"])
                    if fid not in collected:
                        d = self._row_to_dict(r)
                        d["hop"] = hop
                        collected[fid] = d
                        new_ids.append(fid)
                # Expand the frontier to entities co-mentioned in the new facts.
                if new_ids and hop < hops:
                    f_ph = ",".join("?" * len(new_ids))
                    erows = self._conn.execute(
                        f"SELECT DISTINCT entity_id FROM fact_entities WHERE fact_id IN ({f_ph})",
                        new_ids,
                    ).fetchall()
                    next_frontier = {int(er["entity_id"]) for er in erows} - visited_entities
                    visited_entities |= next_frontier
                    frontier = next_frontier
                else:
                    frontier = set()

            results = sorted(
                collected.values(), key=lambda d: (d["hop"], -d.get("trust_score", 0.0))
            )
            return results[:limit]

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity candidates from text using simple regex rules.

        Rules applied (in order):
        1. Capitalized multi-word phrases  e.g. "John Doe"
        2. Double-quoted terms             e.g. "Python"
        3. Single-quoted terms             e.g. 'pytest'
        4. AKA patterns                    e.g. "Guido aka BDFL" -> two entities

        Returns a deduplicated list preserving first-seen order.
        """
        seen: set[str] = set()
        candidates: list[str] = []

        def _add(name: str) -> None:
            stripped = name.strip()
            if stripped and stripped.lower() not in seen:
                seen.add(stripped.lower())
                candidates.append(stripped)

        for m in _RE_CAPITALIZED.finditer(text):
            _add(m.group(1))

        for m in _RE_DOUBLE_QUOTE.finditer(text):
            _add(m.group(1))

        for m in _RE_SINGLE_QUOTE.finditer(text):
            _add(m.group(1))

        for m in _RE_AKA.finditer(text):
            _add(m.group(1))
            _add(m.group(2))

        return candidates

    def _resolve_entity(self, name: str) -> int:
        """Find an existing entity by name or alias (case-insensitive) or create one.

        Returns the entity_id.
        """
        # Exact (case-insensitive) name match. Use `= ? COLLATE NOCASE`, not
        # LIKE: a LIKE pattern lets `%`/`_` in an extracted entity name (e.g. a
        # quoted "100%") wildcard-match unrelated entities, silently merging
        # distinct entities and corrupting the fact→entity graph.
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])

        # Search aliases — stored comma-separated; a LIKE membership check
        # against ',<aliases>,'. Escape LIKE metacharacters in `name` (and add
        # ESCAPE) so a name containing `%`/`_` matches literally instead of as
        # a wildcard.
        safe_name = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        alias_row = self._conn.execute(
            r"""
            SELECT entity_id FROM entities
            WHERE ',' || aliases || ',' LIKE '%,' || ? || ',%' ESCAPE '\'
            """,
            (safe_name,),
        ).fetchone()
        if alias_row is not None:
            return int(alias_row["entity_id"])

        # Create new entity
        cur = self._conn.execute(
            "INSERT INTO entities (name) VALUES (?)", (name,)
        )
        self._conn.commit()
        return int(cur.lastrowid)  # type: ignore[return-value]

    def _link_fact_entity(self, fact_id: int, entity_id: int) -> None:
        """Insert into fact_entities, silently ignore if the link already exists."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO fact_entities (fact_id, entity_id)
            VALUES (?, ?)
            """,
            (fact_id, entity_id),
        )
        self._conn.commit()

    def _compute_hrr_vector(self, fact_id: int, content: str) -> None:
        """Compute and store HRR vector for a fact. No-op if numpy unavailable."""
        with self._lock:
            if not self._hrr_available:
                return

            # Get entities linked to this fact
            rows = self._conn.execute(
                """
                SELECT e.name FROM entities e
                JOIN fact_entities fe ON fe.entity_id = e.entity_id
                WHERE fe.fact_id = ?
                """,
                (fact_id,),
            ).fetchall()
            entities = [row["name"] for row in rows]

            vector = hrr.encode_fact(content, entities, self.hrr_dim)
            self._conn.execute(
                "UPDATE facts SET hrr_vector = ? WHERE fact_id = ?",
                (hrr.phases_to_bytes(vector), fact_id),
            )
            self._conn.commit()

    def _compute_embedding(self, fact_id: int, content: str) -> None:
        """Compute and store the semantic embedding for a fact.

        No-op when no embedder is configured or the backend is unavailable —
        the fact simply has a NULL embedding and retrieval falls back to the
        lexical + HRR signals for it.
        """
        embedder = getattr(self, "embedder", None)
        if embedder is None or not getattr(embedder, "enabled", False):
            return
        try:
            vec = embedder.embed_one(content)
        except Exception:
            return
        if not vec:
            return
        from .embeddings import vec_to_bytes

        with self._lock:
            self._conn.execute(
                "UPDATE facts SET embedding = ? WHERE fact_id = ?",
                (vec_to_bytes(vec), fact_id),
            )
            self._conn.commit()

    def _rebuild_bank(self, category: str) -> None:
        """Full rebuild of a category's memory bank from all its fact vectors."""
        with self._lock:
            if not self._hrr_available:
                return

            bank_name = f"cat:{category}"
            rows = self._conn.execute(
                "SELECT hrr_vector FROM facts WHERE category = ? AND hrr_vector IS NOT NULL",
                (category,),
            ).fetchall()

            if not rows:
                self._conn.execute("DELETE FROM memory_banks WHERE bank_name = ?", (bank_name,))
                self._conn.commit()
                return

            vectors = [hrr.bytes_to_phases(row["hrr_vector"]) for row in rows]
            bank_vector = hrr.bundle(*vectors)
            fact_count = len(vectors)

            # Check SNR
            hrr.snr_estimate(self.hrr_dim, fact_count)

            self._conn.execute(
                """
                INSERT INTO memory_banks (bank_name, vector, dim, fact_count, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bank_name) DO UPDATE SET
                    vector = excluded.vector,
                    dim = excluded.dim,
                    fact_count = excluded.fact_count,
                    updated_at = excluded.updated_at
                """,
                (bank_name, hrr.phases_to_bytes(bank_vector), self.hrr_dim, fact_count),
            )
            self._conn.commit()

    def rebuild_all_vectors(self, dim: int | None = None) -> int:
        """Recompute all HRR vectors + banks from text. For recovery/migration.

        Returns the number of facts processed.
        """
        with self._lock:
            if not self._hrr_available:
                return 0

            if dim is not None:
                self.hrr_dim = dim

            rows = self._conn.execute(
                "SELECT fact_id, content, category FROM facts"
            ).fetchall()

            categories: set[str] = set()
            for row in rows:
                self._compute_hrr_vector(row["fact_id"], row["content"])
                categories.add(row["category"])

            for category in categories:
                self._rebuild_bank(category)

            return len(rows)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict."""
        return dict(row)

    def close(self) -> None:
        """Release this instance's reference to the shared connection.

        The underlying connection is closed only when the last MemoryStore
        referencing the same database is closed, so closing one instance can
        never break sibling instances that still hold it. Idempotent.
        """
        if getattr(self, "_entry", None) is None:
            return
        with MemoryStore._shared_guard:
            entry = self._entry
            if entry is None:
                return
            entry["refs"] -= 1
            if entry["refs"] <= 0:
                try:
                    entry["conn"].close()
                finally:
                    MemoryStore._shared.pop(self._key, None)
            self._entry = None

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
