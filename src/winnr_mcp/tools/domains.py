"""Domain tools — search, purchase, connect, setup, manage, and verify domains."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


def register_domain_tools(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    """Register domain-related MCP tools."""

    # ── Read tools (always registered) ──────────────────────────────────

    @mcp.tool()
    def winnr_list_domains(limit: int = 25, cursor: str | None = None) -> str:
        """List all email domains in your Winnr account.

        Returns domain names, status, DNS provider, tags, and email user counts.
        Supports pagination via limit and cursor.

        Args:
            limit: Page size (1-100, default 25)
            cursor: Pagination cursor from a previous response
        """
        params: dict = {"limit": min(max(limit, 1), 100)}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/domains", params=params)
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_domain(domain_id: str) -> str:
        """Get detailed information about a specific domain.

        Args:
            domain_id: The domain ID (from winnr_list_domains)
        """
        response = client.get(f"/v1/domains/{domain_id}")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_search_domains(domain: str) -> str:
        """Check if a single domain is available for registration.

        Returns availability status and price (in USD) via Dynadot.

        Args:
            domain: Domain to check (e.g., "example.com")
        """
        response = client.get("/v1/domains/search", params={"domain": domain})
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_search_domains_bulk(domains: list[str]) -> str:
        """Check availability of multiple domains at once (up to 100).

        Returns availability and pricing for each domain.

        Args:
            domains: List of domain names to check
        """
        response = client.post("/v1/domains/search-bulk", json_body={"domains": domains})
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_suggest_domains(keyword: str) -> str:
        """Get domain name suggestions based on a keyword.

        Args:
            keyword: Keyword to generate suggestions for
        """
        response = client.get("/v1/domains/suggest", params={"keyword": keyword})
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_dns_status(domain_id: str) -> str:
        """Check DNS propagation and health status for a domain.

        Returns the current state of DNS records (MX, SPF, DKIM, DMARC).

        Args:
            domain_id: The domain ID
        """
        response = client.get(f"/v1/domains/{domain_id}/dns-status")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    @mcp.tool()
    def winnr_get_dns_records(domain_id: str) -> str:
        """Get the expected DNS records for a domain with manual DNS setup.

        Returns the records that need to be configured at your DNS provider.
        Only applicable for domains using manual/external DNS.

        Args:
            domain_id: The domain ID
        """
        response = client.get(f"/v1/domains/{domain_id}/dns-records")
        if not response.ok:
            return response.error_message or "Unknown error"
        return response.to_json()

    # ── Write tools (only if token has write permission) ────────────────

    if "write" in config.permissions:

        @mcp.tool()
        def winnr_purchase_domains(domains: list[dict]) -> str:
            """Purchase and set up domains. CHARGES THE ACCOUNT'S STRIPE CARD ON FILE.

            Each domain costs the Dynadot registration price + $1. Domain credits
            from your plan are applied first (cheapest domains covered by credits).
            Payment is processed via Stripe before registration begins. Idempotent —
            will not double-charge if retried.

            Args:
                domains: List of domain objects, each with:
                    - domain (str, required): Domain name (e.g., "example.com")
                    - price (float): Expected price in USD (from winnr_search_domains)
                    - register (bool, default true): Register via Dynadot
                    - setup_dns (bool, default true): Setup DNS zone and records
                    - setup_email (bool, default true): Setup in Mailcow
                    - users (list[dict], optional): Email users to create
                      (each with "name" and "username" keys)
                    - redirect_url (str, optional): URL to redirect the domain to
                    - tags (list[str], optional): Tags for the domain
            """
            response = client.post("/v1/domains/purchase", json_body={"domains": domains})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_setup_domain(
            domain: str,
            register: bool = False,
            setup_dns: bool = True,
            setup_email: bool = True,
            redirect_url: str | None = None,
            tags: list[str] | None = None,
        ) -> str:
            """Queue a domain for setup (DNS + email provisioning).

            Use this for domains you already own. For purchasing new domains,
            use winnr_purchase_domains instead.

            Args:
                domain: Domain name (e.g., "example.com")
                register: Whether to register via Dynadot (default false)
                setup_dns: Setup DNS zone and records (default true)
                setup_email: Setup email in Mailcow (default true)
                redirect_url: Optional URL to redirect the domain to
                tags: Optional tags for the domain
            """
            body: dict = {
                "domain": domain,
                "register": register,
                "setup_dns": setup_dns,
                "setup_email": setup_email,
            }
            if redirect_url:
                body["redirect_url"] = redirect_url
            if tags:
                body["tags"] = tags
            response = client.post("/v1/domains/setup", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_connect_domains(
            domains: list[str],
            cloudflare_api_token: str | None = None,
        ) -> str:
            """Connect external domains you already own to your Winnr account.

            Returns nameservers to point your domains to. After updating
            nameservers at your registrar, use winnr_check_nameservers to verify.

            Args:
                domains: List of domain names to connect
                cloudflare_api_token: Optional Cloudflare API token for
                    automatic DNS zone creation
            """
            body: dict = {"domains": domains}
            if cloudflare_api_token:
                body["cloudflare_api_token"] = cloudflare_api_token
            response = client.post("/v1/domains/connect", json_body=body)
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_delete_domain(domain_id: str) -> str:
            """Delete a domain and all its email users.

            This queues the domain for deletion (async). Use winnr_get_job
            to track progress.

            Args:
                domain_id: The domain ID to delete
            """
            response = client.delete(f"/v1/domains/{domain_id}")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_verify_dns(domain_id: str) -> str:
            """Verify DNS records for a domain with manual DNS setup.

            Performs live DNS lookups to check if the required records
            (MX, SPF, DKIM, DMARC) are correctly configured.

            Args:
                domain_id: The domain ID
            """
            response = client.post(f"/v1/domains/{domain_id}/verify-dns")
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()

        @mcp.tool()
        def winnr_check_nameservers(domains: list[str]) -> str:
            """Verify that domains have nameservers correctly pointed to Winnr.

            After connecting domains and updating nameservers at your registrar,
            call this to verify propagation. If nameservers are correct, domain
            provisioning is automatically queued.

            Args:
                domains: List of domain names to check
            """
            response = client.post("/v1/domains/check-ns", json_body={"domains": domains})
            if not response.ok:
                return response.error_message or "Unknown error"
            return response.to_json()
