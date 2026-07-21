"""``hercules model`` subcommand parser.

Extracted verbatim from ``hercules_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_model_parser(subparsers, *, cmd_model: Callable) -> None:
    """Attach the ``model`` subcommand to ``subparsers``."""
    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help="Select default model and provider",
        description="Interactively select your inference provider and default model",
    )
    model_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Wipe the model picker disk cache and re-fetch every provider's live /v1/models list.",
    )
    model_parser.add_argument(
        "--portal-url", help="(deprecated) Ignored — removed with the Nous Portal device-code login; use `hercules model`.",
    )
    model_parser.add_argument(
        "--inference-url", help="(deprecated) Ignored — removed with the Nous Portal device-code login; use `hercules model`.",
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help="(deprecated) Ignored — surviving OAuth providers use built-in client ids.",
    )
    model_parser.add_argument(
        "--scope", default=None, help="(deprecated) Ignored — surviving OAuth providers use built-in scopes."
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically during device-code login",
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds for device-code login (default: 15)",
    )
    model_parser.add_argument(
        "--ca-bundle", help="(deprecated) Ignored — removed with the Nous Portal device-code login; use `hercules model`."
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help="(deprecated) Ignored — removed with the Nous Portal device-code login; use `hercules model`.",
    )
    model_parser.set_defaults(func=cmd_model)
