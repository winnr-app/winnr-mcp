"""Configuration loading from env vars and CLI args."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field


DEFAULT_API_URL = "https://api.winnr.app"
DEFAULT_TIMEOUT = 30


@dataclass
class WinnrConfig:
    """MCP server configuration."""

    api_token: str
    api_url: str = DEFAULT_API_URL
    timeout: int = DEFAULT_TIMEOUT
    # Populated after initial /v1/account call:
    permissions: list[str] = field(default_factory=lambda: ["read", "write"])
    account_id: str | None = None
    plan: str | None = None


def load_config() -> WinnrConfig:
    """Load configuration from CLI args and environment variables.

    Priority: CLI args > env vars > defaults.
    """
    parser = argparse.ArgumentParser(description="Winnr MCP Server")
    parser.add_argument("--token", help="Winnr API token (wnr_*)")
    parser.add_argument("--api-url", help=f"API base URL (default: {DEFAULT_API_URL})")
    parser.add_argument("--timeout", type=int, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})")
    args = parser.parse_args()

    api_token = args.token or os.environ.get("WINNR_API_TOKEN")
    if not api_token:
        print("Error: WINNR_API_TOKEN environment variable or --token argument is required.", file=sys.stderr)
        print("\nGet your API token at https://app.winnr.app → Settings → API Tokens", file=sys.stderr)
        sys.exit(1)

    if not api_token.startswith("wnr_"):
        print("Error: Invalid token format. Winnr API tokens start with 'wnr_'.", file=sys.stderr)
        sys.exit(1)

    api_url = args.api_url or os.environ.get("WINNR_API_URL", DEFAULT_API_URL)
    timeout = args.timeout or int(os.environ.get("WINNR_TIMEOUT", str(DEFAULT_TIMEOUT)))

    return WinnrConfig(
        api_token=api_token,
        api_url=api_url.rstrip("/"),
        timeout=timeout,
    )
