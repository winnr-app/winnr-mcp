"""Tests for webhook MCP tools."""

from __future__ import annotations

import json

import httpx
import respx

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools.webhooks import register_webhook_tools


# ── Helpers ─────────────────────────────────────────────────────────────

def make_mcp_and_client(config: WinnrConfig) -> tuple[FastMCP, WinnrClient]:
    mcp = FastMCP("test")
    client = WinnrClient(config)
    return mcp, client


def api_success(data=None, pagination=None) -> httpx.Response:
    body: dict = {"meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"}}
    if data is not None:
        body["data"] = data
    if pagination:
        body["pagination"] = pagination
    return httpx.Response(200, json=body, headers={"X-RateLimit-Remaining": "50"})


def api_error(status_code=400, code="validation_error", message="Bad request") -> httpx.Response:
    return httpx.Response(
        status_code,
        json={
            "error": {"code": code, "message": message},
            "meta": {"request_id": "req_1", "timestamp": "2026-03-24T12:00:00Z"},
        },
    )


# ── Permission gating ───────────────────────────────────────────────────

def test_webhook_write_tools_hidden_read_only(read_only_config):
    """Webhook write tools (including secret access) hidden for read-only tokens."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_webhook_tools(mcp, client, read_only_config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    # Read tools should be present
    assert "winnr_list_webhooks" in tool_names
    assert "winnr_get_webhook_deliveries" in tool_names
    # Write tools should be absent
    assert "winnr_create_webhook" not in tool_names
    assert "winnr_update_webhook" not in tool_names
    assert "winnr_delete_webhook" not in tool_names
    assert "winnr_test_webhook" not in tool_names
    assert "winnr_get_webhook_secret" not in tool_names
    assert "winnr_rotate_webhook_secret" not in tool_names


def test_webhook_write_tools_registered_with_write_permission(config):
    """All 8 webhook tools registered when token has write permission."""
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert len(tool_names) == 8
    assert "winnr_create_webhook" in tool_names
    assert "winnr_get_webhook_secret" in tool_names
    assert "winnr_rotate_webhook_secret" in tool_names


# ── Functional tests (mocked API) ───────────────────────────────────────

@respx.mock
def test_winnr_list_webhooks(config):
    """winnr_list_webhooks returns the webhook list."""
    respx.get("https://api.test.winnr.app/v1/webhooks").mock(
        return_value=api_success(
            data=[
                {
                    "id": "wh_1",
                    "url": "https://example.com/hooks/winnr",
                    "events": ["email.bounced", "domain.ready"],
                    "status": "enabled",
                }
            ]
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_list_webhooks"].fn()
    data = json.loads(result)
    assert data["data"][0]["id"] == "wh_1"
    assert data["data"][0]["events"] == ["email.bounced", "domain.ready"]


@respx.mock
def test_winnr_get_webhook_deliveries(config):
    """winnr_get_webhook_deliveries passes limit as a query param."""
    route = respx.get("https://api.test.winnr.app/v1/webhooks/wh_1/deliveries").mock(
        return_value=api_success(
            data=[{"event": "email.bounced", "status_code": 200, "success": True}]
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_get_webhook_deliveries"].fn(
        webhook_id="wh_1", limit=10
    )
    data = json.loads(result)
    assert data["data"][0]["success"] is True
    assert route.calls[0].request.url.params["limit"] == "10"


@respx.mock
def test_winnr_create_webhook(config):
    """winnr_create_webhook posts url + events and surfaces the signing secret."""
    route = respx.post("https://api.test.winnr.app/v1/webhooks").mock(
        return_value=api_success(
            data={
                "id": "wh_new",
                "url": "https://example.com/hooks/winnr",
                "events": ["*"],
                "secret": "whsec_abc123",
            }
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_create_webhook"].fn(
        url="https://example.com/hooks/winnr",
        events=["*"],
        description="All events",
    )
    data = json.loads(result)
    assert data["data"]["id"] == "wh_new"
    assert data["data"]["secret"] == "whsec_abc123"
    body = json.loads(route.calls[0].request.content)
    assert body == {
        "url": "https://example.com/hooks/winnr",
        "events": ["*"],
        "description": "All events",
    }


@respx.mock
def test_winnr_create_webhook_error(config):
    """winnr_create_webhook returns a clean error on API failure."""
    respx.post("https://api.test.winnr.app/v1/webhooks").mock(
        return_value=api_error(400, "validation_error", "Invalid event type: bogus.event")
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_create_webhook"].fn(
        url="https://example.com/hooks/winnr", events=["bogus.event"]
    )
    # Rejected locally before any request: the error names the bad event.
    assert "bogus.event" in result
    assert json.loads(result)["error"]["code"] == "invalid_arguments"


@respx.mock
def test_winnr_update_webhook_builds_partial_body(config):
    """winnr_update_webhook PATCHes only the provided fields."""
    route = respx.patch("https://api.test.winnr.app/v1/webhooks/wh_1").mock(
        return_value=api_success(data={"id": "wh_1", "status": "enabled"})
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_update_webhook"].fn(
        webhook_id="wh_1", status="enabled"
    )
    data = json.loads(result)
    assert data["data"]["status"] == "enabled"
    assert json.loads(route.calls[0].request.content) == {"status": "enabled"}


def test_winnr_update_webhook_requires_a_field(config):
    """winnr_update_webhook with no fields is rejected locally."""
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_update_webhook"].fn(webhook_id="wh_1")
    assert "At least one field" in result


@respx.mock
def test_winnr_delete_webhook(config):
    """winnr_delete_webhook issues a DELETE."""
    respx.delete("https://api.test.winnr.app/v1/webhooks/wh_1").mock(
        return_value=api_success(data={"deleted": True})
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_delete_webhook"].fn(webhook_id="wh_1")
    assert json.loads(result)["data"]["deleted"] is True


@respx.mock
def test_winnr_test_webhook(config):
    """winnr_test_webhook posts to the test endpoint."""
    respx.post("https://api.test.winnr.app/v1/webhooks/wh_1/test").mock(
        return_value=api_success(data={"event": "test.ping", "delivered": True})
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_test_webhook"].fn(webhook_id="wh_1")
    assert json.loads(result)["data"]["event"] == "test.ping"


@respx.mock
def test_winnr_get_webhook_secret(config):
    """winnr_get_webhook_secret returns the secret payload."""
    respx.get("https://api.test.winnr.app/v1/webhooks/wh_1/secret").mock(
        return_value=api_success(data={"secret": "whsec_abc123"})
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_get_webhook_secret"].fn(webhook_id="wh_1")
    assert json.loads(result)["data"]["secret"] == "whsec_abc123"


@respx.mock
def test_winnr_rotate_webhook_secret(config):
    """winnr_rotate_webhook_secret posts to rotate-secret."""
    respx.post("https://api.test.winnr.app/v1/webhooks/wh_1/rotate-secret").mock(
        return_value=api_success(
            data={"secret": "whsec_new456", "old_secret_valid_until": "2026-08-11T12:00:00Z"}
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_webhook_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_rotate_webhook_secret"].fn(webhook_id="wh_1")
    assert json.loads(result)["data"]["secret"] == "whsec_new456"
