"""Shared helpers for tool modules: annotations and small validators."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from mcp.types import ToolAnnotations

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
