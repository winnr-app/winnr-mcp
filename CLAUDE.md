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
- Tools: `src/winnr_mcp/tools/` (account, domains, email_users, inbox, warming, jobs, export)
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
