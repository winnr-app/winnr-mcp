"""HTTP client wrapper for the Winnr API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from winnr_mcp import __version__
from winnr_mcp.config import WinnrConfig
from winnr_mcp.errors import (
    extract_error,
    format_api_error,
    format_network_error,
    format_timeout_error,
)

# Truncate email bodies to prevent context window blowout
MAX_BODY_LENGTH = 10_000

# One automatic retry on 429, capped so a tool call never silently stalls.
MAX_RATE_LIMIT_SLEEP_SECONDS = 10


@dataclass
class WinnrResponse:
    """Normalized API response."""

    ok: bool
    status_code: int
    data: Any = None
    pagination: dict[str, Any] | None = None
    error_message: str | None = None
    error_code: str | None = None
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

    def error_json(self) -> str:
        """Serialize an error as a JSON object so agents can branch on it."""
        payload: dict[str, Any] = {
            "error": {
                "message": self.error_message or "Unknown error",
                "status_code": self.status_code,
            }
        }
        if self.error_code:
            payload["error"]["code"] = self.error_code
        return json.dumps(payload, indent=2)

    def render(self) -> str:
        """Success → data JSON, failure → error JSON. The one return path for tools."""
        return self.to_json() if self.ok else self.error_json()


class WinnrClient:
    """HTTP client for api.winnr.app."""

    def __init__(self, config: WinnrConfig) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.api_url,
            headers={
                "Authorization": f"Bearer {config.api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"winnr-mcp/{__version__}",
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
        if not isinstance(body, dict):
            body = None

        if response.status_code >= 400:
            code, _, _, _ = extract_error(body)
            return WinnrResponse(
                ok=False,
                status_code=response.status_code,
                error_message=format_api_error(response.status_code, body),
                error_code=code if code != "unknown" else None,
                rate_limit_remaining=self._rate_limit_remaining,
                rate_limit_reset=self._rate_limit_reset,
            )

        data = body.get("data") if body else None
        pagination = body.get("pagination") if body else None

        # Older API builds wrapped paginated responses as
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
                error_code="network_error",
            )
        if isinstance(exc, httpx.TimeoutException):
            return WinnrResponse(
                ok=False,
                status_code=0,
                error_message=format_timeout_error(self._config.timeout),
                error_code="timeout",
            )
        return WinnrResponse(
            ok=False,
            status_code=0,
            error_message=f"Unexpected error: {exc}",
            error_code="unexpected_error",
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        """Seconds to wait before a single retry of a 429, capped."""
        raw = response.headers.get("Retry-After")
        try:
            wait = float(raw) if raw else 1.0
        except ValueError:
            wait = 1.0
        return max(0.5, min(wait, MAX_RATE_LIMIT_SLEEP_SECONDS))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> WinnrResponse:
        """Issue a request with one automatic retry on 429 (idempotent GETs only)."""
        kwargs: dict[str, Any] = {"params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            response = self._client.request(method, path, **kwargs)
            if response.status_code == 429 and method == "GET":
                time.sleep(self._retry_after_seconds(response))
                response = self._client.request(method, path, **kwargs)
            return self._make_response(response)
        except Exception as exc:
            return self._handle_error(exc)

    def get(self, path: str, params: dict[str, Any] | None = None) -> WinnrResponse:
        """GET request."""
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> WinnrResponse:
        """POST request. `params` go on the query string (some POST routes read filters there)."""
        return self._request("POST", path, params=params, json_body=json_body, timeout=timeout)

    def patch(self, path: str, json_body: dict[str, Any] | None = None) -> WinnrResponse:
        """PATCH request."""
        return self._request("PATCH", path, json_body=json_body)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> WinnrResponse:
        """DELETE request."""
        return self._request("DELETE", path, params=params)
