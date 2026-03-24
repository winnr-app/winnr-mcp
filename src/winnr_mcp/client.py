"""HTTP client wrapper for the Winnr API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from winnr_mcp.config import WinnrConfig
from winnr_mcp.errors import format_api_error, format_network_error, format_timeout_error

# Truncate email bodies to prevent context window blowout
MAX_BODY_LENGTH = 10_000


@dataclass
class WinnrResponse:
    """Normalized API response."""

    ok: bool
    status_code: int
    data: Any = None
    pagination: dict[str, Any] | None = None
    error_message: str | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None

    def to_json(self) -> str:
        """Serialize response data to JSON string for MCP tool output."""
        result: dict[str, Any] = {}
        if self.data is not None:
            result["data"] = self.data
        if self.pagination:
            result["pagination"] = self.pagination
        if self.rate_limit_remaining is not None and self.rate_limit_remaining < 10:
            result["warning"] = (
                f"Only {self.rate_limit_remaining} API requests remaining in this rate limit window."
            )
        return json.dumps(result, indent=2, default=str)


class WinnrClient:
    """HTTP client for api.winnr.app."""

    def __init__(self, config: WinnrConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={
                "Authorization": f"Bearer {config.api_token}",
                "Content-Type": "application/json",
                "User-Agent": "winnr-mcp/0.1.0",
            },
            timeout=config.timeout,
        )
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: int | None = None

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _extract_rate_limits(self, response: httpx.Response) -> None:
        """Extract rate limit headers from response."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            try:
                self._rate_limit_remaining = int(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                self._rate_limit_reset = int(reset)
            except ValueError:
                pass

    def _make_response(self, response: httpx.Response) -> WinnrResponse:
        """Parse an httpx response into a WinnrResponse."""
        self._extract_rate_limits(response)

        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            body = None

        if response.status_code >= 400:
            return WinnrResponse(
                ok=False,
                status_code=response.status_code,
                error_message=format_api_error(response.status_code, body),
                rate_limit_remaining=self._rate_limit_remaining,
                rate_limit_reset=self._rate_limit_reset,
            )

        data = body.get("data") if body else None
        pagination = body.get("pagination") if body else None

        # The API wraps paginated route responses as:
        #   {"data": {"data": [...], "pagination": {...}}, "meta": {...}}
        # Unwrap the inner data/pagination if present.
        if isinstance(data, dict) and "data" in data and "pagination" in data:
            pagination = data["pagination"]
            data = data["data"]

        return WinnrResponse(
            ok=True,
            status_code=response.status_code,
            data=data,
            pagination=pagination,
            rate_limit_remaining=self._rate_limit_remaining,
            rate_limit_reset=self._rate_limit_reset,
        )

    def _handle_error(self, exc: Exception) -> WinnrResponse:
        """Convert an exception into a WinnrResponse."""
        if isinstance(exc, httpx.ConnectError):
            return WinnrResponse(
                ok=False,
                status_code=0,
                error_message=format_network_error(self._config.api_url),
            )
        if isinstance(exc, httpx.TimeoutException):
            return WinnrResponse(
                ok=False,
                status_code=0,
                error_message=format_timeout_error(self._config.timeout),
            )
        return WinnrResponse(
            ok=False,
            status_code=0,
            error_message=f"Unexpected error: {exc}",
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> WinnrResponse:
        """GET request."""
        try:
            response = self._client.get(path, params=params)
            return self._make_response(response)
        except Exception as exc:
            return self._handle_error(exc)

    def post(self, path: str, json_body: dict[str, Any] | None = None) -> WinnrResponse:
        """POST request."""
        try:
            response = self._client.post(path, json=json_body)
            return self._make_response(response)
        except Exception as exc:
            return self._handle_error(exc)

    def patch(self, path: str, json_body: dict[str, Any] | None = None) -> WinnrResponse:
        """PATCH request."""
        try:
            response = self._client.patch(path, json=json_body)
            return self._make_response(response)
        except Exception as exc:
            return self._handle_error(exc)

    def delete(self, path: str) -> WinnrResponse:
        """DELETE request."""
        try:
            response = self._client.delete(path)
            return self._make_response(response)
        except Exception as exc:
            return self._handle_error(exc)
