"""Warming tools — manage email warming for mailboxes."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_warming_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register email warming MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool()
    def winnr_list_warming() -> str:
        """List all warming-enabled mailboxes with their current stats.

        Returns mailbox address, warming status (active/paused), health score,
        inbox rate, spam rate, daily volume, and warm-up progress.
        """
        response = client.get("/v1/warming")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_warming_overview() -> str:
        """Get aggregate warming statistics across all mailboxes.

        Returns total active/paused counts, average health score, average
        inbox rate, total daily volume, and estimated monthly warming cost.
        """
        response = client.get("/v1/warming/overview")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_warming_metrics(user_id: str) -> str:
        """Get daily warming metrics for a specific mailbox.

        Returns time-series data: emails sent, inbox rate, spam rate,
        health score per day.

        Args:
            user_id: The email user ID
        """
        response = client.get(f"/v1/warming/{user_id}/metrics")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    # ── Write tools ─────────────────────────────────────────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_enable_warming(user_ids: list[str]) -> str:
            """Enable email warming for one or more mailboxes.

            Warming gradually increases sending volume to build sender
            reputation. Charges $0.60/mailbox/month.

            Args:
                user_ids: List of email user IDs to enable warming for
            """
            response = client.post("/v1/warming/enable", json_body={"user_ids": user_ids})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_disable_warming(user_ids: list[str]) -> str:
            """Disable email warming for one or more mailboxes.

            Stops warming activity and billing for the specified mailboxes.

            Args:
                user_ids: List of email user IDs to disable warming for
            """
            response = client.post("/v1/warming/disable", json_body={"user_ids": user_ids})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_pause_warming(user_id: str) -> str:
            """Pause warming for a specific mailbox.

            Pausing temporarily stops warming activity without disabling it.
            Resume with winnr_resume_warming.

            Args:
                user_id: The email user ID
            """
            response = client.post(f"/v1/warming/{user_id}/pause")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_resume_warming(user_id: str) -> str:
            """Resume warming for a paused mailbox.

            Args:
                user_id: The email user ID
            """
            response = client.post(f"/v1/warming/{user_id}/resume")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_update_warming_settings(
            user_id: str,
            daily_limit: int | None = None,
            ramp_up: bool | None = None,
            reply_rate: int | None = None,
        ) -> str:
            """Update warming settings for a mailbox.

            Args:
                user_id: The email user ID
                daily_limit: Max warming emails per day
                ramp_up: Whether to gradually increase volume (true) or
                    send at daily_limit immediately (false)
                reply_rate: Target reply rate percentage (0-100)
            """
            body: dict = {}
            if daily_limit is not None:
                body["daily_limit"] = daily_limit
            if ramp_up is not None:
                body["ramp_up"] = ramp_up
            if reply_rate is not None:
                body["reply_rate"] = reply_rate
            if not body:
                return "Error: At least one setting must be provided."
            response = client.patch(f"/v1/warming/{user_id}/settings", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
