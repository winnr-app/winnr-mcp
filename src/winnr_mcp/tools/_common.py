"""Shared helpers for tool modules: annotations and small validators."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from winnr_mcp import scopes as _scopes

# MCP tool annotations. Clients use these to decide when to ask the user for
# confirmation (destructive / non-idempotent) and which tools are safe to call
# freely (read-only). Every tool declares exactly one of these.
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Creates or changes state, but re-running it with the same input converges
# (update name, tag domains, enable warming on the same ids, ...).
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
# Creates something new or sends something each time it is called.
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
# Charges the account's card. Not destructive in the MCP sense (nothing is
# deleted) but never safe to retry blindly — the description says so.
PURCHASE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
# Deletes or cancels something that cannot be undone.
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)


def tool_error(message: str, **extra: Any) -> str:
    """Local (pre-request) validation error in the same JSON shape as API errors."""
    payload: dict[str, Any] = {"error": {"message": message, "status_code": 0, "code": "invalid_arguments"}}
    payload["error"].update(extra)
    return json.dumps(payload, indent=2)


def clamp(value: int, low: int, high: int) -> int:
    return min(max(int(value), low), high)


def clean_domain(domain: str) -> str:
    """Normalize a domain the way the API does: lowercase, no scheme, no trailing dot."""
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip("/").rstrip(".")


def seg(value: str) -> str:
    """URL-encode one path segment so it can never change the route.

    '/', '?', '#' are percent-encoded by quote(); dots are encoded too, so a
    value of '.' or '..' cannot be collapsed as a dot-segment by the HTTP client.
    """
    return quote(str(value or "").strip(), safe="").replace(".", "%2E")


def is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# tool name -> scope it needs. Filled by winnr_tool(); read by the tools/list filter.
TOOL_SCOPES: dict[str, str] = {}


def scope_error(tool_name: str, needed: str) -> str:
    have = sorted(_scopes.current_scopes())
    if needed == _scopes.PURCHASE:
        why = "this session may not spend money"
    elif needed == _scopes.WRITE:
        why = "this session is read-only"
    else:
        why = "this session has no read access"
    return tool_error(
        f"{tool_name} needs the '{needed}' scope but {why} (scopes: {', '.join(have) or 'none'}). "
        "Reconnect with a token/consent that grants it: https://app.winnr.app/mcp",
        code="insufficient_scope",
        needed_scope=needed,
    )


def winnr_tool(mcp: FastMCP, scope: str, annotations: ToolAnnotations, **kwargs: Any) -> Callable:
    """Register a tool that requires `scope`.

    The check happens at call time against the session's current scopes, so
    one server instance can serve read-only, read/write and purchasing sessions
    (the remote OAuth server does). tools/list is filtered separately so a
    session never sees a tool it cannot call.
    """
    if scope not in _scopes.ALL_SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        TOOL_SCOPES[fn.__name__] = scope

        @functools.wraps(fn)
        def guarded(*args: Any, **kw: Any) -> str:
            if scope not in _scopes.current_scopes():
                return scope_error(fn.__name__, scope)
            return fn(*args, **kw)

        mcp.tool(annotations=annotations, **kwargs)(guarded)
        return fn

    return decorator


def money_line(amount: float) -> str:
    return f"${amount:,.2f}"
