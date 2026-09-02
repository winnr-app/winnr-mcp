"""Domain tools — search, purchase, connect, setup, manage, and verify domains."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import (
    DESTRUCTIVE,
    PURCHASE,
    READ,
    WRITE,
    WRITE_IDEMPOTENT,
    clamp,
    clean_domain,
    tool_error,
)

DOMAIN_STATUSES = ("active", "pending", "pending_ns", "disabled", "error", "deleting")
MAX_SEARCH_BULK = 100
MAX_PURCHASE = 100
MAX_TAG_DOMAINS = 50
# Purchases run Stripe + registrar calls; give them more than the default.
PURCHASE_TIMEOUT_SECONDS = 60


def register_domain_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register domain-related MCP tools."""

    # ── Read tools (always registered) ──────────────────────────────────

    @mcp.tool(annotations=READ)
    def winnr_list_domains(
        limit: int = 25,
        cursor: str | None = None,
        status: str | None = None,
    ) -> str:
        """List the domains in the account.

        Returns id, name, status, dns_provider, dns_status, ns_status, live DNS
        health, registrar, redirect/forward settings, tags, email user count and
        expiry. Paginated; follow `pagination.cursor` while `has_more` is true.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
            status: Optional status filter: active, pending, pending_ns, disabled, error
        """
        params: dict = {"limit": clamp(limit, 1, 100)}
        if cursor:
            params["cursor"] = cursor
        if status:
            s = status.strip().lower()
            if s not in DOMAIN_STATUSES:
                return tool_error(f"status must be one of {', '.join(DOMAIN_STATUSES)}")
            params["filter[status]"] = s
        return client.get("/v1/domains", params=params).render()

    @mcp.tool(annotations=READ)
    def winnr_get_domain(domain_id: str) -> str:
        """Get one domain by id, including DNS/provisioning status and health.

        Args:
            domain_id: The domain ID (from winnr_list_domains)
        """
        return client.get(f"/v1/domains/{domain_id}").render()

    @mcp.tool(annotations=READ)
    def winnr_search_domains(domain: str) -> str:
        """Check whether ONE domain is available to register, with Winnr's price in USD.

        Price = registrar cost rounded up + $1. Some TLDs are blocked for cold
        email and come back unavailable with a reason. For several names at once
        use winnr_search_domains_bulk.

        Args:
            domain: Domain to check (e.g. "example.com")
        """
        d = clean_domain(domain)
        if not d or "." not in d:
            return tool_error("domain must be a full domain name like example.com")
        return client.get("/v1/domains/search", params={"domain": d}).render()

    @mcp.tool(annotations=READ)
    def winnr_search_domains_bulk(domains: list[str]) -> str:
        """Check availability and price for up to 100 domains in one call.

        The right way to propose names: generate candidates that look like a real
        company (brand + short word: acmehq.com, tryacme.com, acmeteam.com — never
        outreach/blast/bulk/mail/marketing), check them here, then show the user
        the available ones with prices before buying.

        Args:
            domains: Domain names to check (1-100)
        """
        cleaned = [clean_domain(d) for d in (domains or []) if d and d.strip()]
        if not cleaned:
            return tool_error("domains must contain at least one domain name")
        if len(cleaned) > MAX_SEARCH_BULK:
            return tool_error(f"Maximum {MAX_SEARCH_BULK} domains per call, got {len(cleaned)}")
        return client.post("/v1/domains/search-bulk", json_body={"domains": cleaned}).render()

    @mcp.tool(annotations=READ)
    def winnr_get_dns_status(domain_id: str) -> str:
        """Provisioning + propagation status of a domain's DNS (MX, SPF, DKIM, DMARC).

        Args:
            domain_id: The domain ID
        """
        return client.get(f"/v1/domains/{domain_id}/dns-status").render()

    @mcp.tool(annotations=READ)
    def winnr_get_dns_records(domain_id: str) -> str:
        """The exact DNS records to add at the customer's own DNS host (manual-DNS domains).

        Only meaningful for domains connected with manual_dns=true; Winnr-managed
        domains have their records written automatically.

        Args:
            domain_id: The domain ID
        """
        return client.get(f"/v1/domains/{domain_id}/dns-records").render()

    @mcp.tool(annotations=READ)
    def winnr_check_dns_provider(domains: list[str]) -> str:
        """Detect where domains are currently hosted (registrar / nameserver provider).

        Pure lookup, nothing changes. Use before winnr_connect_domains to tell the
        user which provider's dashboard they will need to change nameservers in,
        or whether Cloudflare is already in play.

        Args:
            domains: Domain names to inspect (1-100)
        """
        cleaned = [clean_domain(d) for d in (domains or []) if d and d.strip()]
        if not cleaned:
            return tool_error("domains must contain at least one domain name")
        if len(cleaned) > MAX_SEARCH_BULK:
            return tool_error(f"Maximum {MAX_SEARCH_BULK} domains per call, got {len(cleaned)}")
        return client.post("/v1/domains/check-provider", json_body={"domains": cleaned}).render()

    # ── Write tools (only if token has write permission) ────────────────

    if not config.can_write:
        return

    @mcp.tool(annotations=PURCHASE)
    def winnr_purchase_domains(domains: list[dict]) -> str:
        """Buy and fully set up new domains. CHARGES THE ACCOUNT'S CARD ON FILE.

        Price per domain = registrar cost rounded up + $1 (from winnr_search_domains).
        Plan domain credits are applied first. Get an explicit yes from the user
        with the exact list and total before calling.

        Asynchronous: returns a job_id (HTTP 202). Poll winnr_get_job — the job's
        `result` is the full purchase receipt; an `error` of payment_failed carries
        the decline reason. Registration + DNS + mail server takes a few minutes;
        the domain then shows status "active" in winnr_list_domains. Mailboxes
        listed in `users` are created as part of the same job.

        Idempotent per order: re-sending the same domains does not double-charge,
        but after a timeout check winnr_list_jobs before retrying.

        Args:
            domains: 1-100 objects, each with:
                - domain (str, required): e.g. "acmehq.com"
                - price (float, recommended): expected USD price from the search
                  tool; the order is rejected if the live price is higher
                - users (list[dict], optional): mailboxes to create, each with
                  "username" and "name" (2-5 per domain recommended)
                - redirect_url (str, optional): forward the website to this URL
                - tags (list[str], optional): tags for organizing the domain
        """
        if not domains:
            return tool_error("domains must contain at least one order entry")
        if len(domains) > MAX_PURCHASE:
            return tool_error(f"Maximum {MAX_PURCHASE} domains per order, got {len(domains)}")
        order = []
        for i, entry in enumerate(domains):
            if not isinstance(entry, dict) or not (entry.get("domain") or "").strip():
                return tool_error(f"domains[{i}] must be an object with a 'domain' field")
            item = dict(entry)
            item["domain"] = clean_domain(entry["domain"])
            order.append(item)
        return client.post(
            "/v1/domains/purchase",
            json_body={"domains": order, "async": True},
            timeout=PURCHASE_TIMEOUT_SECONDS,
        ).render()

    @mcp.tool(annotations=WRITE)
    def winnr_setup_domain(
        domain: str,
        setup_dns: bool = True,
        setup_email: bool = True,
        users: list[dict] | None = None,
        redirect_url: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Re-run DNS and/or mail-server provisioning for a domain already in the account.

        No purchase happens here. Typical uses: a domain stuck in "error", or
        adding mailboxes + a redirect in one job. For a domain the customer owns
        elsewhere use winnr_connect_domains first. Returns a job_id.

        Args:
            domain: Domain name (e.g. "example.com")
            setup_dns: Create/repair the DNS zone and records (default true)
            setup_email: Create/repair the domain on the mail server (default true)
            users: Optional mailboxes to create, each {"username", "name"}
            redirect_url: Optional URL to redirect the domain's website to
            tags: Optional tags
        """
        body: dict = {
            "domain": clean_domain(domain),
            "register": False,
            "setup_dns": setup_dns,
            "setup_email": setup_email,
        }
        if users:
            body["users"] = users
        if redirect_url:
            body["redirect_url"] = redirect_url
        if tags:
            body["tags"] = tags
        return client.post("/v1/domains/setup", json_body=body).render()

    @mcp.tool(annotations=WRITE)
    def winnr_connect_domains(
        domains: list[str],
        manual_dns: bool = False,
        cloudflare_api_token: str | None = None,
    ) -> str:
        """Bring domains the customer already owns (registered elsewhere) into Winnr.

        Three modes, pick one:
        - Nameserver mode (default): Winnr hosts the DNS. Response lists the
          nameservers to set at the registrar; after the user changes them, call
          winnr_check_nameservers — provisioning starts automatically once they
          resolve. Best deliverability, simplest for the user.
        - manual_dns=true: the customer keeps their DNS host and adds the records
          themselves. Read them with winnr_get_dns_records, then winnr_verify_dns.
        - cloudflare_api_token: the domain is already on the customer's Cloudflare;
          Winnr writes the records there directly with the token.

        Args:
            domains: Domain names to connect (1-100)
            manual_dns: Keep the customer's DNS and hand them records (default false)
            cloudflare_api_token: Cloudflare API token with DNS edit rights on the zone
        """
        cleaned = [clean_domain(d) for d in (domains or []) if d and d.strip()]
        if not cleaned:
            return tool_error("domains must contain at least one domain name")
        if len(cleaned) > MAX_SEARCH_BULK:
            return tool_error(f"Maximum {MAX_SEARCH_BULK} domains per call, got {len(cleaned)}")
        if manual_dns and cloudflare_api_token:
            return tool_error("Choose either manual_dns or cloudflare_api_token, not both")
        body: dict = {"domains": cleaned}
        if manual_dns:
            body["manual_dns"] = True
        if cloudflare_api_token:
            body["cloudflare_api_token"] = cloudflare_api_token.strip()
        return client.post("/v1/domains/connect", json_body=body).render()

    @mcp.tool(annotations=DESTRUCTIVE)
    def winnr_delete_domain(domain_id: str) -> str:
        """Permanently delete a domain and every mailbox (and all mail) on it.

        Asynchronous (job_id). Warming campaigns are torn down and billing for
        the mailboxes stops. A purchased domain stays registered until it expires
        but is removed from the account. Pre-warmed domains are routed to the
        marketplace cancel path instead. Confirm with the user first.

        Args:
            domain_id: The domain ID to delete
        """
        return client.delete(f"/v1/domains/{domain_id}").render()

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_tag_domains(domain_ids: list[str], tags: list[str], mode: str = "add") -> str:
        """Add, remove, or replace tags on up to 50 domains.

        Tags are free-form labels shown in winnr_list_domains and filterable in
        the dashboard (e.g. a client name or campaign).

        Args:
            domain_ids: Domain IDs to update (from winnr_list_domains), max 50
            tags: Tag strings to add/remove/set
            mode: "add" (default), "remove", or "set" (replace the whole list)
        """
        mode = (mode or "add").strip().lower()
        if mode not in ("add", "remove", "set"):
            return tool_error('mode must be "add", "remove", or "set"')
        if not domain_ids:
            return tool_error("domain_ids is required")
        if len(domain_ids) > MAX_TAG_DOMAINS:
            return tool_error(f"Maximum {MAX_TAG_DOMAINS} domains per call")
        cleaned = [t.strip() for t in tags if t and t.strip()]
        if not cleaned and mode != "set":
            return tool_error("tags is required for add/remove")

        results = []
        for domain_id in domain_ids:
            if mode == "set":
                new_tags = cleaned
            else:
                # read-modify-write: PATCH replaces the whole list
                current_resp = client.get(f"/v1/domains/{domain_id}")
                if not current_resp.ok:
                    results.append({
                        "domain_id": domain_id, "ok": False,
                        "error": current_resp.error_message or "Unknown error",
                    })
                    continue
                current = (current_resp.data or {}).get("tags") or []
                if mode == "add":
                    new_tags = current + [t for t in cleaned if t not in current]
                else:  # remove
                    new_tags = [t for t in current if t not in cleaned]
            patch_resp = client.patch(f"/v1/domains/{domain_id}", json_body={"tags": new_tags})
            if not patch_resp.ok:
                results.append({
                    "domain_id": domain_id, "ok": False,
                    "error": patch_resp.error_message or "Unknown error",
                })
            else:
                results.append({"domain_id": domain_id, "ok": True, "tags": new_tags})

        updated = sum(1 for r in results if r["ok"])
        return json.dumps({
            "mode": mode, "updated": updated,
            "failed": len(results) - updated, "results": results,
        })

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_verify_dns(domain_id: str) -> str:
        """Live-check a manual-DNS domain's records (MX, SPF, DKIM, DMARC) and record the result.

        Run after the customer has added the records from winnr_get_dns_records.
        Marks the domain ready when everything resolves correctly.

        Args:
            domain_id: The domain ID
        """
        return client.post(f"/v1/domains/{domain_id}/verify-dns").render()

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def winnr_check_nameservers(domains: list[str]) -> str:
        """Check whether connected domains now point at Winnr's nameservers.

        Call after the customer updates nameservers at their registrar. When the
        nameservers resolve correctly, provisioning is queued automatically (a
        job per domain). Propagation can take minutes to a day; if it fails, tell
        the user to wait and try again rather than re-adding the domain.

        Args:
            domains: Domain names to check
        """
        cleaned = [clean_domain(d) for d in (domains or []) if d and d.strip()]
        if not cleaned:
            return tool_error("domains must contain at least one domain name")
        return client.post("/v1/domains/check-ns", json_body={"domains": cleaned}).render()
