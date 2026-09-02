"""Webhook tools — manage webhook endpoints and deliveries."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import DESTRUCTIVE, READ, WRITE, WRITE_IDEMPOTENT, clamp, tool_error, seg

WEBHOOK_EVENTS = (
    "message.relayed",
    "email.received",
    "email.bounced",
    "email.complained",
    "domain.created",
    "domain.ready",
    "domain.dns_failed",
    "email_user.created",
    "email_user.deleted",
)


def _validate_events(events: list[str]) -> str | None:
    if not events:
        return "events must contain at least one event type (or [\"*\"] for all)"
    for e in events:
        if e != "*" and e not in WEBHOOK_EVENTS:
            return f"Unknown event '{e}'. Valid: {', '.join(WEBHOOK_EVENTS)} or \"*\""
    return None


def register_webhook_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register webhook management MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
    def winnr_list_webhooks() -> str:
        """List the account's outbound webhook endpoints.

        Returns id, URL, subscribed events, description, status (enabled /
        disabled / auto-disabled after repeated failures) and delivery health.
        """
        return client.get("/v1/webhooks").render()

    @mcp.tool(annotations=READ)
    def winnr_get_webhook_deliveries(webhook_id: str, limit: int = 25, cursor: str | None = None) -> str:
        """Recent delivery attempts for one webhook (event, HTTP status, retries, time).

        The first place to look when a customer says events are not arriving.

        Args:
            webhook_id: The webhook ID
            limit: Max deliveries to return (1-100, default 25)
            cursor: Pagination cursor from a previous response
        """
        params: dict = {"limit": clamp(limit, 1, 100)}
        if cursor:
            params["cursor"] = cursor
        return client.get(f"/v1/webhooks/{seg(webhook_id)}/deliveries", params=params).render()

    # ── Write tools ─────────────────────────────────────────────────────

    if not config.can_write:
        return

    @mcp.tool(annotations=WRITE)
    def winnr_create_webhook(url: str, events: list[str], description: str | None = None) -> str:
        """Create a webhook endpoint that receives signed event notifications.

        The response includes the signing secret — it is shown here and via
        winnr_get_webhook_secret only. Payloads are HMAC-signed; failed deliveries
        retry with backoff and the endpoint auto-disables after sustained failure.

        Events: message.relayed, email.received, email.bounced, email.complained,
        domain.created, domain.ready, domain.dns_failed, email_user.created,
        email_user.deleted — or ["*"] for all.

        Args:
            url: HTTPS URL to deliver events to
            events: Event types to subscribe to (or ["*"])
            description: Optional human-readable description
        """
        if not url or not url.lower().startswith("https://"):
            return tool_error("url must be an https:// URL")
        err = _validate_events(events)
        if err:
            return tool_error(err)
        body: dict = {"url": url.strip(), "events": events}
        if description is not None:
            body["description"] = description
        return client.post("/v1/webhooks", json_body=body).render()

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_update_webhook(
        webhook_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> str:
        """Update a webhook's URL, events, description, or enabled/disabled status.

        status="enabled" also clears an auto-disable applied after repeated
        delivery failures.

        Args:
            webhook_id: The webhook ID
            url: New HTTPS delivery URL
            events: New list of event types (replaces the existing list)
            description: New description
            status: "enabled" or "disabled"
        """
        body: dict = {}
        if url is not None:
            if not url.lower().startswith("https://"):
                return tool_error("url must be an https:// URL")
            body["url"] = url.strip()
        if events is not None:
            err = _validate_events(events)
            if err:
                return tool_error(err)
            body["events"] = events
        if description is not None:
            body["description"] = description
        if status is not None:
            s = status.strip().lower()
            if s not in ("enabled", "disabled"):
                return tool_error('status must be "enabled" or "disabled"')
            body["status"] = s
        if not body:
            return tool_error("At least one field must be provided.")
        return client.patch(f"/v1/webhooks/{seg(webhook_id)}", json_body=body).render()

    @mcp.tool(annotations=DESTRUCTIVE)
    def winnr_delete_webhook(webhook_id: str) -> str:
        """Delete a webhook endpoint and stop all deliveries to it. Cannot be undone.

        Args:
            webhook_id: The webhook ID
        """
        return client.delete(f"/v1/webhooks/{seg(webhook_id)}").render()

    @mcp.tool(annotations=WRITE)
    def winnr_test_webhook(webhook_id: str) -> str:
        """Send a signed test.ping event to a webhook so the customer can verify their receiver.

        Args:
            webhook_id: The webhook ID
        """
        return client.post(f"/v1/webhooks/{seg(webhook_id)}/test").render()

    @mcp.tool(annotations=READ)
    def winnr_get_webhook_secret(webhook_id: str) -> str:
        """Retrieve a webhook's signing secret. SENSITIVE — treat like a password.

        Only shown when the user needs it to configure their receiver; do not
        repeat it back unprompted or store it anywhere.

        Args:
            webhook_id: The webhook ID
        """
        return client.get(f"/v1/webhooks/{seg(webhook_id)}/secret").render()

    @mcp.tool(annotations=WRITE)
    def winnr_rotate_webhook_secret(webhook_id: str) -> str:
        """Rotate a webhook's signing secret. The old one stays valid for 24 hours.

        Args:
            webhook_id: The webhook ID
        """
        return client.post(f"/v1/webhooks/{seg(webhook_id)}/rotate-secret").render()
