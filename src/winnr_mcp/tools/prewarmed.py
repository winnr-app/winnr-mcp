"""Pre-warmed marketplace tools — browse and buy aged, already-warmed domains."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp import scopes as sc
from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import (
    DESTRUCTIVE,
    PURCHASE,
    READ,
    clamp,
    clean_domain,
    is_str,
    money_line,
    seg,
    tool_error,
    winnr_tool,
)

BLOCKLISTS = (
    "spamhaus_dbl",
    "surbl",
    "ivmuri",
    "nordspam_dbl",
    "sem_fresh",
    "sem_uri",
    "sem_urired",
    "sorbs_badconf",
    "sorbs_nomail",
)
SORT_OPTIONS = ("health", "warming_days", "name")
MIN_ADDRESSES = 3
MAX_ADDRESSES = 50
MAX_CUSTOM_USERNAMES = 10
MAX_BATCH = 25
PURCHASE_TIMEOUT_SECONDS = 60
PRICE_PER_ADDRESS = 3.0


def register_prewarmed_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register pre-warmed marketplace MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_browse_prewarmed(
        search: str | None = None,
        sort_by: str = "health",
        include_all: bool = False,
        page: int = 1,
        per_page: int = 50,
    ) -> str:
        """Browse pre-warmed domains available to buy right now.

        Pre-warmed domains are aged domains whose mailboxes have been warming for
        weeks, so they can send immediately instead of waiting 2-3 weeks. Returns
        name, average health score, warming days, domain age, address count and
        blocklist status per listing. $3 per address per month, domain included,
        no minimum term, no base plan required.

        Inventory is shared across all customers and can sell at any moment.
        Re-check right before buying and treat a conflict on purchase as "already
        sold" — move to the next candidate, do not retry the same domain.

        Args:
            search: Filter to domains whose name contains this text
            sort_by: "health" (default), "warming_days" or "name"
            include_all: Also include aged domains that are not warming yet
            page: Page number (default 1)
            per_page: Results per page (1-1000, default 50)
        """
        sort = (sort_by or "health").strip().lower()
        if sort not in SORT_OPTIONS:
            return tool_error(f"sort_by must be one of {', '.join(SORT_OPTIONS)}")
        params: dict = {
            "sort_by": sort,
            "page": max(int(page), 1),
            "per_page": clamp(per_page, 1, 1000),
        }
        if search:
            params["search"] = search.strip()
        if include_all:
            params["include_all"] = "true"
        return client.get("/v1/prewarmed/browse", params=params).render()

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_get_prewarmed_domain(domain: str) -> str:
        """Full detail for one available pre-warmed domain.

        Returns each warmed address with its own health score, blocklist state,
        registration/expiry dates and pricing. Not-found once sold or retired.

        Args:
            domain: The pre-warmed domain name (e.g. "example.com")
        """
        d = clean_domain(domain)
        if not d:
            return tool_error("domain is required")
        return client.get(f"/v1/prewarmed/{seg(d)}").render()

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_check_prewarmed_blocklist(domain: str, blocklist: str | None = None) -> str:
        """Live blocklist check on a pre-warmed domain (all nine lists by default).

        Use right before buying. Results are cached for 5 minutes ("cached": true).
        Only SURBL and Spamhaus DBL materially affect deliverability; the rest are
        informational.

        Args:
            domain: The pre-warmed domain name
            blocklist: Check a single list instead of all nine. One of:
                spamhaus_dbl, surbl, ivmuri, nordspam_dbl, sem_fresh,
                sem_uri, sem_urired, sorbs_badconf, sorbs_nomail
        """
        d = clean_domain(domain)
        if not d:
            return tool_error("domain is required")
        params: dict | None = None
        if blocklist:
            bl = blocklist.strip().lower()
            if bl not in BLOCKLISTS:
                return tool_error(f"Unknown blocklist '{blocklist}'. Valid: {', '.join(BLOCKLISTS)}")
            params = {"list": bl}
        return client.post(f"/v1/prewarmed/{seg(d)}/blocklist-check", params=params).render()

    @winnr_tool(mcp, sc.READ, READ)
    def winnr_list_my_prewarmed() -> str:
        """List the pre-warmed domains this account has bought, with purchase dates.

        Credentials are not included — use winnr_list_email_users filtered to the
        domain for addresses, and winnr_export_email_users for passwords.
        """
        return client.get("/v1/prewarmed/my-domains").render()

    # ── Write tools ─────────────────────────────────────────────────────

    @winnr_tool(mcp, sc.PURCHASE, PURCHASE)
    def winnr_purchase_prewarmed(
        domain: str,
        address_count: int = MIN_ADDRESSES,
        custom_usernames: list[str] | None = None,
        confirmation_token: str | None = None,
    ) -> str:
        """Buy pre-warmed addresses on ONE domain. CHARGES THE ACCOUNT'S CARD ON FILE.

        Two-step, nothing is charged on the first call: without
        confirmation_token you get a quote (domain still available, address
        count, monthly total) and a token valid 10 minutes; show it, get an
        explicit yes, call again with the same arguments plus the token.

        $3 per address per month, domain included, no minimum term. The first
        month is charged immediately; if the charge fails nothing is provisioned.
        Idempotent per domain: re-running a successful purchase returns
        already_provisioned instead of charging again.

        Two modes:
        - Keep (default): you get the domain's existing warmed mailboxes,
          credentials unchanged, usable as soon as this returns.
        - Custom (custom_usernames given): the warmed sample mailboxes are
          replaced by new ones with your local-parts on the same aged domain.
          Provisioning is asynchronous; the mailboxes appear in
          winnr_list_email_users a few minutes later. They inherit the domain's
          reputation but are not individually warmed.

        Warming stops at sale either way — addresses arrive ready to send.
        Pre-warmed addresses are a separate pool: no base plan is needed, they do
        not count against email user or domain limits, and they bill on their own
        subscription. A 402 payment_method_required error means the account has
        no card — the user adds one in the dashboard first.

        For several domains use winnr_purchase_prewarmed_batch (one charge).

        Args:
            domain: The pre-warmed domain to buy
            address_count: Warmed addresses to take (3-50, default 3). Ignored when
                custom_usernames is given.
            custom_usernames: Optional 3-10 unique local-parts to provision as new
                mailboxes instead of keeping the warmed ones.
            confirmation_token: Token from the quote step, after the user said yes
        """
        d = clean_domain(domain)
        if not d:
            return tool_error("domain is required")
        body: dict = {"domain": d}
        if custom_usernames:
            names = [u.strip().lower() for u in custom_usernames if is_str(u)]
            if not MIN_ADDRESSES <= len(names) <= MAX_CUSTOM_USERNAMES:
                return tool_error(f"custom_usernames must contain {MIN_ADDRESSES}-{MAX_CUSTOM_USERNAMES} names")
            if len(set(names)) != len(names):
                return tool_error("custom_usernames must be unique")
            body["custom_usernames"] = names
            body["address_count"] = len(names)
        else:
            if not MIN_ADDRESSES <= int(address_count) <= MAX_ADDRESSES:
                return tool_error(f"address_count must be {MIN_ADDRESSES}-{MAX_ADDRESSES}")
            body["address_count"] = int(address_count)

        gate = _confirm_prewarmed(client, config, "purchase_prewarmed", [body], confirmation_token)
        if gate is not None:
            return gate
        return client.post(
            "/v1/prewarmed/purchase", json_body=body, timeout=PURCHASE_TIMEOUT_SECONDS
        ).render()

    @winnr_tool(mcp, sc.PURCHASE, PURCHASE)
    def winnr_purchase_prewarmed_batch(items: list[dict], confirmation_token: str | None = None) -> str:
        """Buy pre-warmed addresses on SEVERAL domains as one order and one charge.
        CHARGES THE ACCOUNT'S CARD ON FILE.

        Two-step like winnr_purchase_prewarmed: the first call (no
        confirmation_token) only checks every domain is still available and
        returns the quote + token; the second call with the token buys.

        All-or-nothing: every domain is claimed up front; if any was already sold
        or the charge fails, the whole order rolls back and nothing is billed.
        Same terms as winnr_purchase_prewarmed.

        Args:
            items: 1-25 order items, each:
                - domain (str, required)
                - address_count (int, required): 3-50
                - custom_usernames (list[str], optional): 3-10 local-parts to
                  provision as new mailboxes instead of the warmed ones
                Duplicate domains in one order are rejected.
            confirmation_token: Token from the quote step, after the user said yes
        """
        if not items:
            return tool_error("items must contain at least one order item")
        if len(items) > MAX_BATCH:
            return tool_error(
                f"A batch order takes at most {MAX_BATCH} domains, got {len(items)}. Split it up."
            )
        order = []
        seen: set[str] = set()
        for i, item in enumerate(items):
            if not isinstance(item, dict) or not is_str(item.get("domain")):
                return tool_error(f"items[{i}] must be an object with a 'domain'")
            d = clean_domain(item["domain"])
            if d in seen:
                return tool_error(f"Duplicate domain in order: {seg(d)}")
            seen.add(d)
            entry: dict = {"domain": d}
            if "address_count" in item:
                entry["address_count"] = item["address_count"]
            if item.get("custom_usernames"):
                entry["custom_usernames"] = item["custom_usernames"]
            order.append(entry)
        for i, entry in enumerate(order):
            if "custom_usernames" in entry:
                names = [u.strip().lower() for u in entry["custom_usernames"] if is_str(u)]
                if not MIN_ADDRESSES <= len(names) <= MAX_CUSTOM_USERNAMES or len(set(names)) != len(names):
                    return tool_error(f"items[{i}].custom_usernames must be {MIN_ADDRESSES}-{MAX_CUSTOM_USERNAMES} unique names")
                entry["custom_usernames"] = names
                entry["address_count"] = len(names)
            try:
                count = int(entry.get("address_count"))
            except (TypeError, ValueError):
                return tool_error(f"items[{i}].address_count is required (3-50)")
            if not MIN_ADDRESSES <= count <= MAX_ADDRESSES:
                return tool_error(f"items[{i}].address_count must be {MIN_ADDRESSES}-{MAX_ADDRESSES}")
            entry["address_count"] = count
        gate = _confirm_prewarmed(client, config, "purchase_prewarmed_batch", order, confirmation_token)
        if gate is not None:
            return gate
        return client.post(
            "/v1/prewarmed/purchase-batch",
            json_body={"items": order},
            timeout=PURCHASE_TIMEOUT_SECONDS,
        ).render()

    @winnr_tool(mcp, sc.WRITE, DESTRUCTIVE)
    def winnr_cancel_prewarmed(domain: str) -> str:
        """Cancel a purchased pre-warmed domain and stop its billing. DESTRUCTIVE.

        Deletes the domain and all of its mailboxes (and their mail) from the
        account and returns the domain to the marketplace. Cannot be undone.
        Allowed at any time — there is no minimum term. Confirm first.

        Args:
            domain: The purchased pre-warmed domain to cancel
        """
        d = clean_domain(domain)
        if not d:
            return tool_error("domain is required")
        return client.post("/v1/prewarmed/cancel", json_body={"domain": d}).render()


def _confirm_prewarmed(client: WinnrClient, config: WinnrConfig, kind: str, order: list[dict], token: str | None) -> str | None:
    """Quote → confirm gate shared by the two pre-warmed purchase tools.

    Returns None when the caller may proceed with the purchase; otherwise the
    JSON to return (a quote with a confirmation token, or an error).
    """
    lines = []
    problems = []
    for entry in order:
        detail = client.get(f"/v1/prewarmed/{seg(entry['domain'])}")
        if not detail.ok:
            problems.append(f"{entry['domain']}: {detail.error_message}")
            continue
        data = detail.data if isinstance(detail.data, dict) else {}
        available = data.get("address_count") or data.get("available_addresses") or len(data.get("addresses") or [])
        if not entry.get("custom_usernames") and available and entry["address_count"] > int(available):
            problems.append(f"{entry['domain']}: only {available} warmed addresses available, asked for {entry['address_count']}")
        lines.append({
            "domain": entry["domain"],
            "address_count": entry["address_count"],
            "custom_usernames": entry.get("custom_usernames"),
            "monthly": round(entry["address_count"] * PRICE_PER_ADDRESS, 2),
        })
    if problems:
        return tool_error("Cannot quote this order: " + "; ".join(problems), code="unavailable")
    quote = {
        "items": lines,
        "price_per_address_per_month": PRICE_PER_ADDRESS,
        "monthly_total": round(sum(x["monthly"] for x in lines), 2),
        "note": "First month charged now; no minimum term; cancel any time.",
    }
    subject = client.account_id or ""
    if not token:
        tok, exp = config.confirmer.issue(kind, subject, quote)
        return json.dumps({
            "quote": quote,
            "summary": f"Buy {sum(x['address_count'] for x in lines)} pre-warmed address(es) on {len(lines)} domain(s): "
                       f"{money_line(quote['monthly_total'])}/month, first month charged now.",
            "confirmation_token": tok,
            "expires_at": exp,
            "next_step": f"Show the quote to the user. After an explicit yes, call winnr_{kind} again with the same arguments and this confirmation_token.",
        }, indent=2)
    reason = config.confirmer.verify(token, kind, subject, quote)
    if reason:
        return tool_error(
            f"Confirmation {reason}. Nothing was bought. Call without confirmation_token for a fresh quote.",
            code="confirmation_invalid",
            quote=quote,
        )
    return None
