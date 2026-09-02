"""Email user tools — create, manage, and delete mailboxes."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import (
    DESTRUCTIVE,
    READ,
    WRITE,
    WRITE_IDEMPOTENT,
    clamp,
    clean_domain,
    tool_error,
)

MAX_BULK_USERS = 100


def register_email_user_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register email user (mailbox) MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    def winnr_list_email_users(
        limit: int = 25,
        cursor: str | None = None,
        domain: str | None = None,
    ) -> str:
        """List email users (mailboxes) in the account, optionally for one domain.

        Returns id, username, domain, full_address, display name, status, type,
        daily send limit, and IMAP/SMTP host+port. Passwords are never included —
        use winnr_export_email_users for credentials.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
            domain: Optional domain name to filter by (e.g. "example.com")
        """
        params: dict = {"limit": clamp(limit, 1, 100)}
        if cursor:
            params["cursor"] = cursor
        if domain:
            params["filter[domain]"] = clean_domain(domain)
        return client.get("/v1/email-users", params=params).render()

    @mcp.tool(annotations=READ)
    def winnr_get_email_user(user_id: str) -> str:
        """Get one email user (mailbox) by id.

        Returns username, domain, full address, display name, status, IMAP/SMTP
        connection details and timestamps (no password).

        Args:
            user_id: The email user ID (from winnr_list_email_users)
        """
        return client.get(f"/v1/email-users/{user_id}").render()

    # ── Write tools ─────────────────────────────────────────────────────

    if not config.can_write:
        return

    @mcp.tool(annotations=WRITE)
    def winnr_create_email_user(
        username: str,
        domain: str,
        name: str = "",
        password: str | None = None,
    ) -> str:
        """Create one mailbox on a domain the account already owns.

        Provisioning is asynchronous: returns a job_id, poll winnr_get_job. The
        mailbox is usable about a minute later. If no password is given a secure
        one is generated; retrieve it with winnr_export_email_users.

        Counts against the account's email user limit (except on pre-warmed
        domains). Regular domains allow up to 10 mailboxes; 2-5 per domain is the
        deliverability-safe range.

        Args:
            username: Local part before the @ (e.g. "john.doe"), lowercase
            domain: Domain name (e.g. "example.com") — must already be active
            name: Display name shown to recipients (e.g. "John Doe")
            password: Optional password (min 8 chars); auto-generated if omitted
        """
        if not username or not username.strip():
            return tool_error("username is required")
        body: dict = {
            "username": username.strip().lower(),
            "domain": clean_domain(domain),
            "name": name or "",
        }
        if password:
            body["password"] = password
        return client.post("/v1/email-users", json_body=body).render()

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_update_email_user(
        user_id: str,
        name: str | None = None,
        password: str | None = None,
    ) -> str:
        """Update a mailbox's display name and/or password.

        Changing the password breaks any sequencer already connected with the old
        one — warn the user before rotating a mailbox that is in use.

        Args:
            user_id: The email user ID
            name: New display name
            password: New password (min 8 chars)
        """
        body: dict = {}
        if name is not None:
            body["name"] = name
        if password is not None:
            body["password"] = password
        if not body:
            return tool_error("At least one field (name or password) must be provided.")
        return client.patch(f"/v1/email-users/{user_id}", json_body=body).render()

    @mcp.tool(annotations=DESTRUCTIVE)
    def winnr_delete_email_user(user_id: str) -> str:
        """Permanently delete a mailbox and all mail in it. Cannot be undone.

        Queued asynchronously (returns a job_id). Warming on the mailbox is torn
        down and billing for it stops. Mailboxes on pre-warmed domains cannot be
        deleted individually — cancel the whole domain with winnr_cancel_prewarmed.

        Args:
            user_id: The email user ID to delete
        """
        return client.delete(f"/v1/email-users/{user_id}").render()

    @mcp.tool(annotations=WRITE)
    def winnr_bulk_create_email_users(domain: str, users: list[dict]) -> str:
        """Create up to 100 mailboxes on ONE domain in a single job.

        Asynchronous — returns a job_id to poll with winnr_get_job. For several
        domains, call once per domain. The per-domain cap (10 mailboxes on regular
        domains, 5 on subdomain-strategy children) still applies.

        Args:
            domain: Domain name the mailboxes go on (e.g. "example.com")
            users: 1-100 user objects, each with:
                - username (str, required): local part (e.g. "john.doe")
                - name (str, optional): display name
                - password (str, optional): auto-generated if omitted
                - footer (str, optional): signature footer
        """
        if not users:
            return tool_error("users must contain at least one entry")
        if len(users) > MAX_BULK_USERS:
            return tool_error(f"Maximum {MAX_BULK_USERS} users per call, got {len(users)}")
        cleaned = []
        for i, u in enumerate(users):
            if not isinstance(u, dict) or not (u.get("username") or "").strip():
                return tool_error(f"users[{i}] must be an object with a non-empty 'username'")
            entry = {"username": u["username"].strip().lower(), "name": (u.get("name") or "").strip()}
            if u.get("password"):
                entry["password"] = u["password"]
            if u.get("footer"):
                entry["footer"] = u["footer"]
            cleaned.append(entry)
        body = {"domain": clean_domain(domain), "users": cleaned}
        return client.post("/v1/email-users/bulk", json_body=body).render()
