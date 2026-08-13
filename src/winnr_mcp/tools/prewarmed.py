"""Pre-warmed marketplace tools — browse and buy aged, already-warmed domains."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig

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


def register_prewarmed_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register pre-warmed marketplace MCP tools."""

    # ── Read tools ──────────────────────────────────────────────────────

    @mcp.tool()
    def winnr_browse_prewarmed(
        search: str | None = None,
        sort_by: str = "health",
        include_all: bool = False,
        page: int = 1,
        per_page: int = 50,
    ) -> str:
        """Browse pre-warmed domains available to buy.

        Pre-warmed domains are aged domains with mailboxes that have already
        been warming, so they can send immediately instead of waiting weeks.

        Returns domain name, average health score, warming days, domain age,
        address count, and blocklist status for each listing.

        Inventory is finite and shared across all customers — a domain listed
        here can be bought by someone else at any moment. Re-check availability
        right before purchasing, and move to the next candidate if a purchase
        comes back with a conflict.

        Args:
            search: Filter to domains whose name contains this text
            sort_by: One of "health", "warming_days", "name" (default "health")
            include_all: Also include aged domains that are not warming yet
            page: Page number (default 1)
            per_page: Results per page (1-1000, default 50)
        """
        if sort_by not in ("health", "warming_days", "name"):
            return 'Error: sort_by must be one of "health", "warming_days", "name".'
        params: dict = {
            "sort_by": sort_by,
            "page": max(page, 1),
            "per_page": min(max(per_page, 1), 1000),
        }
        if search:
            params["search"] = search
        if include_all:
            params["include_all"] = "true"
        response = client.get("/v1/prewarmed/browse", params=params)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_prewarmed_domain(domain: str) -> str:
        """Get full detail for one available pre-warmed domain.

        Returns the individual warmed addresses with per-address health scores,
        blocklist state, registration/expiration dates, and pricing terms.

        Returns a not-found error once the domain has been sold or retired.

        Args:
            domain: The pre-warmed domain name (e.g. "example.com")
        """
        response = client.get(f"/v1/prewarmed/{domain}")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_check_prewarmed_blocklist(domain: str, blocklist: str | None = None) -> str:
        """Run a live blocklist check on a pre-warmed domain.

        Useful for re-verifying a domain immediately before buying it. Checks
        all nine blocklists by default. Results are cached for 5 minutes; a
        cached result comes back with "cached": true.

        Args:
            domain: The pre-warmed domain name
            blocklist: Check a single list instead of all nine. One of:
                spamhaus_dbl, surbl, ivmuri, nordspam_dbl, sem_fresh,
                sem_uri, sem_urired, sorbs_badconf, sorbs_nomail
        """
        path = f"/v1/prewarmed/{domain}/blocklist-check"
        if blocklist:
            if blocklist not in BLOCKLISTS:
                return f"Error: unknown blocklist '{blocklist}'. Valid: {', '.join(BLOCKLISTS)}"
            # The API reads `list` from the query string, and client.post() takes
            # no params, so it goes on the path.
            path = f"{path}?list={blocklist}"
        response = client.post(path)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_list_my_prewarmed() -> str:
        """List the pre-warmed domains this account has purchased.

        Returns each domain with its purchase date. (The 90-day minimum term
        was removed in August 2026; pre-warmed domains can be cancelled at any
        time, and minimum_term_end is null on newer purchases.)

        Mailbox credentials are not returned here — use winnr_list_email_users
        filtered to the domain to get addresses and passwords.
        """
        response = client.get("/v1/prewarmed/my-domains")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    # ── Write tools ─────────────────────────────────────────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_purchase_prewarmed(
            domain: str,
            address_count: int,
            custom_usernames: list[str] | None = None,
        ) -> str:
            """Buy pre-warmed addresses on ONE domain. CHARGES THE ACCOUNT'S
            STRIPE CARD ON FILE.

            Billed at $3 per address per month with no minimum term; the
            domain itself is included. The first month is charged immediately.
            Fail-closed — if the charge fails nothing is provisioned and the
            account is not billed. Idempotent per domain: re-running a
            successful purchase returns already_provisioned instead of charging
            again.

            Two modes:
            - Keep (default): you receive the domain's existing warmed
              mailboxes, credentials unchanged, usable as soon as this returns.
            - Custom (custom_usernames given): the warmed sample addresses are
              dropped and new mailboxes with your local-parts are provisioned
              on the same aged domain. Provisioning is asynchronous — the
              response returns provisioning "async" and the mailboxes appear on
              winnr_list_email_users a few minutes later. New mailboxes are not
              individually warmed but inherit the domain's reputation.

            Warming is stopped at sale either way; addresses arrive ready to
            send. Pre-warmed addresses are a separate pool from the regular
            mailbox allowance and do not count against the email user limit,
            but the domain does count against the domain limit.

            To buy several domains, use winnr_purchase_prewarmed_batch instead
            so the whole order is one charge.

            Args:
                domain: The pre-warmed domain to buy
                address_count: How many existing warmed addresses to take
                    (minimum 3, maximum 50). Ignored when custom_usernames is
                    given — the count comes from that list.
                custom_usernames: Optional. 3-10 unique local-parts (the part
                    before the @) to provision as new mailboxes instead.
            """
            body: dict = {"domain": domain, "address_count": address_count}
            if custom_usernames:
                body["custom_usernames"] = custom_usernames
            response = client.post("/v1/prewarmed/purchase", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_purchase_prewarmed_batch(items: list[dict]) -> str:
            """Buy pre-warmed addresses across SEVERAL domains as one order.
            CHARGES THE ACCOUNT'S STRIPE CARD ON FILE.

            Preferred over calling winnr_purchase_prewarmed repeatedly: the
            whole order is a single charge instead of one charge per domain.

            All-or-nothing. Every domain is claimed up front; if any domain has
            already been bought by someone else, or the charge fails, the entire
            order is rolled back and the account is not billed.

            Same terms as winnr_purchase_prewarmed: $3 per address per month,
            no minimum term, domain included, first month charged now.

            Args:
                items: 1-25 order items, each a dict with:
                    - domain (str, required): The pre-warmed domain
                    - address_count (int, required): Warmed addresses to take
                      (3-50)
                    - custom_usernames (list[str], optional): 3-10 local-parts
                      to provision as new mailboxes instead of the warmed ones
                    Duplicate domains in one order are rejected.
            """
            if not items:
                return "Error: items must contain at least one order item."
            if len(items) > 25:
                return (
                    f"Error: a batch order takes at most 25 domains, got {len(items)}. "
                    "Split it into multiple orders."
                )
            response = client.post("/v1/prewarmed/purchase-batch", json_body={"items": items})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_cancel_prewarmed(domain: str) -> str:
            """Cancel a purchased pre-warmed domain and stop its billing.

            DESTRUCTIVE — this deletes the domain and all of its mailboxes from
            the account, along with any mail stored in them, and returns the
            domain to the marketplace. It cannot be undone.

            Allowed at any time — there is no minimum term (the 90-day term
            was removed in August 2026).

            Args:
                domain: The purchased pre-warmed domain to cancel
            """
            response = client.post("/v1/prewarmed/cancel", json_body={"domain": domain})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
