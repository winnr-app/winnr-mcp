"""API error → MCP error normalization."""

from __future__ import annotations

from typing import Any


def extract_error(body: dict[str, Any] | None) -> tuple[str, str, Any, str | None]:
    """Pull (code, message, details, request_id) out of an API error body."""
    if not isinstance(body, dict):
        return "unknown", "Unknown error", None, None
    error = body.get("error")
    if not isinstance(error, dict):
        # Some gateway-level failures return {"message": "..."} only.
        message = body.get("message") if isinstance(body.get("message"), str) else "Unknown error"
        return "unknown", message, None, None
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    return (
        str(error.get("code", "unknown")),
        str(error.get("message", "Unknown error")),
        error.get("details"),
        meta.get("request_id"),
    )


def format_api_error(status_code: int, body: dict[str, Any] | None) -> str:
    """Convert an API error response into a clean error message for AI agents."""
    code, message, details, request_id = extract_error(body)

    if status_code == 400:
        return f"Bad request: {message}"

    if status_code == 401:
        return "Authentication failed. Check your WINNR_API_TOKEN (it must start with wnr_ and not be revoked)."

    if status_code == 402:
        return (
            f"Payment required: {message}. Add or update a payment method in the Winnr "
            "dashboard (Settings → Subscription) and try again."
        )

    if status_code == 403:
        if code == "insufficient_permissions":
            return (
                "Permission denied: this API token is read-only. Create a token with write "
                "permission in the Winnr dashboard (API → Create Token) to perform this action."
            )
        return f"Permission denied: {message}"

    if status_code == 404:
        return f"Not found: {message}"

    if status_code == 409:
        return f"Conflict: {message}"

    if status_code == 422:
        if isinstance(details, list) and details:
            detail_strs = []
            for d in details:
                if isinstance(d, dict):
                    detail_strs.append(f"  - {d.get('field', '?')}: {d.get('message', '?')}")
                else:
                    detail_strs.append(f"  - {d}")
            return "Validation error:\n" + "\n".join(detail_strs)
        return f"Validation error: {message}"

    if status_code == 429:
        return f"Rate limited. {message} Wait a few seconds before retrying."

    if status_code >= 500:
        rid = f" (request_id: {request_id})" if request_id else ""
        return f"API server error{rid}. Try again in a moment or contact support@winnr.app."

    return f"API error ({status_code}): {message}"


def format_network_error(api_url: str) -> str:
    """Format a network connectivity error."""
    return f"Could not reach Winnr API at {api_url}. Check network connectivity."


def format_timeout_error(timeout: int) -> str:
    """Format a timeout error."""
    return (
        f"Request timed out after {timeout}s. The operation may still be processing on the "
        "server — check winnr_list_jobs before retrying anything that charges a card."
    )
