"""Account tools — get account info and usage stats."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import READ


def register_account_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register account-related MCP tools."""

    @mcp.tool(annotations=READ)
    def winnr_get_account() -> str:
        """Get the Winnr account this token belongs to: plan, limits and subscription status.

        Returns account id, name, email, current plan, domain limit/used, email user
        limit/used, domain credits, subscription status, and (under `api_token`)
        the name and permissions of the API token in use. Call this first in a
        session to learn the account's capacity before proposing changes.
        """
        return client.get("/v1/account").render()

    @mcp.tool(annotations=READ)
    def winnr_get_usage() -> str:
        """Get current usage vs. plan limits (domains, email users, pre-warmed addresses).

        Useful for checking capacity before creating resources. Pre-warmed addresses
        are a separate pool billed at $3/address/month and do not count against the
        email user limit. Domain slots are free and self-serve — never tell a user to
        upgrade for more domains.
        """
        return client.get("/v1/account/usage").render()
