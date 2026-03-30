# winnr-mcp

MCP server for the [Winnr](https://winnr.app) email infrastructure API.

> Full documentation below. This README is also the primary docs for the package.

---

## What is this?

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets AI assistants like Claude, Cursor, and Windsurf manage your Winnr email infrastructure through natural language.

**36 tools** covering:
- Domain management (search, purchase, connect, DNS verification)
- Email user/mailbox provisioning (create, update, delete, bulk)
- Inbox operations (list, read, send, refresh)
- Email warming (enable, pause, resume, metrics)
- Job tracking (async operation monitoring)
- Data export (CSV for Smartlead, Instantly, Snov, etc.)

## Quick Start

### 1. Get your API token

Sign up at [app.winnr.app](https://app.winnr.app), go to **Settings → API Tokens**, and create a token.

### 2. Configure your AI assistant

#### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "winnr": {
      "command": "uvx",
      "args": ["winnr-mcp"],
      "env": {
        "WINNR_API_TOKEN": "wnr_your_token_here"
      }
    }
  }
}
```

#### Cursor

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "winnr": {
      "command": "uvx",
      "args": ["winnr-mcp"],
      "env": {
        "WINNR_API_TOKEN": "wnr_your_token_here"
      }
    }
  }
}
```

#### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "winnr": {
      "command": "uvx",
      "args": ["winnr-mcp"],
      "env": {
        "WINNR_API_TOKEN": "wnr_your_token_here"
      }
    }
  }
}
```

#### Claude Code

```bash
claude mcp add winnr -- env WINNR_API_TOKEN=wnr_your_token_here uvx winnr-mcp
```

**Want guided workflows?** Install [Winnr Claude Code Skills](https://github.com/winnr-app/winnr-claude-skills) for slash commands like `/winnr setup`, `/winnr health`, and `/winnr export`:

```bash
curl -sL https://raw.githubusercontent.com/winnr-app/winnr-claude-skills/main/install.sh | bash
```

### 3. Alternative: pip install

```bash
pip install winnr-mcp
WINNR_API_TOKEN=wnr_xxx winnr-mcp
```

Or run directly without installing:

```bash
WINNR_API_TOKEN=wnr_xxx uvx winnr-mcp
```

## Configuration

| Source | Variable | Description |
|--------|----------|-------------|
| Env var | `WINNR_API_TOKEN` | **Required.** Your Winnr API token (`wnr_*`) |
| Env var | `WINNR_API_URL` | API base URL (default: `https://api.winnr.app`) |
| Env var | `WINNR_TIMEOUT` | HTTP timeout in seconds (default: 30) |
| CLI arg | `--token` | Override `WINNR_API_TOKEN` |
| CLI arg | `--api-url` | Override `WINNR_API_URL` |
| CLI arg | `--timeout` | Override `WINNR_TIMEOUT` |

CLI args take precedence over environment variables.

## Tools Reference

### Account

| Tool | Description |
|------|-------------|
| `winnr_get_account` | Get account details (plan, limits, subscription) |
| `winnr_get_usage` | Get current usage vs. plan limits |

### Domains

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_domains` | List all domains (paginated) | read |
| `winnr_get_domain` | Get domain details | read |
| `winnr_search_domains` | Check single domain availability | read |
| `winnr_search_domains_bulk` | Bulk availability check (up to 100) | read |
| `winnr_suggest_domains` | Get domain suggestions for a keyword | read |
| `winnr_purchase_domains` | Purchase + setup domains (**charges Stripe card**) | write |
| `winnr_setup_domain` | Queue domain setup (no purchase) | write |
| `winnr_connect_domains` | Connect external domains | write |
| `winnr_delete_domain` | Delete a domain | write |
| `winnr_get_dns_status` | Check DNS propagation | read |
| `winnr_get_dns_records` | Get expected DNS records | read |
| `winnr_verify_dns` | Verify DNS via live lookup | write |
| `winnr_check_nameservers` | Verify nameserver pointing | write |

### Email Users

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_email_users` | List mailboxes (filterable by domain) | read |
| `winnr_get_email_user` | Get mailbox details | read |
| `winnr_create_email_user` | Create a mailbox | write |
| `winnr_update_email_user` | Update name/password | write |
| `winnr_delete_email_user` | Delete a mailbox | write |
| `winnr_bulk_create_email_users` | Create up to 100 mailboxes | write |

### Inbox

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_inbox` | List messages (date/mailbox filterable) | read |
| `winnr_get_message_body` | Get full email body | read |
| `winnr_send_email` | Send an email from a mailbox | write |
| `winnr_refresh_inbox` | Trigger inbox sync | write |
| `winnr_delete_message` | Delete a message | write |

### Warming

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_warming` | List warming-enabled mailboxes | read |
| `winnr_get_warming_overview` | Aggregate warming stats | read |
| `winnr_get_warming_metrics` | Daily metrics for a mailbox | read |
| `winnr_enable_warming` | Enable warming ($0.60/mailbox/mo) | write |
| `winnr_disable_warming` | Disable warming | write |
| `winnr_pause_warming` | Pause warming | write |
| `winnr_resume_warming` | Resume warming | write |
| `winnr_update_warming_settings` | Update volume/ramp-up/reply rate | write |

### Jobs

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_list_jobs` | List recent async jobs | read |
| `winnr_get_job` | Get job status/progress | read |

### Export

| Tool | Description | Permission |
|------|-------------|------------|
| `winnr_export_email_users` | Export to CSV (15+ formats) | read |

## Security

### Token-based authentication

All requests use your Winnr API token. Tokens are scoped to your account and can be revoked instantly from the dashboard.

### Permission gating

If your API token has read-only permissions, write tools (create, update, delete, send) are completely hidden — the AI assistant won't even see them.

### Rate limiting

Same limits as the REST API:
- **Startup plan**: 300 requests/minute
- **Enterprise plan**: 500 requests/minute

The server tracks rate limit headers and warns when you're running low.

### Data protection

- Passwords are never echoed in tool responses
- Email bodies are truncated to 10K characters to prevent context overflow
- Your API token is never logged or included in error messages

## Development

```bash
git clone https://github.com/winnr-app/winnr-mcp.git
cd winnr-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT
