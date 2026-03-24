"""Shared test fixtures."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


@pytest.fixture
def config() -> WinnrConfig:
    """Test configuration with read+write permissions."""
    return WinnrConfig(
        api_token="wnr_test123_abcdefghijklmnopqrstuvwx",
        api_url="https://api.test.winnr.app",
        timeout=10,
        permissions=["read", "write"],
        account_id="test123",
        plan="startup",
    )


@pytest.fixture
def read_only_config() -> WinnrConfig:
    """Test configuration with read-only permissions."""
    return WinnrConfig(
        api_token="wnr_test123_abcdefghijklmnopqrstuvwx",
        api_url="https://api.test.winnr.app",
        timeout=10,
        permissions=["read"],
        account_id="test123",
        plan="startup",
    )


def make_api_response(
    data: Any = None,
    status_code: int = 200,
    pagination: dict | None = None,
    error: dict | None = None,
    rate_limit_remaining: int = 100,
) -> httpx.Response:
    """Build a mock httpx.Response matching the Winnr API format."""
    body: dict[str, Any] = {
        "meta": {
            "request_id": "req_test_123",
            "timestamp": "2026-03-24T12:00:00Z",
        }
    }
    if error:
        body["error"] = error
    else:
        body["data"] = data
        if pagination:
            body["pagination"] = pagination

    response = httpx.Response(
        status_code=status_code,
        json=body,
        headers={
            "X-RateLimit-Remaining": str(rate_limit_remaining),
            "X-RateLimit-Reset": "1711281600",
        },
    )
    return response


# Common response factories

def account_response() -> httpx.Response:
    return make_api_response(data={
        "id": "test123",
        "name": "Test Account",
        "email": "test@example.com",
        "plan": "startup",
        "domains_limit": 10,
        "email_users_limit": 50,
        "created_at": "2026-01-01T00:00:00Z",
    })


def usage_response() -> httpx.Response:
    return make_api_response(data={
        "domains": {"used": 3, "limit": 10},
        "email_users": {"used": 15, "limit": 50},
    })


def domain_list_response() -> httpx.Response:
    return make_api_response(
        data=[
            {
                "id": "dom_1",
                "name": "example.com",
                "status": "active",
                "dns_provider": "clouddns1",
                "ns_status": True,
                "tags": ["cold-outreach"],
                "email_users_count": 5,
                "created_at": "2026-01-15T00:00:00Z",
            }
        ],
        pagination={"cursor": None, "has_more": False, "total": 1},
    )


def email_user_list_response() -> httpx.Response:
    return make_api_response(
        data=[
            {
                "id": "eu_1",
                "username": "john.doe",
                "domain": "example.com",
                "full_address": "john.doe@example.com",
                "name": "John Doe",
                "status": "active",
                "created_at": "2026-02-01T00:00:00Z",
            }
        ],
        pagination={"cursor": None, "has_more": False, "total": 1},
    )


def job_response() -> httpx.Response:
    return make_api_response(data={
        "job_id": "job_abc123",
        "type": "domain_setup",
        "status": "completed",
        "progress": None,
        "result": {"domain": "example.com"},
        "error": None,
        "created_at": "2026-03-24T12:00:00Z",
        "updated_at": "2026-03-24T12:01:00Z",
        "completed_at": "2026-03-24T12:01:00Z",
    })


def error_response(status_code: int = 400, code: str = "validation_error", message: str = "Bad request") -> httpx.Response:
    return make_api_response(
        status_code=status_code,
        error={"code": code, "message": message},
    )
