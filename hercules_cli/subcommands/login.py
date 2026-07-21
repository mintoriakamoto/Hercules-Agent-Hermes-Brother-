"""``hercules login`` subcommand parser.

Extracted verbatim from ``hercules_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_login_parser(subparsers, *, cmd_login: Callable) -> None:
    """Attach the deprecated ``login`` subcommand to ``subparsers``.

    ``hercules login`` was removed in favor of ``hercules auth`` / ``hercules model``
    (the runtime handler in ``hercules_cli/auth.py::login_command`` just prints a
    deprecation message and exits).  The subparser is kept registered so that
    old scripts/aliases invoking ``hercules login [--flags]`` still receive the
    actionable deprecation message rather than an argparse ``invalid choice:
    'login'`` error — but:

    - The subparser is registered WITHOUT a ``help=`` kwarg so the row is
      omitted from ``hercules --help`` (argparse only lists subcommands that
      have a help string).  This hides a command that no longer works (#24756)
      without the ``help=argparse.SUPPRESS`` ``==SUPPRESS==`` leak that
      argparse emits for a top-level subparser on Python 3.12+.
    - ``--provider`` accepts ANY value (no ``choices=``) so that, e.g.,
      ``hercules login --provider anthropic`` reaches the deprecation handler and
      gets pointed at ``hercules model`` instead of crashing in argparse with
      ``invalid choice: 'anthropic'`` before the handler can run.
    """
    login_parser = subparsers.add_parser(
        "login",
        description=(
            "Deprecated. Use `hercules auth` to manage credentials, "
            "`hercules model` to select a provider, or `hercules setup` for full setup."
        ),
    )
    # No ``choices=`` on purpose — the handler is a deprecation notice that
    # ignores the value, and a restrictive list would reject providers the user
    # legitimately wants (e.g. ``anthropic``) with an argparse error before the
    # friendly redirect message is ever printed.
    login_parser.add_argument(
        "--provider",
        default=None,
        help="(deprecated) Provider name; ignored — see `hercules model`",
    )
    login_parser.add_argument(
        "--portal-url", help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`."
    )
    login_parser.add_argument(
        "--inference-url", help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`.",
    )
    login_parser.add_argument(
        "--client-id", default=None, help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`."
    )
    login_parser.add_argument("--scope", default=None, help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`.")
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`.",
    )
    login_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`.",
    )
    login_parser.add_argument(
        "--ca-bundle", help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`."
    )
    login_parser.add_argument(
        "--insecure",
        action="store_true",
        help="(deprecated) Ignored — `hercules login` was removed; use `hercules model` / `hercules auth`.",
    )
    login_parser.set_defaults(func=cmd_login)
