"""API error → MCP error normalization."""

from __future__ import annotations

from typing import Any


def format_api_error(status_code: int, body: dict[str, Any] | None) -> str:
    """Convert an API error response into a clean error message for AI agents."""
    error = (body or {}).get("error", {})
    code = error.get("code", "unknown")
    message = error.get("message", "Unknown error")
    details = error.get("details")
    request_id = (body or {}).get("meta", {}).get("request_id")

    if status_code == 401:
        return "Authentication failed. Check your WINNR_API_TOKEN."

    if status_code == 403:
        return f"Permission denied: {message}. Your API token may need write permission."

    if status_code == 404:
        return f"Not found: {message}"

    if status_code == 409:
        return f"Conflict: {message}"

    if status_code == 422:
        if details:
            detail_strs = [f"  - {d.get('field', '?')}: {d.get('message', '?')}" for d in details]
            return f"Validation error:\n" + "\n".join(detail_strs)
        return f"Validation error: {message}"

    if status_code == 429:
        return f"Rate limited. {message}"

    if status_code >= 500:
        rid = f" (request_id: {request_id})" if request_id else ""
        return f"API server error{rid}. Try again or contact support."

    return f"API error ({status_code}): {message}"


def format_network_error(api_url: str) -> str:
    """Format a network connectivity error."""
    return f"Could not reach Winnr API at {api_url}. Check network connectivity."


def format_timeout_error(timeout: int) -> str:
    """Format a timeout error."""
    return f"Request timed out after {timeout}s. The operation may still be processing."
