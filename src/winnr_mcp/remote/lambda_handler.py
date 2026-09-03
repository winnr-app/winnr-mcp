"""AWS Lambda entrypoint: API Gateway (HTTP API v2) → Mangum → Starlette."""

from __future__ import annotations

from mangum import Mangum

from winnr_mcp.remote.app import create_app

app = create_app()
handler = Mangum(app, lifespan="auto")
