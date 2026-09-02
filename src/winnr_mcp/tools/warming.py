"""Warming tools — manage email warming for mailboxes."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp import scopes as sc
from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import (
    PURCHASE,
    READ,
    WRITE_IDEMPOTENT,
    money_line,
    seg,
    tool_error,
    winnr_tool,
)

RAMPUP_SPEEDS = ("slow", "normal", "fast")
MAX_EMAILS_PER_DAY = 20
WARMING_PRICE_PER_MAILBOX = 0.60


def register_warming_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register email warming MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_list_warming() -> str:
        """List every warming-enabled mailbox with its current stats.

        Returns address, status (active/paused), health score, inbox rate, spam
        rate, daily volume and ramp-up progress per mailbox.
        """
        return client.get("/v1/warming").render()

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_get_warming_overview() -> str:
        """Aggregate warming stats for the account.

        Returns active/paused counts, average health score and inbox rate, total
        daily volume, and the estimated monthly warming cost ($0.60/mailbox).
        """
        return client.get("/v1/warming/overview").render()

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_get_warming_metrics(user_id: str) -> str:
        """Daily warming time-series for one mailbox (sent, inbox rate, spam rate, health).

        Args:
            user_id: The email user ID
        """
        return client.get(f"/v1/warming/{seg(user_id)}/metrics").render()

    # ── Write tools ─────────────────────────────────────────────────────

    @winnr_tool(mcp, sc.PURCHASE, PURCHASE)
    def winnr_enable_warming(
        user_ids: list[str],
        emails_per_day: int = 20,
        rampup_speed: str = "normal",
        confirmation_token: str | None = None,
    ) -> str:
        """Enable warming on one or more mailboxes. BILLS $0.60 per mailbox per month.

        Two-step, nothing is charged on the first call:
        1. Call WITHOUT confirmation_token → returns a quote (mailboxes, monthly
           total) and a confirmation_token valid for 10 minutes.
        2. Show the quote, get an explicit yes from the user, call again with the
           same arguments plus confirmation_token → warming is enabled and billed.

        Warming exchanges mail with a network of real inboxes to build sender
        reputation. New mailboxes should warm for 2-3 weeks before any cold
        sending. Re-enabling a mailbox that is already warming is a no-op and is
        not billed twice.

        Args:
            user_ids: Email user IDs to enable warming for
            emails_per_day: Target warming volume, 1-20 (default 20)
            rampup_speed: "slow", "normal" or "fast" (default "normal")
            confirmation_token: Token from the quote step, after the user said yes
        """
        if not user_ids or not all(isinstance(u, str) and u.strip() for u in user_ids):
            return tool_error("user_ids must contain at least one email user ID")
        ids = sorted({u.strip() for u in user_ids})
        try:
            per_day = int(emails_per_day)
        except (TypeError, ValueError):
            return tool_error("emails_per_day must be an integer 1-20")
        if not 1 <= per_day <= MAX_EMAILS_PER_DAY:
            return tool_error(f"emails_per_day must be 1-{MAX_EMAILS_PER_DAY}")
        speed = (rampup_speed or "normal").strip().lower()
        if speed not in RAMPUP_SPEEDS:
            return tool_error(f"rampup_speed must be one of {', '.join(RAMPUP_SPEEDS)}")

        monthly = round(len(ids) * WARMING_PRICE_PER_MAILBOX, 2)
        quote = {
            "mailboxes": ids,
            "emails_per_day": per_day,
            "rampup_speed": speed,
            "price_per_mailbox_per_month": WARMING_PRICE_PER_MAILBOX,
            "monthly_total": monthly,
        }
        subject = client.account_id or ""
        if not confirmation_token:
            token, exp = config.confirmer.issue("enable_warming", subject, quote)
            return json.dumps({
                "quote": quote,
                "summary": f"Enable warming on {len(ids)} mailbox(es): {money_line(monthly)}/month, billed now.",
                "confirmation_token": token,
                "expires_at": exp,
                "next_step": "Show the summary to the user. After an explicit yes, call winnr_enable_warming again with the same arguments and this confirmation_token.",
            }, indent=2)
        reason = config.confirmer.verify(confirmation_token, "enable_warming", subject, quote)
        if reason:
            return tool_error(
                f"Confirmation {reason}. Call without confirmation_token to get a fresh quote and confirm again.",
                code="confirmation_invalid",
            )
        body = {"user_ids": ids, "settings": {"emails_per_day": per_day, "rampup_speed": speed}}
        return client.post("/v1/warming/enable", json_body=body).render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT)
    def winnr_disable_warming(user_ids: list[str]) -> str:
        """Turn warming off for one or more mailboxes and stop their $0.60/month charge.

        The reputation built so far is kept, but it decays if the mailbox goes idle.
        Prefer winnr_pause_warming for a temporary stop.

        Args:
            user_ids: Email user IDs to disable warming for
        """
        if not user_ids:
            return tool_error("user_ids must contain at least one email user ID")
        return client.post("/v1/warming/disable", json_body={"user_ids": user_ids}).render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT)
    def winnr_pause_warming(user_id: str) -> str:
        """Pause warming on one mailbox without disabling it (billing continues).

        Args:
            user_id: The email user ID
        """
        return client.post(f"/v1/warming/{seg(user_id)}/pause").render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT)
    def winnr_resume_warming(user_id: str) -> str:
        """Resume warming on a paused mailbox.

        Args:
            user_id: The email user ID
        """
        return client.post(f"/v1/warming/{seg(user_id)}/resume").render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT)
    def winnr_update_warming_settings(
        user_id: str,
        emails_per_day: int | None = None,
        rampup_enabled: bool | None = None,
        rampup_speed: str | None = None,
    ) -> str:
        """Change warming volume or ramp-up for a mailbox that is already warming.

        The reply rate is fixed server-side (30%) and cannot be changed.

        Args:
            user_id: The email user ID
            emails_per_day: Warming emails per day, 1-20
            rampup_enabled: true to ramp volume up gradually, false to send at
                emails_per_day right away
            rampup_speed: "slow", "normal" or "fast" (implies rampup_enabled=true)
        """
        body: dict = {}
        if emails_per_day is not None:
            if not 1 <= int(emails_per_day) <= MAX_EMAILS_PER_DAY:
                return tool_error(f"emails_per_day must be 1-{MAX_EMAILS_PER_DAY}")
            body["emails_per_day"] = int(emails_per_day)
        if rampup_enabled is not None:
            body["rampup_enabled"] = bool(rampup_enabled)
        if rampup_speed is not None:
            speed = rampup_speed.strip().lower()
            if speed not in RAMPUP_SPEEDS:
                return tool_error(f"rampup_speed must be one of {', '.join(RAMPUP_SPEEDS)}")
            body["rampup_speed"] = speed
        if not body:
            return tool_error("Provide at least one of emails_per_day, rampup_enabled, rampup_speed.")
        return client.patch(f"/v1/warming/{seg(user_id)}/settings", json_body=body).render()
