"""Configuration loading from env vars and CLI args."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

from winnr_mcp.confirm import Confirmer


DEFAULT_API_URL = "https://api.winnr.app"
DEFAULT_TIMEOUT = 30
TOKEN_HELP_URL = "https://app.winnr.app/mcp"


@dataclass
class WinnrConfig:
    """MCP server configuration."""

    api_token: str
    api_url: str = DEFAULT_API_URL
    timeout: int = DEFAULT_TIMEOUT
    # ["read"] or ["read", "write"]. Starts from the --read-only flag and is
    # narrowed further at startup if the API reports the token itself is read-only.
    permissions: list[str] = field(default_factory=lambda: ["read", "write"])
    # Populated after initial /v1/account call:
    account_id: str | None = None
    account_name: str | None = None
    plan: str | None = None
    token_name: str | None = None
    # --no-purchases / WINNR_NO_PURCHASES: keep write access but never spend money.
    allow_purchases: bool = True
    # Signs the quote → confirm tokens for money tools. Random per process for
    # stdio; the remote server injects a shared secret so any instance can verify.
    confirmer: Confirmer = field(default_factory=Confirmer)
    # Longest a single winnr_wait_for_job call may block (remote: API Gateway's 30s).
    max_wait_seconds: int = 90

    @property
    def can_write(self) -> bool:
        return "write" in self.permissions


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes", "on")


def load_config(argv: list[str] | None = None) -> WinnrConfig:
    """Load configuration from CLI args and environment variables.

    Priority: CLI args > env vars > defaults.
    """
    parser = argparse.ArgumentParser(
        prog="winnr-mcp",
        description="Winnr MCP server — manage cold-email infrastructure from any MCP client.",
    )
    parser.add_argument("--token", help="Winnr API token (wnr_*). Overrides WINNR_API_TOKEN.")
    parser.add_argument("--api-url", help=f"API base URL (default: {DEFAULT_API_URL})")
    parser.add_argument(
        "--timeout", type=int, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Only register read tools (hides every tool that can change or buy anything)",
    )
    parser.add_argument(
        "--no-purchases",
        action="store_true",
        help="Keep write access but hide the four tools that charge the card",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_package_version()}"
    )
    args = parser.parse_args(argv)

    api_token = (args.token or os.environ.get("WINNR_API_TOKEN") or "").strip()
    if not api_token:
        print(
            "Error: WINNR_API_TOKEN environment variable or --token argument is required.",
            file=sys.stderr,
        )
        print(f"\nCreate a token and copy a ready-made config at {TOKEN_HELP_URL}", file=sys.stderr)
        sys.exit(1)

    if not api_token.startswith("wnr_"):
        print("Error: Invalid token format. Winnr API tokens start with 'wnr_'.", file=sys.stderr)
        print(f"Create one at {TOKEN_HELP_URL}", file=sys.stderr)
        sys.exit(1)

    api_url = args.api_url or os.environ.get("WINNR_API_URL") or DEFAULT_API_URL
    if not api_url.startswith(("http://", "https://")):
        print(f"Error: WINNR_API_URL must start with http:// or https:// (got {api_url!r}).", file=sys.stderr)
        sys.exit(1)

    timeout = args.timeout
    if timeout is None:
        raw_timeout = os.environ.get("WINNR_TIMEOUT", "").strip()
        try:
            timeout = int(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT
        except ValueError:
            print(f"Error: WINNR_TIMEOUT must be an integer number of seconds (got {raw_timeout!r}).", file=sys.stderr)
            sys.exit(1)
    if timeout <= 0:
        print("Error: timeout must be a positive number of seconds.", file=sys.stderr)
        sys.exit(1)

    read_only = args.read_only or _env_flag("WINNR_READ_ONLY")
    permissions = ["read"] if read_only else ["read", "write"]
    allow_purchases = not (args.no_purchases or _env_flag("WINNR_NO_PURCHASES"))

    confirm_secret = os.environ.get("WINNR_CONFIRM_SECRET", "").strip()
    confirmer = Confirmer(confirm_secret.encode()) if confirm_secret else Confirmer()

    return WinnrConfig(
        api_token=api_token,
        api_url=api_url.rstrip("/"),
        timeout=timeout,
        permissions=permissions,
        allow_purchases=allow_purchases,
        confirmer=confirmer,
    )


def _package_version() -> str:
    from winnr_mcp import __version__

    return __version__
