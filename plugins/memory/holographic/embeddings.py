"""Optional semantic embeddings for the holographic memory store.

The store's other retrieval signals (FTS5, Jaccard, HRR) are all lexical or
structural — they match on shared tokens/entities, not meaning. This module
adds a genuine *semantic* signal: a dense embedding per fact, compared by
cosine similarity, so "where do we deploy?" can recall "production runs on
Fly.io" with no shared words.

Design goals:
  * Pluggable + graceful. When no embedding backend is configured/reachable,
    ``Embedder.enabled`` is False and the store/retriever fall back to the
    existing lexical+HRR blend with ZERO behavior change.
  * Cheap to test. Inject ``embed_fn`` (a ``list[str] -> list[list[float]]``
    callable) to exercise the semantic path with a deterministic fake — no
    network, no API key.
  * Self-healing circuit breaker. A backend that errors is disabled for the
    rest of the process instead of being hammered once per write/query.

Backends are OpenAI-compatible ``/v1/embeddings`` endpoints (OpenAI, a local
vLLM/Ollama server, etc.), reached via the already-vendored ``openai`` SDK.
"""

from __future__ import annotations

import logging
import math
import os
import struct
from typing import Callable, List, Optional, Sequence

logger = logging.getLogger("memory.holographic.embeddings")

# Default model + dims for the auto-enable path (OpenAI text-embedding-3-small).
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIMS = 1536


def vec_to_bytes(vec: Sequence[float]) -> bytes:
    """Pack a float vector into little-endian float32 bytes for BLOB storage."""
    return struct.pack(f"<{len(vec)}f", *vec)


def bytes_to_vec(blob: bytes) -> List[float]:
    """Unpack float32 BLOB bytes back into a list of floats."""
    if not blob:
        return []
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 when either vector is empty/degenerate.

    Pure-Python (no numpy dependency) so the semantic path works even in the
    numpy-less fallback build where HRR is disabled.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class Embedder:
    """Turns text into dense semantic vectors via an OpenAI-compatible endpoint.

    Construct from config (``Embedder.from_config``) or inject ``embed_fn`` for
    tests. When neither a working client nor an ``embed_fn`` is present,
    ``enabled`` is False and every ``embed`` call returns ``None``.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        dims: int = _DEFAULT_DIMS,
        base_url: str = "",
        api_key: str = "",
        embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    ) -> None:
        self.model = model or _DEFAULT_MODEL
        self.dims = int(dims or _DEFAULT_DIMS)
        self._base_url = base_url or ""
        self._api_key = api_key or ""
        self._embed_fn = embed_fn
        self._client = None
        self._client_tried = False
        self._broken = False  # circuit breaker: a backend error disables it

    # -- construction --------------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "Embedder":
        """Build an Embedder from the holographic provider config.

        Recognized keys (all optional): ``embedding_model``,
        ``embedding_dims``, ``embedding_base_url``, ``embedding_api_key_env``,
        and ``embedding_enabled`` (tri-state: unset = auto).

        Auto-enable rule: if not explicitly disabled and an API key is
        resolvable (``embedding_api_key_env`` → that env var, else
        ``OPENAI_API_KEY``), the semantic path turns on with a sensible
        default model. Otherwise it stays off (lexical-only).
        """
        cfg = config or {}
        enabled = cfg.get("embedding_enabled", None)
        if enabled is False:
            return cls(embed_fn=None, api_key="", base_url="")  # disabled

        key_env = str(cfg.get("embedding_api_key_env", "") or "").strip()
        api_key = ""
        if key_env:
            api_key = os.getenv(key_env, "").strip()
        base_url = str(cfg.get("embedding_base_url", "") or "").strip()
        if not api_key and not base_url:
            # Auto path: use OPENAI_API_KEY if present.
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
        # Explicit enable with no resolvable creds and no base_url → stays off.
        return cls(
            model=str(cfg.get("embedding_model", "") or _DEFAULT_MODEL),
            dims=int(cfg.get("embedding_dims", 0) or _DEFAULT_DIMS),
            base_url=base_url,
            api_key=api_key,
        )

    # -- capability ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when a semantic backend is available (client creds or embed_fn)."""
        if self._broken:
            return False
        if self._embed_fn is not None:
            return True
        return bool(self._api_key or self._base_url)

    def _get_client(self):
        if self._client is not None or self._client_tried:
            return self._client
        self._client_tried = True
        try:
            from openai import OpenAI

            kwargs = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            else:
                kwargs["api_key"] = "EMPTY"  # local servers ignore the key
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover - import/config failure
            logger.debug("embeddings: client init failed: %s", exc)
            self._client = None
        return self._client

    # -- embedding -----------------------------------------------------------

    def embed(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Embed a batch of texts. Returns a list of vectors, or None if the
        semantic path is unavailable / the backend errored."""
        if not texts or not self.enabled:
            return None
        if self._embed_fn is not None:
            try:
                out = self._embed_fn(list(texts))
                return [list(map(float, v)) for v in out] if out else None
            except Exception as exc:  # a fake/injected fn should not crash writes
                logger.debug("embeddings: embed_fn failed: %s", exc)
                return None

        client = self._get_client()
        if client is None:
            self._broken = True
            return None
        try:
            resp = client.embeddings.create(model=self.model, input=list(texts))
            vectors = [list(map(float, item.embedding)) for item in resp.data]
            if vectors and vectors[0]:
                self.dims = len(vectors[0])
            return vectors
        except Exception as exc:
            # Trip the breaker: one failure disables the semantic path for the
            # rest of the process so we never hammer a dead endpoint.
            logger.warning("embeddings: backend failed, disabling semantic path: %s", exc)
            self._broken = True
            return None

    def embed_one(self, text: str) -> Optional[List[float]]:
        """Embed a single text; convenience over ``embed``."""
        out = self.embed([text])
        return out[0] if out else None
