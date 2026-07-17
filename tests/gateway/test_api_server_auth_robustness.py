"""Regression: APIServerAdapter._check_auth must fail closed (401), never 500.

hmac.compare_digest raises TypeError when a `str` argument contains non-ASCII
code points. A malformed Bearer token carrying such characters must be
rejected as invalid auth (401), not surface as an unhandled 500.
"""

from __future__ import annotations

from aiohttp.test_utils import make_mocked_request

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


def _adapter(key: str = "sk-strong-test-key-1234") -> APIServerAdapter:
    return APIServerAdapter(
        PlatformConfig(enabled=True, extra={"host": "127.0.0.1", "key": key}),
    )


def _req(authorization: str):
    return make_mocked_request(
        "POST", "/v1/chat/completions",
        headers={"Authorization": authorization},
    )


def test_correct_key_authorizes():
    a = _adapter("sk-strong-test-key-1234")
    assert a._check_auth(_req("Bearer sk-strong-test-key-1234")) is None


def test_wrong_ascii_key_rejected_401():
    a = _adapter("sk-strong-test-key-1234")
    resp = a._check_auth(_req("Bearer sk-wrong"))
    assert resp is not None
    assert resp.status == 401


def test_non_ascii_bearer_token_rejected_401_not_500():
    """A non-ASCII token would make hmac.compare_digest raise TypeError; the
    guard must catch it and return 401 instead of letting a 500 escape."""
    a = _adapter("sk-strong-test-key-1234")
    resp = a._check_auth(_req("Bearer éèê-not-ascii"))
    assert resp is not None
    assert resp.status == 401


def test_non_ascii_matching_bytes_still_rejected():
    """Even if the configured key itself were non-ASCII, a compare that would
    raise must not authorize — it fails closed."""
    a = _adapter("sk-é-key")
    resp = a._check_auth(_req("Bearer sk-é-key"))
    # Depending on Python's compare_digest behavior for equal non-ASCII str
    # inputs this may raise TypeError internally; the guard converts that to a
    # rejection, so the result must be a 401 response (fail closed), never a
    # crash.
    assert resp is not None
    assert resp.status == 401
