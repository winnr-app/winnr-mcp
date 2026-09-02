"""Pre-warmed marketplace tools — browse and buy aged, already-warmed domains."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import DESTRUCTIVE, PURCHASE, READ, clamp, clean_domain, is_str, tool_error, seg

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


def register_prewarmed_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register pre-warmed marketplace MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool(annotations=READ)
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

    @mcp.tool(annotations=READ)
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

    @mcp.tool(annotations=READ)
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

    @mcp.tool(annotations=READ)
    def winnr_list_my_prewarmed() -> str:
        """List the pre-warmed domains this account has bought, with purchase dates.

        Credentials are not included — use winnr_list_email_users filtered to the
        domain for addresses, and winnr_export_email_users for passwords.
        """
        return client.get("/v1/prewarmed/my-domains").render()

    # ── Write tools ─────────────────────────────────────────────────────

    if not config.can_write:
        return

    @mcp.tool(annotations=PURCHASE)
    def winnr_purchase_prewarmed(
        domain: str,
        address_count: int = MIN_ADDRESSES,
        custom_usernames: list[str] | None = None,
    ) -> str:
        """Buy pre-warmed addresses on ONE domain. CHARGES THE ACCOUNT'S CARD ON FILE.

        $3 per address per month, domain included, no minimum term. The first
        month is charged immediately; if the charge fails nothing is provisioned.
        Idempotent per domain: re-running a successful purchase returns
        already_provisioned instead of charging again. Confirm domain, address
        count and monthly total with the user before calling.

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
        return client.post(
            "/v1/prewarmed/purchase", json_body=body, timeout=PURCHASE_TIMEOUT_SECONDS
        ).render()

    @mcp.tool(annotations=PURCHASE)
    def winnr_purchase_prewarmed_batch(items: list[dict]) -> str:
        """Buy pre-warmed addresses on SEVERAL domains as one order and one charge.
        CHARGES THE ACCOUNT'S CARD ON FILE.

        All-or-nothing: every domain is claimed up front; if any was already sold
        or the charge fails, the whole order rolls back and nothing is billed.
        Same terms as winnr_purchase_prewarmed. Confirm the full list and total
        with the user first.

        Args:
            items: 1-25 order items, each:
                - domain (str, required)
                - address_count (int, required): 3-50
                - custom_usernames (list[str], optional): 3-10 local-parts to
                  provision as new mailboxes instead of the warmed ones
                Duplicate domains in one order are rejected.
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
        return client.post(
            "/v1/prewarmed/purchase-batch",
            json_body={"items": order},
            timeout=PURCHASE_TIMEOUT_SECONDS,
        ).render()

    @mcp.tool(annotations=DESTRUCTIVE)
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
