"""MCP Server definition with tool and resource registration."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools.account import register_account_tools
from winnr_mcp.tools.domains import register_domain_tools
from winnr_mcp.tools.email_users import register_email_user_tools
from winnr_mcp.tools.export import register_export_tools
from winnr_mcp.tools.inbox import register_inbox_tools
from winnr_mcp.tools.jobs import register_job_tools
from winnr_mcp.tools.warming import register_warming_tools


def create_server(config: WinnrConfig) -> FastMCP:
    """Create and configure the Winnr MCP server."""
    mcp = FastMCP(
        "Winnr",
        dependencies=["httpx"],
    )
    client = WinnrClient(config)

    # Discover token permissions by calling the API
    _discover_permissions(client, config)

    # Register all tool modules
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)

    return mcp


def _discover_permissions(client: WinnrClient, config: WinnrConfig) -> None:
    """Validate token and populate account info via GET /v1/account.

    If the token is invalid, we still start (tools will return auth errors).
    Permission gating is controlled by --read-only flag or WINNR_READ_ONLY env var.
    """
    response = client.get("/v1/account")
    if response.ok and response.data:
        config.account_id = response.data.get("id")
        config.plan = response.data.get("plan")
