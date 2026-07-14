"""Inbound cron-fire token verification.

An external scheduler (any service you point at this agent — a cloud
scheduler, a self-hosted timer, cron-job.org, etc.) can trigger a due job by
POSTing ``/api/cron/fire`` with a short-lived signed JWT. This module verifies
that JWT before any job runs — the security boundary for remotely-triggered
job execution.

The scheduler signs the fire token with a private key; the agent verifies it
against the matching public key (a JWKS URL or an inline PEM configured under
``cron.fire.*``). The agent never holds the signing key, so a leaked agent
credential cannot mint fire tokens.

The verifier is pluggable (``get_fire_verifier``) so an alternate auth mode
(e.g. a direct per-job cron-key) can swap in later with no handler change.

Crypto is delegated to PyJWT (already a declared dependency) — we do NOT
hand-roll JWT verification.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("cron.fire_verifier")

# The purpose claim that scopes a token to the fire endpoint. A general agent
# JWT (without this claim) must NOT be replayable against /api/cron/fire.
_FIRE_PURPOSE = "cron_fire"


def verify_fire_token(
    *,
    token: str,
    expected_audience: str,
    jwks_or_key: Optional[str] = None,
    issuer: Optional[str] = None,
    leeway_seconds: int = 30,
) -> Optional[Dict[str, Any]]:
    """Verify an inbound cron-fire JWT. Return decoded claims, or None.

    Checks (all must pass):
      - signature against the configured JWKS (``jwks_or_key`` is a JWKS URL)
        or inline PEM — RS/ES family; symmetric secrets are rejected.
      - ``aud`` == ``expected_audience`` (this agent: ``agent:{instance_id}``).
      - ``exp`` / ``nbf`` within ``leeway_seconds``.
      - ``iss`` == ``issuer`` when an issuer is configured.
      - ``purpose`` == ``"cron_fire"`` — so a general agent JWT can't be
        replayed against the fire endpoint.

    Returns None (never raises) on any failure, so the handler can answer 401
    without leaking which check failed.
    """
    if not token or not expected_audience:
        return None
    if not jwks_or_key:
        # No verification key configured → cannot verify → refuse. We never
        # fall back to unsigned decode for a security boundary.
        logger.warning("cron fire: no JWKS/key configured; refusing token")
        return None

    try:
        import jwt
        from jwt import PyJWKClient

        # Resolve the signing key from the JWKS endpoint by the token's kid.
        signing_key = None
        if jwks_or_key.startswith("http://") or jwks_or_key.startswith("https://"):
            jwk_client = PyJWKClient(jwks_or_key)
            signing_key = jwk_client.get_signing_key_from_jwt(token).key
        else:
            # A PEM public key passed inline (test / pinned-key deployments).
            signing_key = jwks_or_key

        options = {"require": ["exp", "aud"]}
        decode_kwargs: Dict[str, Any] = dict(
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=expected_audience,
            leeway=leeway_seconds,
            options=options,
        )
        if issuer:
            decode_kwargs["issuer"] = issuer

        claims = jwt.decode(token, signing_key, **decode_kwargs)
    except Exception as e:
        logger.warning("cron fire: token verification failed: %s", e)
        return None

    if claims.get("purpose") != _FIRE_PURPOSE:
        logger.warning("cron fire: token missing/!=%s purpose claim", _FIRE_PURPOSE)
        return None

    return claims


def get_fire_verifier() -> Callable[..., Optional[Dict[str, Any]]]:
    """Return the active inbound-fire verifier.

    Default = the signed-JWT verifier. An alternate mode (e.g. a direct
    per-job cron-key) would return a different verifier here, selected by
    config — so the webhook handler never changes when the auth mode is
    swapped.
    """
    return verify_fire_token
