"""Inbox tools — read, send, and manage emails."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import MAX_BODY_LENGTH, WinnrClient
from winnr_mcp.config import WinnrConfig


def register_inbox_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register inbox MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool()
    def winnr_list_inbox(
        limit: int = 50,
        cursor: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        mailbox: str | None = None,
        exclude_warmup: bool = False,
    ) -> str:
        """List inbox messages across all mailboxes in your account.

        Returns message previews (subject, from, to, date). For full
        email body, use winnr_get_message_body.

        Args:
            limit: Page size (1-200, default 50)
            cursor: Pagination cursor from a previous response
            date_from: Start date filter (YYYY-MM-DD)
            date_to: End date filter (YYYY-MM-DD)
            mailbox: Filter by specific email address
            exclude_warmup: Exclude warming/warmup emails (default false)
        """
        params: dict = {"limit": min(max(limit, 1), 200)}
        if cursor:
            params["cursor"] = cursor
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if mailbox:
            params["mailbox"] = mailbox
        if exclude_warmup:
            params["exclude_warmup"] = "true"
        response = client.get("/v1/inbox", params=params)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_message_body(uid: str) -> str:
        """Get the full email body for a message.

        The inbox list only shows a preview. Use this to read the full
        content of an email.

        Args:
            uid: The message UID (from winnr_list_inbox)
        """
        response = client.get(f"/v1/inbox/{uid}/body")
        if not response.ok:
            return response.error_message or "Unknown error"
        # Truncate very long email bodies to prevent context blowout
        json_str = response.to_json()
        if len(json_str) > MAX_BODY_LENGTH:
            return json_str[:MAX_BODY_LENGTH] + '\n\n... [truncated — email body exceeded 10K chars]'
        return json_str

    # ── Write tools ─────────────────────────────────────────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_send_email(
            user_id: str,
            to: str,
            subject: str,
            body: str,
            html: bool = False,
            cc: str | None = None,
            bcc: str | None = None,
            in_reply_to: str | None = None,
            references: str | None = None,
        ) -> str:
            """Send an email from a specific mailbox.

            Args:
                user_id: The email user ID to send from
                to: Recipient email address
                subject: Email subject line
                body: Email body text (plain text or HTML)
                html: Whether the body is HTML (default false = plain text)
                cc: CC recipient(s), comma-separated
                bcc: BCC recipient(s), comma-separated
                in_reply_to: Message-ID to reply to (for threading)
                references: References header (for threading)
            """
            payload: dict = {"to": to, "subject": subject, "body": body}
            if html:
                payload["html"] = True
            if cc:
                payload["cc"] = cc
            if bcc:
                payload["bcc"] = bcc
            if in_reply_to:
                payload["in_reply_to"] = in_reply_to
            if references:
                payload["references"] = references
            response = client.post(f"/v1/email-users/{user_id}/inbox/send", json_body=payload)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_refresh_inbox() -> str:
            """Trigger an inbox sync to fetch new emails across all mailboxes.

            This refreshes the inbox cache by fetching new messages from
            the mail server. May take a few seconds to complete.
            """
            response = client.post("/v1/inbox/refresh")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_delete_message(user_id: str, message_id: str) -> str:
            """Delete a message from a mailbox's inbox.

            Args:
                user_id: The email user ID that owns the message
                message_id: The message ID to delete
            """
            response = client.delete(f"/v1/email-users/{user_id}/inbox/{message_id}")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
