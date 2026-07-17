"""Regression: the RelayAdapter per-chat egress hint maps must stay bounded.

`_scope_by_chat` / `_dm_user_by_chat` / `_platform_by_chat` are written from
every inbound event and were never pruned on disconnect, so a long-lived
gateway fronting many chats leaked one entry per unique chat_id for the
process lifetime. They are now capped with a bounded-FIFO eviction policy
(evict the oldest entry on overflow); the values are cheap, re-learnable
routing hints, so eviction is safe.
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from gateway.relay.adapter import (
    RelayAdapter,
    _MAX_CHAT_HINT_ENTRIES,
    _remember_chat_hint,
)
from gateway.relay.descriptor import CONTRACT_VERSION, CapabilityDescriptor


def _make_desc(**kw) -> CapabilityDescriptor:
    base = dict(
        contract_version=CONTRACT_VERSION,
        platform="telegram",
        label="Telegram",
        max_message_length=4096,
        supports_draft_streaming=False,
        supports_edit=True,
        supports_threads=True,
        markdown_dialect="markdown_v2",
        len_unit="utf16",
        emoji="✈️",
        platform_hint="",
        pii_safe=False,
    )
    base.update(kw)
    return CapabilityDescriptor(**base)


def _adapter() -> RelayAdapter:
    return RelayAdapter(PlatformConfig(), _make_desc())


def _dm_event(chat_id: str, user_id: str):
    return SimpleNamespace(
        source=SimpleNamespace(
            chat_id=chat_id,
            platform=Platform.DISCORD,
            scope_id=None,
            user_id=user_id,
        )
    )


def _scoped_event(chat_id: str, scope_id: str):
    return SimpleNamespace(
        source=SimpleNamespace(
            chat_id=chat_id,
            platform=Platform.DISCORD,
            scope_id=scope_id,
            user_id="ignored-because-scoped",
        )
    )


def test_remember_chat_hint_evicts_oldest_on_overflow():
    m: dict[str, str] = {}
    for i in range(_MAX_CHAT_HINT_ENTRIES):
        _remember_chat_hint(m, f"chat-{i}", f"v-{i}")
    assert len(m) == _MAX_CHAT_HINT_ENTRIES
    # The very first inserted key is still present at capacity.
    assert "chat-0" in m
    # One more distinct key evicts the oldest (chat-0), not any other.
    _remember_chat_hint(m, "chat-overflow", "v")
    assert len(m) == _MAX_CHAT_HINT_ENTRIES
    assert "chat-0" not in m
    assert "chat-overflow" in m
    assert "chat-1" in m  # second-oldest survives


def test_remember_chat_hint_update_in_place_does_not_evict():
    m: dict[str, str] = {}
    for i in range(_MAX_CHAT_HINT_ENTRIES):
        _remember_chat_hint(m, f"chat-{i}", "old")
    # Re-writing an existing key must not trip eviction or grow the map.
    _remember_chat_hint(m, "chat-0", "new")
    assert len(m) == _MAX_CHAT_HINT_ENTRIES
    assert m["chat-0"] == "new"
    assert "chat-1" in m


def test_dm_egress_map_stays_bounded_across_many_chats():
    a = _adapter()
    overflow = _MAX_CHAT_HINT_ENTRIES + 500
    for i in range(overflow):
        a._capture_scope(_dm_event(f"dm-{i}", f"user-{i}"))
    assert len(a._dm_user_by_chat) <= _MAX_CHAT_HINT_ENTRIES
    # Most-recent chat's hint is retained (it was learned last).
    assert a._dm_user_by_chat[f"dm-{overflow - 1}"] == f"user-{overflow - 1}"


def test_scope_and_platform_maps_stay_bounded_across_many_chats():
    a = _adapter()
    overflow = _MAX_CHAT_HINT_ENTRIES + 500
    for i in range(overflow):
        a._capture_scope(_scoped_event(f"c-{i}", f"scope-{i}"))
    assert len(a._scope_by_chat) <= _MAX_CHAT_HINT_ENTRIES
    assert len(a._platform_by_chat) <= _MAX_CHAT_HINT_ENTRIES
    assert a._scope_by_chat[f"c-{overflow - 1}"] == f"scope-{overflow - 1}"
