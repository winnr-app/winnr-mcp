"""Export tools — export email user credentials."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import READ, WRITE_IDEMPOTENT, clean_domain, is_str, tool_error

# Mirrors SUPPORTED_FORMATS in the API. Kept here so a typo fails locally with
# the full list instead of a round trip.
EXPORT_FORMATS = (
    "default", "smartlead", "instantly", "snov", "saleshandy", "plusvibe",
    "emailbison", "manyreach", "warmy", "warmysender", "reply.io", "smartreach",
    "reachinbox", "masterinbox", "leadengine", "woodpecker", "maillead",
    "salesforge", "aicloser", "sendkit", "mailshake", "pipelime",
)


def register_export_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register data export MCP tools."""

    @mcp.tool(annotations=READ)
    def winnr_list_export_formats() -> str:
        """List the CSV export formats the API supports (one per sequencer/outreach tool).

        Use when the user names a tool that is not in the winnr_export_email_users
        list, to check whether a dedicated format exists.
        """
        return client.get("/v1/export/formats").render()

    if not config.can_write:
        return

    # Registered for read/write tokens only: the API requires write scope for
    # POST /v1/export because the CSV contains mailbox passwords.
    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_export_email_users(
        format: str = "default",
        domains: list[str] | None = None,
        emails: list[str] | None = None,
        all_domains: bool = False,
    ) -> str:
        """Export mailbox credentials (address, password, IMAP/SMTP hosts) to CSV.

        Returns a download URL valid for 15 minutes — this is the ONLY way to get
        passwords out of Winnr; list tools never include them. Needs a read/write
        token. Give the user the link rather than pasting credentials into the
        conversation.

        Formats: default, smartlead, instantly, snov, saleshandy, plusvibe,
        emailbison, manyreach, warmy, warmysender, reply.io, smartreach, reachinbox,
        masterinbox, leadengine, woodpecker, maillead, salesforge, aicloser, sendkit,
        mailshake, pipelime. Each matches that tool's CSV import columns.

        Rate limited to one export every 5 seconds. Exactly one selector is needed:
        domains, emails, or all_domains=true.

        Args:
            format: Export format (default "default")
            domains: Domain names whose mailboxes to export
            emails: Specific email addresses to export
            all_domains: Export every mailbox in the account
        """
        fmt = (format or "default").strip().lower()
        if fmt not in EXPORT_FORMATS:
            return tool_error(
                f"Unknown export format '{format}'. Supported: {', '.join(EXPORT_FORMATS)}"
            )
        if not (domains or emails or all_domains):
            return tool_error("Provide domains, emails, or all_domains=true.")
        body: dict = {"format": fmt}
        if domains:
            body["domains"] = [clean_domain(d) for d in domains if is_str(d)]
        if emails:
            body["emails"] = [e.strip().lower() for e in emails if is_str(e)]
        if all_domains:
            body["getAllDomains"] = True
        return client.post("/v1/export", json_body=body).render()
