"""Email user tools — create, manage, and delete mailboxes."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_email_user_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register email user (mailbox) MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool()
    def winnr_list_email_users(
        limit: int = 25,
        cursor: str | None = None,
        domain: str | None = None,
    ) -> str:
        """List email users (mailboxes) in your account.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
            domain: Optional domain name to filter by (e.g., "example.com")
        """
        params: dict = {"limit": min(max(limit, 1), 100)}
        if cursor:
            params["cursor"] = cursor
        if domain:
            params["filter[domain]"] = domain
        response = client.get("/v1/email-users", params=params)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_email_user(user_id: str) -> str:
        """Get detailed information about a specific email user (mailbox).

        Returns username, domain, full email address, display name, status,
        and creation date.

        Args:
            user_id: The email user ID (from winnr_list_email_users)
        """
        response = client.get(f"/v1/email-users/{user_id}")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    # ── Write tools ─────────────────────────────────────────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_create_email_user(
            username: str,
            domain: str,
            name: str = "",
            password: str | None = None,
        ) -> str:
            """Create a new email user (mailbox) on a domain.

            The mailbox is created asynchronously — a job is queued and a job ID
            is returned. Use winnr_get_job to track progress.

            If no password is provided, a secure one is generated automatically.

            Args:
                username: Local part of the email (e.g., "john.doe")
                domain: Domain name (e.g., "example.com")
                name: Display name (e.g., "John Doe")
                password: Optional password (min 8 chars, auto-generated if omitted)
            """
            body: dict = {"username": username, "domain": domain, "name": name}
            if password:
                body["password"] = password
            response = client.post("/v1/email-users", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_update_email_user(
            user_id: str,
            name: str | None = None,
            password: str | None = None,
        ) -> str:
            """Update an email user's display name or password.

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
                return "Error: At least one field (name or password) must be provided."
            response = client.patch(f"/v1/email-users/{user_id}", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_delete_email_user(user_id: str) -> str:
            """Delete an email user (mailbox).

            This queues the user for deletion (async). The mailbox and all
            its emails will be permanently removed.

            Args:
                user_id: The email user ID to delete
            """
            response = client.delete(f"/v1/email-users/{user_id}")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_bulk_create_email_users(users: list[dict]) -> str:
            """Create multiple email users (mailboxes) at once.

            Each user is created asynchronously via a job queue. Returns
            job IDs for tracking.

            Args:
                users: List of user objects (up to 100), each with:
                    - username (str, required): Local part (e.g., "john.doe")
                    - domain (str, required): Domain name (e.g., "example.com")
                    - name (str, optional): Display name
                    - password (str, optional): Password (auto-generated if omitted)
            """
            response = client.post("/v1/email-users/bulk", json_body={"users": users})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
