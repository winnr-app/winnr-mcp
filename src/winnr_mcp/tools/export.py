"""Export tools — export email user data."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_export_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register data export MCP tools."""

    @mcp.tool()
    def winnr_export_email_users(
        format: str = "default",
        domains: list[str] | None = None,
        emails: list[str] | None = None,
        getAllDomains: bool = False,
    ) -> str:
        """Export email users to CSV and get a download URL.

        Supports formats for popular email outreach tools. Rate limited
        to 1 export every 5 seconds.

        Supported formats: default, smartlead, instantly, snov, saleshandy,
        quickmail, lemlist, woodpecker, reply, mailshake, gmass, yesware,
        mixmax, outreach, salesloft.

        Must specify at least one of: domains, emails, or getAllDomains.

        Args:
            format: Export format (default "default")
            domains: List of domain names to export users from
            emails: List of specific email addresses to export
            getAllDomains: If true, export all email users for the account
        """
        body: dict = {"format": format}
        if domains:
            body["domains"] = domains
        if emails:
            body["emails"] = emails
        if getAllDomains:
            body["getAllDomains"] = True
        response = client.post("/v1/export", json_body=body)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()
