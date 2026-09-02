"""MCP prompts — reusable, parameterised workflows.

These carry the cold-email operating knowledge that used to live only in the
separate Claude Code skills, so any MCP host (claude.ai, Cursor, ...) can run
the same playbooks. Each prompt returns instructions the assistant follows
with the tools; none of them spend money without the two-step confirmation.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(name="winnr_setup_infrastructure", title="Set up cold-email infrastructure",
                description="Buy domains, create mailboxes and start warming for a company, end to end.")
    def setup_infrastructure(company: str, mailboxes: int = 10, tld_preference: str = ".com") -> str:
        per_domain = 3
        domains_needed = max(1, -(-int(mailboxes) // per_domain))
        return f"""You are setting up cold-email sending infrastructure on Winnr for "{company}".
Target: {mailboxes} mailboxes across about {domains_needed} domains ({per_domain} per domain — never more than 5).

Follow these steps and stop for the user's decision where marked:
1. winnr_get_account and winnr_get_usage — confirm capacity and that a payment method/plan exists.
2. Generate 15-25 candidate domain names that look like a real company (brand + short word:
   {company.lower().replace(' ', '')}hq{tld_preference}, try{company.lower().replace(' ', '')}{tld_preference},
   {company.lower().replace(' ', '')}team{tld_preference}). Prefer {tld_preference}, then .net/.org/.co.
   Never use outreach/blast/bulk/mail/marketing/campaign in a name and never the company's main domain.
3. winnr_search_domains_bulk on all candidates. Pick the {domains_needed} best available ones.
4. Plan {per_domain} mailboxes per domain with realistic first.last names (vary the pattern).
5. STOP: present domains, prices, the mailbox plan and warming cost ({mailboxes} × $0.60/month), and ask for a yes.
6. winnr_purchase_domains without confirmation_token to get the quote, then again with the
   confirmation_token after the user's explicit yes. Include the `users` per domain in the order.
7. winnr_wait_for_job on the returned job until it completes (domains take a few minutes).
8. winnr_list_email_users per domain to collect the new mailbox ids.
9. winnr_enable_warming (quote → confirm) on all of them with emails_per_day 15, rampup_speed normal.
10. Tell the user: warm for 2-3 weeks before any cold sending, keep sends at 10-15/mailbox/day early on,
    and that credentials come from winnr_export_email_users in their sequencer's format."""

    @mcp.prompt(name="winnr_health_check", title="Infrastructure health check",
                description="Traffic-light report across domains, DNS, warming and capacity.")
    def health_check() -> str:
        return """Produce a health report for this Winnr account.
1. winnr_list_domains (page through if has_more). For each domain note status (complete = ready), dns_health,
   ns_status and email_users_count.
2. winnr_get_warming_overview, then winnr_list_warming. Flag mailboxes with health score < 60 or inbox rate < 80%.
3. winnr_get_usage for capacity.
4. winnr_list_jobs status=error for anything that failed recently.
Report as three groups — RED (action needed now), YELLOW (watch), GREEN — with one line per item and the concrete
next step for every RED/YELLOW. Do not change anything; this is read-only."""

    @mcp.prompt(name="winnr_reply_triage", title="Triage replies",
                description="Classify real replies from the last N days and draft answers.")
    def reply_triage(days: int = 3) -> str:
        return f"""Triage replies received in the last {days} days.
1. winnr_refresh_inbox, then winnr_list_inbox with date_from = today minus {days} days (exclude_warmup stays true).
2. For each message read the body with winnr_get_message_body (uid + mailbox from the same row).
3. Classify: interested / question / not now / unsubscribe / out-of-office / bounce / other.
4. For interested and question, draft a short plain-text reply. For unsubscribe, note the address to suppress.
5. Present a table (from, mailbox, class, one-line summary) and the drafts. Send NOTHING until the user approves
   each draft; then use winnr_send_email from the receiving mailbox's user_id with in_reply_to set."""

    @mcp.prompt(name="winnr_connect_own_domain", title="Connect a domain I already own",
                description="Bring an externally registered domain into Winnr, nameserver or manual-DNS mode.")
    def connect_own_domain(domain: str) -> str:
        return f"""Connect {domain}, which the user already owns, to Winnr.
1. winnr_check_dns_provider on it to learn where its DNS lives today.
2. Recommend nameserver mode (Winnr hosts DNS, best deliverability) unless the domain already serves a website or
   other mail the user must keep — then manual_dns=true.
3. winnr_connect_domains accordingly. Show the user exactly what to change at their registrar/DNS host.
4. Nameserver mode: after they confirm the change, winnr_check_nameservers (propagation can take up to a day; retry
   rather than re-adding). Manual mode: winnr_get_dns_records, then winnr_verify_dns once the records are in.
5. When the domain reaches status complete, offer to create 2-5 mailboxes and start warming."""

    @mcp.prompt(name="winnr_scale_up", title="Add sending capacity",
                description="Add N mailboxes with the right domain ratios and warming.")
    def scale_up(mailboxes: int) -> str:
        return f"""Add {mailboxes} more mailboxes to this account.
1. winnr_list_domains — reuse domains with fewer than 3 mailboxes first (max 5 per domain).
2. Only if needed, buy new domains following the naming rules (brand-like, no cold-email words, .com first).
3. Present the plan and monthly cost; get a yes.
4. Create mailboxes with winnr_bulk_create_email_users (one call per domain), wait for the jobs.
5. Enable warming (quote → confirm) and remind the user to warm 2-3 weeks before sending."""
