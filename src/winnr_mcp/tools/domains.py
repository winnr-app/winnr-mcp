"""Domain tools — search, purchase, connect, setup, manage, and verify domains."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp import scopes as sc
from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import (
    money_line,
    winnr_tool,
    is_str,
    DESTRUCTIVE,
    PURCHASE,
    READ,
    WRITE,
    WRITE_IDEMPOTENT,
    clamp,
    clean_domain,
    tool_error,
    seg,
)

# Domain doc statuses (winnr-api/docs/FIREBASE_SCHEMA.md). "complete" = ready to use.
DOMAIN_STATUSES = ("pending", "pending_ns", "pending_dns_records", "in_progress", "complete", "error", "deleting")
# /v1/domains/check-provider probes at most this many per call.
MAX_PROVIDER_CHECK = 20
MAX_SEARCH_BULK = 100
MAX_PURCHASE = 100
MAX_TAG_DOMAINS = 50
# Purchases run Stripe + registrar calls; give them more than the default.
PURCHASE_TIMEOUT_SECONDS = 60
# Only these per-domain fields are forwarded to POST /v1/domains/purchase.
PURCHASE_ENTRY_KEYS = ("domain", "price", "register", "setup_dns", "setup_email", "users", "redirect_url", "tags")


def register_domain_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register domain-related MCP tools."""

    # ── Read tools (always registered) ──────────────────────────────────

    @winnr_tool(mcp, sc.READ, READ, title="List domains")
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
            status: Optional filter: complete (ready), pending, pending_ns, pending_dns_records,
                in_progress, error, deleting
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

    @winnr_tool(mcp, sc.READ, READ, title="Get domain")
    def winnr_get_domain(domain_id: str) -> str:
        """Get one domain by id, including DNS/provisioning status and health.

        Args:
            domain_id: The domain ID (from winnr_list_domains)
        """
        return client.get(f"/v1/domains/{seg(domain_id)}").render()

    @winnr_tool(mcp, sc.READ, READ, title="Check one domain")
    def winnr_search_domains(domain: str) -> str:
        """Check whether ONE domain is available to register, with Winnr's price in USD.

        Price = registrar cost rounded up + $1, quoted live from the registrar.
        Some TLDs are blocked for cold email and come back unavailable with a reason. For several names at once
        use winnr_search_domains_bulk.

        Args:
            domain: Domain to check (e.g. "example.com")
        """
        d = clean_domain(domain)
        if not d or "." not in d:
            return tool_error("domain must be a full domain name like example.com")
        return client.get("/v1/domains/search", params={"domain": d}).render()

    @winnr_tool(mcp, sc.READ, READ, title="Check domains in bulk")
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

    @winnr_tool(mcp, sc.READ, READ, title="Get DNS status")
    def winnr_get_dns_status(domain_id: str) -> str:
        """Provisioning + propagation status of a domain's DNS (MX, SPF, DKIM, DMARC).

        Args:
            domain_id: The domain ID
        """
        return client.get(f"/v1/domains/{seg(domain_id)}/dns-status").render()

    @winnr_tool(mcp, sc.READ, READ, title="Get DNS records")
    def winnr_get_dns_records(domain_id: str) -> str:
        """The exact DNS records to add at the customer's own DNS host (manual-DNS domains).

        Only meaningful for domains connected with manual_dns=true; Winnr-managed
        domains have their records written automatically.

        Args:
            domain_id: The domain ID
        """
        return client.get(f"/v1/domains/{seg(domain_id)}/dns-records").render()

    @winnr_tool(mcp, sc.READ, READ, title="Check DNS provider")
    def winnr_check_dns_provider(domains: list[str]) -> str:
        """Detect where domains are currently hosted (registrar / nameserver provider).

        Pure lookup, nothing changes. Use before winnr_connect_domains to tell the
        user which provider's dashboard they will need to change nameservers in,
        or whether Cloudflare is already in play.

        Args:
            domains: Domain names to inspect (1-20 per call)
        """
        cleaned = [clean_domain(d) for d in (domains or []) if is_str(d)]
        if not cleaned:
            return tool_error("domains must contain at least one domain name")
        if len(cleaned) > MAX_PROVIDER_CHECK:
            return tool_error(f"Maximum {MAX_PROVIDER_CHECK} domains per call, got {len(cleaned)}")
        return client.post("/v1/domains/check-provider", json_body={"domains": cleaned}).render()

    # ── Write tools (only if token has write permission) ────────────────

    @winnr_tool(mcp, sc.PURCHASE, PURCHASE, title="Buy domains")
    def winnr_purchase_domains(domains: list[dict], confirmation_token: str | None = None) -> str:
        """Buy and fully set up new domains. CHARGES THE ACCOUNT'S CARD ON FILE.

        Two-step, nothing is charged on the first call:
        1. Call WITHOUT confirmation_token → every domain is checked live for
           availability and price; you get a quote (per-domain price, total, any
           domain credits) and a confirmation_token valid for 10 minutes.
        2. Show the quote, get an explicit yes, call again with the SAME domains
           plus confirmation_token. Prices are re-checked at that moment; if any
           changed, the order is refused with a fresh quote instead of charging
           a different amount.

        Price per domain = registrar cost rounded up + $1. Plan domain credits
        are applied first. The confirmed call is asynchronous: returns a job_id
        (HTTP 202); use winnr_wait_for_job. The job's `result` is the receipt; an
        `error` of payment_failed carries the decline reason. Registration + DNS
        + mail server takes a few minutes; the domain then shows status
        "complete". Mailboxes listed in `users` are created in the same job.

        After a timeout, check winnr_list_jobs before retrying — the order may
        have gone through.

        Args:
            domains: 1-100 objects, each with:
                - domain (str, required): e.g. "acmehq.com"
                - users (list[dict], optional): mailboxes to create, each with
                  "username" and "name" (2-5 per domain recommended)
                - redirect_url (str, optional): forward the website to this URL
                - tags (list[str], optional): tags for organizing the domain
            confirmation_token: Token from the quote step, after the user said yes
        """
        if not domains:
            return tool_error("domains must contain at least one order entry")
        if len(domains) > MAX_PURCHASE:
            return tool_error(f"Maximum {MAX_PURCHASE} domains per order, got {len(domains)}")
        order = []
        for i, entry in enumerate(domains):
            if not isinstance(entry, dict) or not is_str(entry.get("domain")):
                return tool_error(f"domains[{i}] must be an object with a 'domain' field")
            item = {k: entry[k] for k in PURCHASE_ENTRY_KEYS if k in entry}
            item["domain"] = clean_domain(entry["domain"])
            item.pop("price", None)  # always re-quoted live below
            order.append(item)
        seen: set[str] = set()
        for item in order:
            if item["domain"] in seen:
                return tool_error(f"Duplicate domain in order: {item['domain']}")
            seen.add(item["domain"])

        quote_or_error = _quote_domains(client, order)
        if isinstance(quote_or_error, str):
            return quote_or_error
        quote = quote_or_error
        subject = client.account_id or ""
        if not confirmation_token:
            token, exp = config.confirmer.issue("purchase_domains", subject, quote)
            return json.dumps({
                "quote": quote,
                "summary": f"Buy {len(order)} domain(s) for {money_line(quote['total'])} one-time (plus any mailboxes/warming you enable later).",
                "confirmation_token": token,
                "expires_at": exp,
                "next_step": "Show the quote to the user. After an explicit yes, call winnr_purchase_domains again with the same domains and this confirmation_token.",
            }, indent=2)
        reason = config.confirmer.verify(confirmation_token, "purchase_domains", subject, quote)
        if reason:
            return tool_error(
                f"Confirmation {reason}. Nothing was bought. Call without confirmation_token to get a fresh quote "
                "and confirm the new total with the user.",
                code="confirmation_invalid",
                quote=quote,
            )
        priced = []
        for item in order:
            entry = dict(item)
            entry["price"] = quote["prices"][item["domain"]]
            priced.append(entry)
        return client.post(
            "/v1/domains/purchase",
            json_body={"domains": priced, "async": True},
            timeout=PURCHASE_TIMEOUT_SECONDS,
        ).render()

    @winnr_tool(mcp, sc.WRITE, WRITE, title="Set up domain")
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

    @winnr_tool(mcp, sc.WRITE, WRITE, title="Connect your own domains")
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

    @winnr_tool(mcp, sc.WRITE, DESTRUCTIVE, title="Delete domain")
    def winnr_delete_domain(domain_id: str) -> str:
        """Permanently delete a domain and every mailbox (and all mail) on it.

        Asynchronous (job_id). Warming campaigns are torn down and billing for
        the mailboxes stops. A purchased domain stays registered until it expires
        but is removed from the account. Pre-warmed domains are routed to the
        marketplace cancel path instead. Confirm with the user first.

        Args:
            domain_id: The domain ID to delete
        """
        return client.delete(f"/v1/domains/{seg(domain_id)}").render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT, title="Tag domains")
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
                current_resp = client.get(f"/v1/domains/{seg(domain_id)}")
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
            patch_resp = client.patch(f"/v1/domains/{seg(domain_id)}", json_body={"tags": new_tags})
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

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT, title="Verify DNS records")
    def winnr_verify_dns(domain_id: str) -> str:
        """Live-check a manual-DNS domain's records (MX, SPF, DKIM, DMARC) and record the result.

        Run after the customer has added the records from winnr_get_dns_records.
        Marks the domain ready when everything resolves correctly.

        Args:
            domain_id: The domain ID
        """
        return client.post(f"/v1/domains/{seg(domain_id)}/verify-dns").render()

    @winnr_tool(mcp, sc.WRITE, WRITE_IDEMPOTENT, title="Check nameservers")
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


def _quote_domains(client: WinnrClient, order: list[dict]) -> dict | str:
    """Live availability + price for every domain in the order.

    Returns the quote dict (what the confirmation token signs), or a tool_error
    string if any domain is unavailable or unpriced. Entries with register=false
    (setup-only) are not quoted and cost nothing.
    """
    to_quote = sorted(o["domain"] for o in order if o.get("register", True))
    prices: dict[str, float] = {}
    if to_quote:
        check = client.post("/v1/domains/search-bulk", json_body={"domains": to_quote})
        if not check.ok:
            return tool_error(
                f"Could not check availability/prices, so nothing was bought: {check.error_message}",
                code="price_check_failed",
            )
        results = (check.data or {}).get("results") if isinstance(check.data, dict) else None
        live = {}
        for r in results or []:
            if isinstance(r, dict) and is_str(r.get("domain")):
                live[clean_domain(r["domain"])] = r
        problems = []
        for domain in to_quote:
            r = live.get(domain)
            if r is None:
                problems.append(f"{domain}: no availability result")
            elif not r.get("available"):
                problems.append(f"{domain}: not available ({r.get('error') or 'taken'})")
            elif r.get("price") is None:
                problems.append(f"{domain}: no price returned")
            else:
                prices[domain] = round(float(r["price"]), 2)
        if problems:
            return tool_error(
                "Cannot quote this order: " + "; ".join(problems) + ". Remove or replace those domains.",
                code="unavailable",
            )
    return {
        "domains": [o["domain"] for o in order],
        "prices": prices,
        "total": round(sum(prices.values()), 2),
        "note": "Plan domain credits, if any, are applied by the API and reduce the charged amount.",
    }
