"""Optional LLM intelligence for the holographic memory store.

Three capabilities, all pluggable and graceful (disabled → the store falls
back to its non-LLM heuristics with zero behavior change):

  * extract_facts  — pull clean, atomic, durable facts from a conversation
                     instead of regex-dumping raw user turns (salience gating).
  * reconcile      — decide whether a new fact is a duplicate of, an update to
                     (supersedes), or independent from semantically-similar
                     existing facts (contradiction handling / consolidation).
  * expand_query   — rewrite a vague query into a focused retrieval query
                     (HyDE-style) so recall doesn't hinge on the user's phrasing.

Backends are OpenAI-compatible chat endpoints via the vendored ``openai`` SDK.
Inject ``chat_fn(system, user) -> str`` to exercise the intelligence paths with
a deterministic fake — no network, no API key.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Callable, List, Optional

logger = logging.getLogger("memory.holographic.llm")

_DEFAULT_MODEL = "gpt-4o-mini"


def _extract_json(text: str):
    """Best-effort parse of a JSON object/array from an LLM reply.

    Tolerates code fences and surrounding prose. Returns the parsed value or
    None. Never raises.
    """
    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` fences.
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # Fall back to the first {...} or [...] span.
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = s.find(opener), s.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(s[i : j + 1])
            except Exception:
                continue
    return None


class MemoryLLM:
    """Pluggable chat LLM for memory curation. Disabled unless creds/chat_fn."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = "",
        api_key: str = "",
        chat_fn: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self.model = model or _DEFAULT_MODEL
        self._base_url = base_url or ""
        self._api_key = api_key or ""
        self._chat_fn = chat_fn
        self._client = None
        self._client_tried = False
        self._broken = False

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "MemoryLLM":
        cfg = config or {}
        if cfg.get("curation_enabled", None) is False:
            return cls(chat_fn=None, api_key="", base_url="")
        key_env = str(cfg.get("curation_api_key_env", "") or "").strip()
        api_key = os.getenv(key_env, "").strip() if key_env else ""
        base_url = str(cfg.get("curation_base_url", "") or "").strip()
        if not api_key and not base_url:
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
        return cls(
            model=str(cfg.get("curation_model", "") or _DEFAULT_MODEL),
            base_url=base_url,
            api_key=api_key,
        )

    @property
    def enabled(self) -> bool:
        if self._broken:
            return False
        if self._chat_fn is not None:
            return True
        return bool(self._api_key or self._base_url)

    def _get_client(self):
        if self._client is not None or self._client_tried:
            return self._client
        self._client_tried = True
        try:
            from openai import OpenAI

            kwargs = {"api_key": self._api_key or "EMPTY"}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover
            logger.debug("memory llm: client init failed: %s", exc)
            self._client = None
        return self._client

    def _chat(self, system: str, user: str) -> Optional[str]:
        if not self.enabled:
            return None
        if self._chat_fn is not None:
            try:
                return self._chat_fn(system, user)
            except Exception as exc:
                logger.debug("memory llm: chat_fn failed: %s", exc)
                return None
        client = self._get_client()
        if client is None:
            self._broken = True
            return None
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.warning("memory llm: backend failed, disabling curation: %s", exc)
            self._broken = True
            return None

    # -- capabilities --------------------------------------------------------

    def extract_facts(self, conversation: str) -> Optional[List[dict]]:
        """Extract durable atomic facts from a conversation snippet.

        Returns a list of ``{"content", "category", "fact_type"}`` dicts, or
        None when the LLM is unavailable/unparseable. ``fact_type`` is
        "profile" for stable identity/preferences, "episodic" otherwise.
        """
        system = (
            "You extract durable memory from a conversation for an AI agent. "
            "Return ONLY a JSON array. Each item: "
            '{"content": str, "category": one of '
            '["user_pref","project","identity","fact","general"], '
            '"fact_type": "profile" or "episodic"}. '
            "Rules: one atomic fact per item, self-contained, third-person, no "
            "pronouns without referents. Capture stable preferences, decisions, "
            "identity, and durable facts. SKIP small talk, transient state, and "
            "anything not worth remembering next session. Use fact_type "
            '"profile" for stable identity/preferences, "episodic" otherwise. '
            "Return [] if nothing is worth saving."
        )
        raw = self._chat(system, conversation[:6000])
        data = _extract_json(raw or "")
        if not isinstance(data, list):
            return None
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            out.append(
                {
                    "content": content[:400],
                    "category": str(item.get("category", "general") or "general"),
                    "fact_type": "profile"
                    if str(item.get("fact_type", "")).lower() == "profile"
                    else "episodic",
                }
            )
        return out

    def reconcile(self, new_content: str, candidates: List[dict]) -> Optional[dict]:
        """Decide how a new fact relates to semantically-similar existing ones.

        candidates: ``[{"fact_id": int, "content": str}, ...]``.
        Returns ``{"action": "new"|"duplicate"|"update", "target_fact_id": int|None}``
        or None if unavailable. "duplicate" → skip the new fact; "update" →
        supersede target_fact_id with the new fact (reality changed).
        """
        if not candidates:
            return {"action": "new", "target_fact_id": None}
        listing = "\n".join(f'{c["fact_id"]}: {c["content"]}' for c in candidates)
        system = (
            "You maintain an AI agent's fact memory. Given a NEW fact and "
            "EXISTING similar facts, decide the relationship. Return ONLY JSON: "
            '{"action": "new"|"duplicate"|"update", "target_fact_id": int|null}. '
            '"duplicate" = the new fact says nothing the existing set lacks '
            "(discard it). "
            '"update" = the new fact supersedes/contradicts a specific existing '
            "fact because reality changed (set target_fact_id to that fact). "
            '"new" = genuinely independent information.'
        )
        user = f"NEW FACT:\n{new_content}\n\nEXISTING SIMILAR FACTS:\n{listing}"
        data = _extract_json(self._chat(system, user) or "")
        if not isinstance(data, dict):
            return None
        action = str(data.get("action", "new")).lower()
        if action not in ("new", "duplicate", "update"):
            action = "new"
        target = data.get("target_fact_id")
        try:
            target = int(target) if target is not None else None
        except (ValueError, TypeError):
            target = None
        return {"action": action, "target_fact_id": target}

    def expand_query(self, query: str) -> Optional[str]:
        """Rewrite a query into a focused retrieval string (HyDE-style).

        Returns a short hypothetical-answer/expansion to embed+search, or None.
        """
        system = (
            "Rewrite the user's message into a concise search query for a "
            "personal fact memory: a single declarative sentence describing "
            "what a stored answer would say. No preamble, one line."
        )
        out = self._chat(system, query[:1000])
        if not out:
            return None
        out = out.strip().splitlines()[0].strip() if out.strip() else ""
        return out or None
