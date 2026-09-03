"""AWS Lambda entrypoint: API Gateway (HTTP API v2) → Mangum → Starlette.

The MCP SDK's StreamableHTTPSessionManager may only be started once per
instance, but Mangum runs the ASGI lifespan on every invocation, which made
the second request onwards fail with "run() can only be called once". So the
lifespan is entered once here, at cold start, on the same event loop Mangum
later reuses for each request, and Mangum's own lifespan handling is off.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack

from mangum import Mangum

from winnr_mcp.remote.app import create_app

app = create_app()

# One loop for the life of the execution environment. Mangum's HTTPCycle calls
# asyncio.get_event_loop(), so it picks this one up and the session manager's
# task group stays valid across invocations.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)
_stack = AsyncExitStack()


async def _startup() -> None:
    await _stack.enter_async_context(app.router.lifespan_context(app))


try:
    _loop.run_until_complete(_startup())
except Exception as exc:  # noqa: BLE001 — never fail the import; /healthz must still answer
    print(f"winnr-mcp remote: lifespan startup failed: {exc}", file=sys.stderr)

handler = Mangum(app, lifespan="off")
