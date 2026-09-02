"""MCP Server definition with tool and resource registration."""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from winnr_mcp import __version__
from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools.account import register_account_tools
from winnr_mcp.tools.domains import register_domain_tools
from winnr_mcp.tools.email_users import register_email_user_tools
from winnr_mcp.tools.export import register_export_tools
from winnr_mcp.tools.inbox import register_inbox_tools
from winnr_mcp.tools.jobs import register_job_tools
from winnr_mcp.tools.prewarmed import register_prewarmed_tools
from winnr_mcp.tools.warming import register_warming_tools
from winnr_mcp.tools.webhooks import register_webhook_tools

# Sent to the client as the server's `instructions`. Hosts typically place it
# in the model's system context, so it carries the rules an operator would
# want every assistant to follow before touching real infrastructure.
SERVER_INSTRUCTIONS = """\
Winnr provisions cold-email infrastructure: domains, mailboxes (email users), DNS, email warming, \
a marketplace of pre-warmed domains, an account-wide inbox, and outbound webhooks. Every tool here \
is a thin wrapper over the Winnr REST API for ONE customer account (the API token's account).

How to work with it:
- IDs: domains, email users, jobs and webhooks are addressed by their `id` from the list tools. \
Mailboxes are also addressable by full email address in inbox tools (the `mailbox` argument).
- Async: purchases, domain setup, mailbox creation and deletion return a `job_id`. Poll \
winnr_get_job every 10-20 seconds until status is `completed` or `error`; new domains take a few \
minutes (DNS + mail server), mailboxes about a minute.
- Money: winnr_purchase_domains, winnr_purchase_prewarmed, winnr_purchase_prewarmed_batch and \
winnr_enable_warming charge the customer's card. State the exact domains/mailboxes and the price \
and get an explicit yes from the user before calling them. Never retry a purchase after a timeout \
without first checking winnr_list_jobs — the charge may have gone through.
- Deletes are permanent: winnr_delete_domain, winnr_delete_email_user, winnr_delete_message, \
winnr_delete_webhook and winnr_cancel_prewarmed. Confirm first.
- Domain names: suggest names that look like a real company (brand + short word, e.g. \
acmehq.com, tryacme.com, acmeteam.com). Do not put outreach/blast/bulk/mail/marketing in a name; \
those get flagged by spam filters. Check availability with winnr_search_domains_bulk before \
proposing prices.
- Cold-email hygiene: 2-5 mailboxes per domain, warm every new mailbox for 2-3 weeks before \
sending, and keep sends per mailbox modest (10-30/day early on).
- Read-only tokens: if a write tool is missing from the tool list, the token is read-only. \
The user can create a read/write token at https://app.winnr.app/mcp.
- Passwords are never returned by list tools. For credentials use winnr_export_email_users, \
which returns a short-lived CSV download link.
"""


def create_server(config: WinnrConfig) -> FastMCP:
    """Create and configure the Winnr MCP server."""
    # stderr is the only channel we have (stdout is the protocol). Keep it to
    # real problems: httpx logs every request at INFO and FastMCP every tool
    # call, which floods host logs (Claude Desktop shows them as "errors").
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    client = WinnrClient(config)

    # Validate the token, learn the account and narrow permissions if the
    # token itself is read-only. Must happen BEFORE tools are registered.
    _discover_permissions(client, config)

    mcp = FastMCP(
        "Winnr",
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://winnr.app/mcp.html",
        log_level="WARNING",
    )

    # Register all tool modules
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_prewarmed_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    register_webhook_tools(mcp, client, config)

    tool_count = len(mcp._tool_manager.list_tools())
    scope = "read/write" if config.can_write else "read-only"
    who = config.account_name or config.account_id or "unknown account"
    # stderr only — stdout is the MCP stdio transport and must stay clean.
    print(
        f"winnr-mcp {__version__}: {tool_count} tools registered ({scope}) for {who}",
        file=sys.stderr,
    )

    return mcp


def _discover_permissions(client: WinnrClient, config: WinnrConfig) -> None:
    """Validate the token and populate account info via GET /v1/account.

    If the token is rejected (401) we fail fast with a clear message — a server
    that starts but returns auth errors on every call is the worst onboarding
    experience. Any other failure (network, 5xx) still starts the server so a
    transient outage does not break the client's MCP config.

    Permissions: the API reports the calling token's own scope in
    `data.api_token.permissions`. A read-only token narrows the registered
    tool set to read tools regardless of the --read-only flag, so the write
    tools are hidden instead of failing with 403 on every use.
    """
    response = client.get("/v1/account")
    if response.status_code == 401:
        print(
            "winnr-mcp: the API rejected WINNR_API_TOKEN (401). It may have been revoked or "
            "mistyped. Create a new token at https://app.winnr.app/mcp",
            file=sys.stderr,
        )
        sys.exit(1)
    if not response.ok or not isinstance(response.data, dict):
        print(
            f"winnr-mcp: could not verify the token against {config.api_url} "
            f"({response.error_message}). Starting anyway.",
            file=sys.stderr,
        )
        return

    data = response.data
    config.account_id = data.get("id")
    config.account_name = data.get("name")
    config.plan = data.get("plan")

    token_info = data.get("api_token")
    if isinstance(token_info, dict):
        config.token_name = token_info.get("name")
        token_perms = token_info.get("permissions")
        if isinstance(token_perms, list) and "write" not in token_perms and config.can_write:
            config.permissions = ["read"]
            print(
                "winnr-mcp: token is read-only — write tools are hidden.",
                file=sys.stderr,
            )
