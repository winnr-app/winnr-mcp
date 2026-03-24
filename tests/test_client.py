"""Tests for the HTTP client wrapper."""

from __future__ import annotations

import json

import httpx
import respx
import pytest

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig


@pytest.fixture
def client_config() -> WinnrConfig:
    return WinnrConfig(
        api_token="wnr_test123_abcdefghijklmnopqrstuvwx",
        api_url="https://api.test.winnr.app",
        timeout=10,
    )


@respx.mock
def test_get_success(client_config):
    """GET request returns parsed data on success."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": "test123", "plan": "startup"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
            headers={"X-RateLimit-Remaining": "99"},
        )
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/account")
    assert response.ok is True
    assert response.data["id"] == "test123"
    assert response.rate_limit_remaining == 99


@respx.mock
def test_get_with_params(client_config):
    """GET request passes query params."""
    route = respx.get("https://api.test.winnr.app/v1/domains").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [],
                "pagination": {"cursor": None, "has_more": False, "total": 0},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/domains", params={"limit": 10})
    assert response.ok is True
    assert response.pagination is not None


@respx.mock
def test_post_success(client_config):
    """POST request sends JSON body."""
    respx.post("https://api.test.winnr.app/v1/email-users").mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {"id": "eu_1", "username": "john"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.post("/v1/email-users", json_body={"username": "john", "domain": "example.com"})
    assert response.ok is True
    assert response.data["id"] == "eu_1"


@respx.mock
def test_error_401(client_config):
    """401 returns auth error message."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": {"code": "unauthorized", "message": "Invalid token"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/account")
    assert response.ok is False
    assert "Authentication failed" in response.error_message


@respx.mock
def test_error_429(client_config):
    """429 returns rate limit message."""
    respx.get("https://api.test.winnr.app/v1/domains").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {"code": "rate_limited", "message": "Rate limit exceeded. Retry after 5 seconds"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/domains")
    assert response.ok is False
    assert "Rate limited" in response.error_message


@respx.mock
def test_network_error(client_config):
    """Network errors return clean message."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/account")
    assert response.ok is False
    assert "Could not reach" in response.error_message


@respx.mock
def test_timeout_error(client_config):
    """Timeout errors return clean message."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        side_effect=httpx.ReadTimeout("Read timed out")
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/account")
    assert response.ok is False
    assert "timed out" in response.error_message


@respx.mock
def test_rate_limit_warning_in_json(client_config):
    """Low rate limit remaining triggers warning in JSON output."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": "test123"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
            headers={"X-RateLimit-Remaining": "5"},
        )
    )
    client = WinnrClient(client_config)
    response = client.get("/v1/account")
    assert response.ok is True
    json_output = json.loads(response.to_json())
    assert "warning" in json_output
    assert "5" in json_output["warning"]


@respx.mock
def test_delete_request(client_config):
    """DELETE request works."""
    respx.delete("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"job_id": "job_del_1"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.delete("/v1/domains/dom_1")
    assert response.ok is True
    assert response.data["job_id"] == "job_del_1"


@respx.mock
def test_patch_request(client_config):
    """PATCH request sends JSON body."""
    respx.patch("https://api.test.winnr.app/v1/email-users/eu_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": "eu_1", "name": "Updated Name"},
                "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
            },
        )
    )
    client = WinnrClient(client_config)
    response = client.patch("/v1/email-users/eu_1", json_body={"name": "Updated Name"})
    assert response.ok is True
    assert response.data["name"] == "Updated Name"
