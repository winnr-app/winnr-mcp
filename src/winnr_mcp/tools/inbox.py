"""Inbox tools — read, send, and manage emails."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import MAX_BODY_LENGTH, WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import DESTRUCTIVE, READ, WRITE, clamp, tool_error


def register_inbox_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register inbox MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    def winnr_list_inbox(
        limit: int = 50,
        cursor: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        mailbox: str | None = None,
        exclude_warmup: bool = True,
        has_attachments: bool = False,
    ) -> str:
        """List received messages across every mailbox in the account (newest first).

        Returns previews only: uid, mailbox, from, to, subject, date, snippet, read
        state. Use winnr_get_message_body with the uid AND mailbox for full text.
        The inbox is a cache refreshed periodically; call winnr_refresh_inbox first
        when the user expects something that just arrived.

        Args:
            limit: Page size (1-200, default 50)
            cursor: Pagination cursor from a previous response
            date_from: Start date, inclusive (YYYY-MM-DD)
            date_to: End date, inclusive (YYYY-MM-DD)
            mailbox: Only this email address (e.g. "john@example.com")
            exclude_warmup: Hide warm-up traffic (default true — real replies only)
            has_attachments: Only messages with attachments
        """
        params: dict = {"limit": clamp(limit, 1, 200)}
        if cursor:
            params["cursor"] = cursor
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if mailbox:
            params["mailbox"] = mailbox.strip().lower()
        if exclude_warmup:
            params["exclude_warmup"] = "true"
        if has_attachments:
            params["has_attachments"] = "true"
        return client.get("/v1/inbox", params=params).render()

    @mcp.tool(annotations=READ)
    def winnr_get_message_body(uid: str, mailbox: str) -> str:
        """Get the full body of one message.

        Both arguments come from the same winnr_list_inbox row: its `uid` and its
        `mailbox` (the address that received it). Bodies longer than 10,000
        characters are truncated with a marker.

        Args:
            uid: The message UID (from winnr_list_inbox)
            mailbox: The receiving email address (from the same inbox row)
        """
        if not mailbox or "@" not in mailbox:
            return tool_error("mailbox is required and must be the receiving email address")
        response = client.get(
            f"/v1/inbox/{uid}/body", params={"mailbox": mailbox.strip().lower()}
        )
        if not response.ok:
            return response.error_json()
        data = response.data
        if isinstance(data, dict) and isinstance(data.get("body"), str):
            body = data["body"]
            if len(body) > MAX_BODY_LENGTH:
                data = dict(data)
                data["body"] = body[:MAX_BODY_LENGTH]
                data["truncated"] = True
                data["original_length"] = len(body)
                data["note"] = f"Body truncated to {MAX_BODY_LENGTH} characters."
                return json.dumps({"data": data}, indent=2, default=str)
        return response.render()

    # ── Write tools ─────────────────────────────────────────────────────

    if not config.can_write:
        return

    @mcp.tool(annotations=WRITE)
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
        """Send one email from a Winnr mailbox. Sends immediately and cannot be recalled.

        Meant for replies and one-off messages, not campaigns — sequencers handle
        volume. To reply in-thread, pass the original message's Message-ID as
        in_reply_to (and as references). Show the user the final text before
        sending.

        Args:
            user_id: The email user ID to send from (not the address)
            to: Recipient email address(es), comma-separated
            subject: Subject line
            body: Body text (plain text, or HTML when html=true)
            html: Set true if body is HTML (default false)
            cc: CC address(es), comma-separated
            bcc: BCC address(es), comma-separated
            in_reply_to: Message-ID being replied to (for threading)
            references: References header value (for threading)
        """
        if not to or "@" not in to:
            return tool_error("to must contain at least one email address")
        if not subject or not subject.strip():
            return tool_error("subject is required")
        payload: dict = {"to": to, "subject": subject, "body": body or ""}
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
        return client.post(f"/v1/email-users/{user_id}/inbox/send", json_body=payload).render()

    @mcp.tool(annotations=WRITE)
    def winnr_refresh_inbox() -> str:
        """Trigger a sync of new mail into the inbox cache for every mailbox.

        Runs in the background; call winnr_list_inbox again after ~10-30 seconds.
        Rate limited per account, so once per minute is plenty.
        """
        return client.post("/v1/inbox/refresh").render()

    @mcp.tool(annotations=DESTRUCTIVE)
    def winnr_delete_message(uid: str, mailbox: str) -> str:
        """Permanently delete one received message from a mailbox.

        Arguments come from the winnr_list_inbox row: its `uid` and `mailbox`.

        Args:
            uid: The message UID
            mailbox: The email address the message belongs to
        """
        if not mailbox or "@" not in mailbox:
            return tool_error("mailbox is required and must be the receiving email address")
        return client.delete(f"/v1/inbox/{uid}", params={"mailbox": mailbox.strip().lower()}).render()
