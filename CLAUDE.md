# winnr-mcp

MCP (Model Context Protocol) server for the Winnr email infrastructure API. Thin REST API wrapper — all operations go through api.winnr.app.

## Run locally
```bash
source .venv/bin/activate
WINNR_API_TOKEN=wnr_xxx python -m winnr_mcp
```

## Run tests
```bash
source .venv/bin/activate
pytest
```

## Key paths
- Entrypoint: `src/winnr_mcp/__main__.py`
- Server: `src/winnr_mcp/server.py` (FastMCP, tool registration)
- HTTP client: `src/winnr_mcp/client.py` (httpx wrapper for api.winnr.app)
- Config: `src/winnr_mcp/config.py` (env var / CLI arg loading)
- Error mapping: `src/winnr_mcp/errors.py`
- Tools: `src/winnr_mcp/tools/` (account, domains, email_users, inbox, warming, prewarmed, jobs, export, webhooks)
- Tests: `tests/`

## Architecture
- Pure HTTP client — no direct Firestore/Mailcow/AWS access
- Auth via Winnr API token (`wnr_*` format) passed as env var `WINNR_API_TOKEN`
- Permission gating: write tools hidden if token is read-only
- Rate limit tracking from API response headers

## Conventions
- Type hints required everywhere
- Tools return JSON strings (not dicts) for MCP TextContent
- Errors returned as TextContent with is_error=True, never raise
- Tool names prefixed with `winnr_` to avoid MCP namespace collisions

## Official MCP Registry listing

The hosted server is listed in the official registry (registry.modelcontextprotocol.io) as `app.winnr/winnr`
(remote-only: `https://mcp.winnr.app/mcp`, published 2026-10-07; feeds Glama and PulseMCP. Smithery shut down its
hosted proxy on 2026-10-31 and is now only a directory). Namespace
ownership is proven by DNS: an apex TXT `v=MCPv1; k=ed25519; p=...` on winnr.app (Route 53, alongside the SPF
value; never UPSERT that RRSet without keeping SPF). To publish a new version: bump `version` in a remote-only
server.json, then
`mcp-publisher login dns --domain winnr.app --private-key "$(aws --profile winnr ssm get-parameter --name /winnr/mcp-registry/DNS_AUTH_PRIVATE_KEY --with-decryption --query Parameter.Value --output text)"`
and `mcp-publisher publish`, then `mcp-publisher logout`. The repo's `server.json` (`io.github.winnr-app/winnr-mcp`,
PyPI package) is a separate, unpublished entry: publishing it needs GitHub auth as a winnr-app org member; use
the `winnr-team` user, never a personal account (the `io.github.*` namespace is public).

## Pushing (public repo)

Commit as `Winnr <engineering@winnr.app>` and push only as the `winnr-team` GitHub user (Owner of `winnr-app`,
classic PAT in SSM `/winnr/github/BOT_TOKEN`, expires 2027-01-18). The local clone has a repo-local
`credential.helper` that reads that token, so a plain `git push` already does this.
