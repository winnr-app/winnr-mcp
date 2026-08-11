"""Webhook tools — manage webhook endpoints and deliveries."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_webhook_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register webhook management MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool()
    def winnr_list_webhooks() -> str:
        """List all webhook endpoints configured on the account.

        Returns each webhook's ID, URL, subscribed events, description,
        status (enabled/disabled), and delivery health.
        """
        response = client.get("/v1/webhooks")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_webhook_deliveries(webhook_id: str, limit: int = 25) -> str:
        """List recent delivery attempts for a webhook endpoint.

        Returns each delivery's event type, HTTP status, success/failure,
        retry count, and timestamp. Useful for debugging a webhook that
        is not receiving events.

        Args:
            webhook_id: The webhook ID
            limit: Max deliveries to return (default 25)
        """
        response = client.get(f"/v1/webhooks/{webhook_id}/deliveries", params={"limit": limit})
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    # ── Write tools ─────────────────────────────────────────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_create_webhook(url: str, events: list[str], description: str | None = None) -> str:
            """Create a webhook endpoint that receives event notifications.

            The response includes the signing secret used to verify webhook
            payloads — store it securely; it is only shown in full here and
            via winnr_get_webhook_secret.

            Valid events: message.relayed, email.received, email.bounced,
            email.complained, domain.created, domain.ready, domain.dns_failed,
            email_user.created, email_user.deleted — or ["*"] to subscribe
            to all events.

            Args:
                url: The HTTPS URL to deliver events to
                events: List of event types to subscribe to (or ["*"] for all)
                description: Optional human-readable description
            """
            body: dict = {"url": url, "events": events}
            if description is not None:
                body["description"] = description
            response = client.post("/v1/webhooks", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_update_webhook(
            webhook_id: str,
            url: str | None = None,
            events: list[str] | None = None,
            description: str | None = None,
            status: str | None = None,
        ) -> str:
            """Update a webhook endpoint's URL, events, description, or status.

            Setting status to "enabled" re-enables a webhook and clears any
            auto-disable applied after repeated delivery failures.

            Args:
                webhook_id: The webhook ID
                url: New delivery URL
                events: New list of subscribed event types (replaces existing)
                description: New description
                status: "enabled" or "disabled"
            """
            body: dict = {}
            if url is not None:
                body["url"] = url
            if events is not None:
                body["events"] = events
            if description is not None:
                body["description"] = description
            if status is not None:
                body["status"] = status
            if not body:
                return "Error: At least one field must be provided."
            response = client.patch(f"/v1/webhooks/{webhook_id}", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_delete_webhook(webhook_id: str) -> str:
            """Delete a webhook endpoint.

            Stops all event deliveries to the endpoint. This cannot be undone.

            Args:
                webhook_id: The webhook ID
            """
            response = client.delete(f"/v1/webhooks/{webhook_id}")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_test_webhook(webhook_id: str) -> str:
            """Send a test.ping event to a webhook endpoint.

            Delivers a signed test payload so you can verify the endpoint
            receives and validates events correctly.

            Args:
                webhook_id: The webhook ID
            """
            response = client.post(f"/v1/webhooks/{webhook_id}/test")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_get_webhook_secret(webhook_id: str) -> str:
            """Retrieve a webhook's signing secret.

            WARNING: this returns a sensitive signing secret (a credential).
            Anyone holding it can forge valid-looking webhook payloads.
            Handle it like a password — do not log or share it.

            Args:
                webhook_id: The webhook ID
            """
            response = client.get(f"/v1/webhooks/{webhook_id}/secret")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_rotate_webhook_secret(webhook_id: str) -> str:
            """Rotate a webhook's signing secret.

            Generates a new signing secret. The old secret remains valid for
            24 hours so you can roll over signature verification without
            dropping events.

            Args:
                webhook_id: The webhook ID
            """
            response = client.post(f"/v1/webhooks/{webhook_id}/rotate-secret")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
