"""Account tools — get account info and usage stats."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_account_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register account-related MCP tools."""

    @mcp.tool()
    def winnr_get_account() -> str:
        """Get your Winnr account details including plan, limits, and subscription status.

        Returns account ID, name, email, current plan, domain limit, email user limit,
        and creation date.
        """
        response = client.get("/v1/account")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_usage() -> str:
        """Get current usage statistics for your Winnr account.

        Returns how many domains and email users you've used vs. your plan limits.
        Useful for checking capacity before creating new resources.
        """
        response = client.get("/v1/account/usage")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()
