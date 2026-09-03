# winnr-mcp

<!-- mcp-name: io.github.winnr-app/winnr-mcp -->

MCP server for the [Winnr](https://winnr.app) cold-email infrastructure API.

Lets Claude (web, mobile and desktop), ChatGPT, Claude Code, Cursor, Windsurf, VS Code
(Copilot) and any other [MCP](https://modelcontextprotocol.io) client manage your domains,
mailboxes, warming, inbox, pre-warmed marketplace and webhooks through natural language.

**55 tools**, 26 of them read-only, plus 8 resources and 5 prompt playbooks.

Two ways to run it:

| | Hosted (recommended) | Local |
|---|---|---|
| Endpoint | `https://mcp.winnr.app/mcp` | `uvx winnr-mcp` over stdio |
| Auth | Sign in with Winnr (OAuth 2.1 + PKCE) | API token in the config file |
| Install | none | `uv`, plus a config file per app |
| Works with | claude.ai web/mobile, ChatGPT, and every desktop client | desktop clients only |

**Setup either way:** [app.winnr.app/mcp](https://app.winnr.app/mcp).

---

## Hosted server

Add `https://mcp.winnr.app/mcp` as a custom connector / remote MCP server. The client
registers itself (RFC 7591), sends you to Winnr to sign in, and you choose what it may do:

| Scope | Grants |
|-------|--------|
| `read` | List and inspect everything. Always granted. |
| `write` | Create, change, send, delete. |
| `purchase` | Spend money: buy domains, buy pre-warmed domains, enable warming. |

Scopes imply each other (`purchase` ⊃ `write` ⊃ `read`), and a session only ever *sees*
the tools its scopes allow. Each grant is backed by a normal API token named
`MCP · <client>`, so it appears on the dashboard's API page and revoking it there cuts
the assistant off.

```bash
# Claude Code, hosted
claude mcp add --scope user --transport http winnr https://mcp.winnr.app/mcp
```

---

## Quick start (local)

### 1. Get a token

[app.winnr.app/mcp](https://app.winnr.app/mcp) (or API → Create Token). Tokens start with
`wnr_`. Pick **read-only** if you only want reports and reply triage: every tool that
creates, sends, buys or deletes is then hidden from the assistant. Add
`WINNR_NO_PURCHASES=true` (or `--no-purchases`) to keep full write access while hiding the
four tools that charge the card.

### 2. Install `uv`

The server runs with `uvx`, so `uv` must be installed once:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh      # or: brew install uv
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

No `uv`? `pip install winnr-mcp` and use `"command": "winnr-mcp"` with no args instead.

### 3. Add the server to your client

#### Claude Desktop

Settings → Developer → Edit Config, then paste (macOS
`~/Library/Application Support/Claude/claude_desktop_config.json`, Windows
`%APPDATA%\Claude\claude_desktop_config.json`). Fully quit and reopen Claude.

```json
{
  "mcpServers": {
    "winnr": {
      "command": "uvx",
      "args": ["winnr-mcp"],
      "env": { "WINNR_API_TOKEN": "wnr_your_token_here" }
    }
  }
}
```

#### Claude Code

```bash
claude mcp add --scope user winnr -e WINNR_API_TOKEN=wnr_your_token_here -- uvx winnr-mcp
```

Then `/mcp` inside Claude Code shows Winnr as connected. Optional guided workflows
(`/winnr setup`, `/winnr health`, `/winnr export`) come from
[winnr-claude-skills](https://github.com/winnr-app/winnr-claude-skills):

```bash
curl -sL https://raw.githubusercontent.com/winnr-app/winnr-claude-skills/main/install.sh | bash
```

#### Cursor

`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project) — same JSON as Claude
Desktop. Settings → MCP shows Winnr with a green dot when it is up.

#### Windsurf

`~/.codeium/windsurf/mcp_config.json` — same JSON. Refresh in Settings → Cascade → MCP Servers.

#### VS Code (Copilot agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "winnr": {
      "type": "stdio",
      "command": "uvx",
      "args": ["winnr-mcp"],
      "env": { "WINNR_API_TOKEN": "wnr_your_token_here" }
    }
  }
}
```

### 4. Try it

> What's in my Winnr account, and how much capacity do I have left?

The assistant calls `winnr_get_account` and `winnr_get_usage`. The
[app.winnr.app/mcp](https://app.winnr.app/mcp) page flips to **Connected** once the
token has been used.

## Configuration

| Source  | Variable / flag     | Description                                                    |
|---------|---------------------|----------------------------------------------------------------|
| Env var | `WINNR_API_TOKEN`   | **Required.** Your Winnr API token (`wnr_*`)                   |
| Env var | `WINNR_API_URL`     | API base URL (default `https://api.winnr.app`; resellers use their own host) |
| Env var | `WINNR_TIMEOUT`     | HTTP timeout in seconds (default 30; purchases use 60)         |
| Env var | `WINNR_READ_ONLY`   | `true` to register read tools only, even with a read/write token |
| Env var | `WINNR_NO_PURCHASES`| `true` to keep write access but hide the four money tools |
| Env var | `WINNR_CONFIRM_SECRET` | HMAC key for purchase confirmation tokens (set automatically on the hosted server) |
| CLI     | `--token`, `--api-url`, `--timeout`, `--read-only`, `--no-purchases`, `--version` | Override the env vars |

CLI args take precedence over environment variables.

At startup the server calls `GET /v1/account`. An invalid token exits immediately with
a clear message (a server that starts and then fails every call is worse). If the token
is **read-only**, write tools are hidden automatically — no flag needed.

## Spending money is always two steps

The four tools that charge the card — `winnr_purchase_domains`,
`winnr_purchase_prewarmed`, `winnr_purchase_prewarmed_batch`, `winnr_enable_warming` —
never charge on the first call. They return a **live quote** (availability re-checked,
exact prices, monthly total) plus a `confirmation_token` valid for 10 minutes. The
assistant shows the quote, gets an explicit yes, and calls again with the token. The
quote is recomputed at that moment and the token is an HMAC over it, so if a price moved
or a domain sold, the purchase is refused with a fresh quote instead of a surprise charge.

## How the assistant is guided

The server ships `instructions` to the client (most hosts put them in the system
prompt), and every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`) so hosts can ask for confirmation at the right moments. The
instructions cover:

- IDs and async jobs (`job_id` → poll `winnr_get_job`)
- The four tools that charge the card (domain purchase, pre-warmed purchase ×2, warming
  enable) and the rule to get an explicit yes with the exact price first. Domain purchases
  re-check availability and price right before ordering and refuse the order if anything
  changed, so the confirmed total is the charged total
- Never retrying a purchase after a timeout without checking `winnr_list_jobs`
- Domain-name hygiene (brand-like names; no outreach/blast/bulk words)
- Cold-email ratios (2–5 mailboxes per domain, warm 2–3 weeks, modest daily sends)

It also ships **resources** (read-only records the host can attach to a conversation:
`winnr://account`, `winnr://usage`, `winnr://domains`, `winnr://domains/{id}`,
`winnr://domains/{id}/dns-records`, `winnr://domains/{id}/dns-status`,
`winnr://warming/overview`, `winnr://jobs/{id}`) and **prompts** — parameterised
playbooks: `winnr_setup_infrastructure`, `winnr_health_check`, `winnr_reply_triage`,
`winnr_connect_own_domain`, `winnr_scale_up`.

## Tools

Permission is the token scope the tool needs. Read tools are visible to every token.

### Account, jobs, export

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_get_account` | Account, plan, limits, and the calling token's scope | read |
| `winnr_get_usage` | Domains / email users / pre-warmed addresses vs limits | read |
| `winnr_list_jobs` | Recent async jobs (status / type filters) | read |
| `winnr_get_job` | One job's status, progress, result, error | read |
| `winnr_wait_for_job` | Block until a job finishes, streaming progress notifications | read |
| `winnr_list_export_formats` | Supported CSV formats | read |
| `winnr_export_email_users` | CSV of credentials (15-minute link), 22 sequencer formats | write |

### Domains

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_domains` | List domains (paginated, optional status filter: complete, pending, …) | read |
| `winnr_get_domain` | One domain with DNS status and live health | read |
| `winnr_search_domains` | Availability + price for one name | read |
| `winnr_search_domains_bulk` | Availability + price for up to 100 names | read |
| `winnr_get_dns_status` | Provisioning/propagation state (MX, SPF, DKIM, DMARC) | read |
| `winnr_get_dns_records` | Records to add for manual-DNS domains | read |
| `winnr_check_dns_provider` | Where a domain's DNS is hosted today (≤20 per call) | read |
| `winnr_purchase_domains` | Buy + set up domains (quote → confirm, **charges card**) | purchase |
| `winnr_setup_domain` | Re-run DNS/mail provisioning, add mailboxes/redirect | write |
| `winnr_connect_domains` | Bring your own domains (nameserver, manual DNS, or Cloudflare token) | write |
| `winnr_check_nameservers` | Verify NS change; auto-queues provisioning | write |
| `winnr_verify_dns` | Live-verify manual-DNS records | write |
| `winnr_tag_domains` | Add/remove/set tags on up to 50 domains | write |
| `winnr_delete_domain` | Delete a domain and its mailboxes (**destructive**) | write |

### Mailboxes (email users)

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_email_users` | List mailboxes, filterable by domain | read |
| `winnr_get_email_user` | One mailbox with IMAP/SMTP details | read |
| `winnr_create_email_user` | Create one mailbox (async job) | write |
| `winnr_bulk_create_email_users` | Create up to 100 mailboxes on one domain | write |
| `winnr_update_email_user` | Rename or set password | write |
| `winnr_delete_email_user` | Delete a mailbox (**destructive**) | write |

### Inbox

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_inbox` | Messages across all mailboxes; warm-up hidden by default | read |
| `winnr_get_message_body` | Full body by `uid` + `mailbox` (truncated at 10k chars) | read |
| `winnr_send_email` | Send from a mailbox, with threading headers | write |
| `winnr_refresh_inbox` | Trigger a sync | write |
| `winnr_delete_message` | Delete one message (**destructive**) | write |

### Warming

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_warming` | Every warming mailbox with health/inbox rate | read |
| `winnr_get_warming_overview` | Aggregate stats + estimated monthly cost | read |
| `winnr_get_warming_metrics` | Daily series for one mailbox | read |
| `winnr_enable_warming` | Enable, `emails_per_day` 1–20, `rampup_speed` (quote → confirm, **$0.60/mailbox/mo**) | purchase |
| `winnr_disable_warming` | Disable and stop billing | write |
| `winnr_pause_warming` / `winnr_resume_warming` | Temporary stop / restart | write |
| `winnr_update_warming_settings` | `emails_per_day`, `rampup_enabled`, `rampup_speed` | write |

### Pre-warmed marketplace

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_browse_prewarmed` | Available aged, warmed domains | read |
| `winnr_get_prewarmed_domain` | Per-address health for one listing | read |
| `winnr_check_prewarmed_blocklist` | Live blocklist check (9 lists) | read |
| `winnr_list_my_prewarmed` | Purchased pre-warmed domains | read |
| `winnr_purchase_prewarmed` | Buy one domain, $3/address/mo (quote → confirm, **charges card**) | purchase |
| `winnr_purchase_prewarmed_batch` | Buy up to 25 domains as one charge (quote → confirm, **charges card**) | purchase |
| `winnr_cancel_prewarmed` | Cancel and return the domain (**destructive**) | write |

### Webhooks

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_webhooks` | Endpoints with status and health | read |
| `winnr_get_webhook_deliveries` | Recent delivery attempts | read |
| `winnr_create_webhook` | Create (response includes signing secret) | write |
| `winnr_update_webhook` | Change URL/events/description/status | write |
| `winnr_test_webhook` | Send a `test.ping` | write |
| `winnr_rotate_webhook_secret` | Rotate secret (old valid 24 h) | write |
| `winnr_get_webhook_secret` | Read the signing secret (sensitive; hidden from read-only tokens) | write |
| `winnr_delete_webhook` | Delete (**destructive**) | write |

## Errors

Every tool returns JSON. Failures look like:

```json
{ "error": { "message": "Payment required: …", "status_code": 402, "code": "payment_method_required" } }
```

so an agent can branch on `code`. Read-only 403s explain that the token lacks write
scope; 429s on reads are retried once automatically.

## Security

- **Token-scoped.** Everything runs as one account, with the token's permissions.
  Revoke it in the dashboard and the assistant is cut off instantly.
- **Passwords never appear in tool output.** Credentials leave only through
  `winnr_export_email_users`, a 15-minute presigned CSV link that needs a read/write token.
- **Nothing is logged.** The token is sent as a bearer header and never printed;
  the server writes one startup line to stderr.
- **Rate limits** are the API's (300 req/min Startup, 500 Enterprise). The server
  warns when fewer than 10 requests remain in the window.

## Development

```bash
git clone https://github.com/winnr-app/winnr-mcp.git
cd winnr-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest          # 124 tests, all HTTP mocked (incl. the full OAuth flow)
ruff check src tests
WINNR_API_TOKEN=wnr_xxx python -m winnr_mcp   # run locally over stdio

# the hosted server, locally
pip install -e ".[remote]"
uvicorn --factory winnr_mcp.remote.app:create_app --port 8000
```

### Deploying the hosted server

`python scripts/deploy_remote.py` provisions everything in AWS (DynamoDB table for OAuth
state, SSM secret, arm64 Lambda + layer, HTTP API, ACM certificate, `mcp.winnr.app`
domain and Route53 alias) and verifies the deployment.

## License

MIT
