"""``hercules dashboard register`` — self-hosted dashboard OAuth client registration.

This command previously registered a self-hosted dashboard OAuth client against
the Nous Portal (``POST /api/oauth/self-hosted-client``), using the caller's
Nous Portal login to authenticate. The Nous Portal provider has been removed, so
there is no longer a portal to register against and the command is a no-op stub.
"""

from __future__ import annotations

import sys


def cmd_dashboard_register(args) -> None:
    """No-op stub: dashboard registration required the removed Nous Portal provider."""
    print(
        "✗ `hercules dashboard register` is no longer available.\n"
        "  It registered a self-hosted dashboard OAuth client with the Nous Portal,\n"
        "  which has been removed. To secure the dashboard, configure the bundled\n"
        "  self-hosted OIDC provider instead: set dashboard.oauth.self_hosted.issuer\n"
        "  + client_id in config.yaml (or HERCULES_DASHBOARD_OIDC_ISSUER +\n"
        "  HERCULES_DASHBOARD_OIDC_CLIENT_ID), or dashboard.basic_auth for a password."
    )
    sys.exit(1)
