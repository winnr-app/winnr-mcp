"""MCP resources — read-only documents an assistant can pull into context.

Resources are the "give me the record" side of MCP (tools are the "do
something" side). Hosts let users attach them to a conversation directly, and
assistants can read them without spending a tool call.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from winnr_mcp import scopes as sc
from winnr_mcp.client import WinnrClient, WinnrResponse
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools._common import seg


def _text(response: WinnrResponse) -> str:
    return response.render()


def _guard() -> str | None:
    if sc.READ not in sc.current_scopes():
        return json.dumps({"error": {"message": "This session has no read scope.", "code": "insufficient_scope"}})
    return None


def register_resources(mcp: FastMCP, client: WinnrClient, config: WinnrConfig) -> None:
    @mcp.resource("winnr://account", name="Account", mime_type="application/json",
                  description="The Winnr account: plan, limits, usage counters, token scope.")
    def account() -> str:
        return _guard() or _text(client.get("/v1/account"))

    @mcp.resource("winnr://usage", name="Usage", mime_type="application/json",
                  description="Domains, email users and pre-warmed addresses used vs. limits.")
    def usage() -> str:
        return _guard() or _text(client.get("/v1/account/usage"))

    @mcp.resource("winnr://domains", name="Domains", mime_type="application/json",
                  description="First 100 domains with status, DNS health, tags and mailbox counts.")
    def domains() -> str:
        return _guard() or _text(client.get("/v1/domains", params={"limit": 100}))

    @mcp.resource("winnr://domains/{domain_id}", name="Domain", mime_type="application/json",
                  description="One domain by id, with DNS/provisioning status and live health.")
    def domain(domain_id: str) -> str:
        return _guard() or _text(client.get(f"/v1/domains/{seg(domain_id)}"))

    @mcp.resource("winnr://domains/{domain_id}/dns-records", name="DNS records", mime_type="application/json",
                  description="The DNS records a manual-DNS domain needs at the customer's DNS host.")
    def dns_records(domain_id: str) -> str:
        return _guard() or _text(client.get(f"/v1/domains/{seg(domain_id)}/dns-records"))

    @mcp.resource("winnr://domains/{domain_id}/dns-status", name="DNS status", mime_type="application/json",
                  description="Provisioning and propagation state of a domain's MX/SPF/DKIM/DMARC.")
    def dns_status(domain_id: str) -> str:
        return _guard() or _text(client.get(f"/v1/domains/{seg(domain_id)}/dns-status"))

    @mcp.resource("winnr://warming/overview", name="Warming overview", mime_type="application/json",
                  description="Aggregate warming health across the account.")
    def warming_overview() -> str:
        return _guard() or _text(client.get("/v1/warming/overview"))

    @mcp.resource("winnr://jobs/{job_id}", name="Job", mime_type="application/json",
                  description="Status, progress and result of one async job.")
    def job(job_id: str) -> str:
        return _guard() or _text(client.get(f"/v1/jobs/{seg(job_id)}"))
