"""Tests for MCP tool registration and behavior."""

from __future__ import annotations

import json

import httpx
import respx
import pytest

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools.account import register_account_tools
from winnr_mcp.tools.domains import register_domain_tools
from winnr_mcp.tools.email_users import register_email_user_tools
from winnr_mcp.tools.inbox import register_inbox_tools
from winnr_mcp.tools.warming import register_warming_tools
from winnr_mcp.tools.jobs import register_job_tools
from winnr_mcp.tools.export import register_export_tools


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


# ── Permission gating tests ────────────────────────────────────────────

def test_write_tools_registered_with_write_permission(config):
    """Write tools are registered when token has write permission."""
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "winnr_purchase_domains" in tool_names
    assert "winnr_delete_domain" in tool_names
    assert "winnr_connect_domains" in tool_names


def test_write_tools_hidden_with_read_only(read_only_config):
    """Write tools are NOT registered when token is read-only."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_domain_tools(mcp, client, read_only_config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    # Read tools should be present
    assert "winnr_list_domains" in tool_names
    assert "winnr_get_domain" in tool_names
    # Write tools should be absent
    assert "winnr_purchase_domains" not in tool_names
    assert "winnr_delete_domain" not in tool_names
    assert "winnr_connect_domains" not in tool_names


def test_email_user_write_tools_hidden_read_only(read_only_config):
    """Email user write tools hidden for read-only tokens."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_email_user_tools(mcp, client, read_only_config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "winnr_list_email_users" in tool_names
    assert "winnr_create_email_user" not in tool_names
    assert "winnr_delete_email_user" not in tool_names


def test_inbox_write_tools_hidden_read_only(read_only_config):
    """Inbox write tools hidden for read-only tokens."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_inbox_tools(mcp, client, read_only_config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "winnr_list_inbox" in tool_names
    assert "winnr_get_message_body" in tool_names
    assert "winnr_send_email" not in tool_names
    assert "winnr_refresh_inbox" not in tool_names


def test_warming_write_tools_hidden_read_only(read_only_config):
    """Warming write tools hidden for read-only tokens."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_warming_tools(mcp, client, read_only_config)
    tool_names = [t.name for t in mcp._tool_manager.list_tools()]
    assert "winnr_list_warming" in tool_names
    assert "winnr_get_warming_overview" in tool_names
    assert "winnr_enable_warming" not in tool_names
    assert "winnr_pause_warming" not in tool_names


# ── Tool count tests ────────────────────────────────────────────────────

def test_all_tools_registered(config):
    """All 38 tools are registered with full permissions."""
    mcp, client = make_mcp_and_client(config)
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    tools = mcp._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert len(tool_names) == 38, f"Expected 38 tools, got {len(tool_names)}: {sorted(tool_names)}"


def test_read_only_tool_count(read_only_config):
    """Read-only tokens get fewer tools."""
    mcp, client = make_mcp_and_client(read_only_config)
    register_account_tools(mcp, client, read_only_config)
    register_domain_tools(mcp, client, read_only_config)
    register_email_user_tools(mcp, client, read_only_config)
    register_inbox_tools(mcp, client, read_only_config)
    register_warming_tools(mcp, client, read_only_config)
    register_job_tools(mcp, client, read_only_config)
    register_export_tools(mcp, client, read_only_config)
    tools = mcp._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    # Read-only: account(2) + domains(7 read) + email_users(2 read) + inbox(2 read)
    #            + warming(3 read) + jobs(2) + export(1) = 19
    assert len(tool_names) == 19, f"Expected 19 read-only tools, got {len(tool_names)}: {sorted(tool_names)}"


# ── Tool naming convention tests ────────────────────────────────────────

def test_all_tools_have_winnr_prefix(config):
    """All tools start with winnr_ prefix."""
    mcp, client = make_mcp_and_client(config)
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    tools = mcp._tool_manager.list_tools()
    for tool in tools:
        assert tool.name.startswith("winnr_"), f"Tool {tool.name} missing winnr_ prefix"


def test_all_tools_have_descriptions(config):
    """All tools have non-empty descriptions."""
    mcp, client = make_mcp_and_client(config)
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    tools = mcp._tool_manager.list_tools()
    for tool in tools:
        assert tool.description, f"Tool {tool.name} has no description"
        assert len(tool.description) > 20, f"Tool {tool.name} description too short"


# ── Functional tool tests (with mocked API) ────────────────────────────

@respx.mock
def test_winnr_get_account(config):
    """winnr_get_account returns account data."""
    respx.get("https://api.test.winnr.app/v1/account").mock(
        return_value=api_success(data={"id": "test123", "plan": "startup"})
    )
    mcp, client = make_mcp_and_client(config)
    register_account_tools(mcp, client, config)
    # Call the tool function directly
    result = mcp._tool_manager._tools["winnr_get_account"].fn()
    data = json.loads(result)
    assert data["data"]["id"] == "test123"


@respx.mock
def test_winnr_list_domains(config):
    """winnr_list_domains returns paginated domains."""
    respx.get("https://api.test.winnr.app/v1/domains").mock(
        return_value=api_success(
            data=[{"id": "dom_1", "name": "example.com"}],
            pagination={"cursor": None, "has_more": False, "total": 1},
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_list_domains"].fn(limit=25, cursor=None)
    data = json.loads(result)
    assert len(data["data"]) == 1
    assert data["data"][0]["name"] == "example.com"


@respx.mock
def test_winnr_list_domains_error(config):
    """winnr_list_domains returns clean error on API failure."""
    respx.get("https://api.test.winnr.app/v1/domains").mock(
        return_value=api_error(401, "unauthorized", "Invalid token")
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_list_domains"].fn(limit=25, cursor=None)
    assert "Authentication failed" in result


@respx.mock
def test_winnr_send_email(config):
    """winnr_send_email sends correctly."""
    route = respx.post("https://api.test.winnr.app/v1/email-users/eu_1/inbox/send").mock(
        return_value=api_success(data={"message_id": "msg_1"})
    )
    mcp, client = make_mcp_and_client(config)
    register_inbox_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_send_email"].fn(
        user_id="eu_1",
        to="recipient@example.com",
        subject="Hello",
        body="Test body",
    )
    data = json.loads(result)
    assert data["data"]["message_id"] == "msg_1"
    # Verify request body was correct
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body["to"] == "recipient@example.com"
    assert body["subject"] == "Hello"


@respx.mock
def test_winnr_create_email_user(config):
    """winnr_create_email_user sends correct payload."""
    route = respx.post("https://api.test.winnr.app/v1/email-users").mock(
        return_value=api_success(data={"id": "eu_new", "username": "jane"})
    )
    mcp, client = make_mcp_and_client(config)
    register_email_user_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_create_email_user"].fn(
        username="jane",
        domain="example.com",
        name="Jane Doe",
    )
    data = json.loads(result)
    assert data["data"]["id"] == "eu_new"
    body = json.loads(route.calls[0].request.content)
    assert body["username"] == "jane"
    assert body["domain"] == "example.com"


@respx.mock
def test_winnr_enable_warming(config):
    """winnr_enable_warming sends user_ids."""
    route = respx.post("https://api.test.winnr.app/v1/warming/enable").mock(
        return_value=api_success(data={"enabled": 2})
    )
    mcp, client = make_mcp_and_client(config)
    register_warming_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_enable_warming"].fn(user_ids=["eu_1", "eu_2"])
    data = json.loads(result)
    assert data["data"]["enabled"] == 2
    body = json.loads(route.calls[0].request.content)
    assert body["user_ids"] == ["eu_1", "eu_2"]


@respx.mock
def test_winnr_export_email_users(config):
    """winnr_export_email_users sends format."""
    route = respx.post("https://api.test.winnr.app/v1/export").mock(
        return_value=api_success(data={"download_url": "https://s3.amazonaws.com/..."})
    )
    mcp, client = make_mcp_and_client(config)
    register_export_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_export_email_users"].fn(format="smartlead")
    data = json.loads(result)
    assert "download_url" in data["data"]


# ── winnr_tag_domains ───────────────────────────────────────────────────

def test_tag_domains_registered_and_gated(config, read_only_config):
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    assert "winnr_tag_domains" in [t.name for t in mcp._tool_manager.list_tools()]

    mcp_ro, client_ro = make_mcp_and_client(read_only_config)
    register_domain_tools(mcp_ro, client_ro, read_only_config)
    assert "winnr_tag_domains" not in [t.name for t in mcp_ro._tool_manager.list_tools()]


@respx.mock
def test_tag_domains_add_merges_existing(config):
    """add mode reads current tags and appends without duplicates."""
    respx.get("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=api_success(data={"id": "dom_1", "tags": ["clientA", "q3"]})
    )
    respx.patch("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=api_success(data={"id": "dom_1", "tags": ["clientA", "q3", "warm"]})
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(
        domain_ids=["dom_1"], tags=["warm", "q3"], mode="add"
    ))
    assert result["updated"] == 1 and result["failed"] == 0
    assert result["results"][0]["tags"] == ["clientA", "q3", "warm"]
    # PATCH body carried the merged list
    patch_call = [c for c in respx.calls if c.request.method == "PATCH"][0]
    assert json.loads(patch_call.request.content) == {"tags": ["clientA", "q3", "warm"]}


@respx.mock
def test_tag_domains_remove(config):
    respx.get("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=api_success(data={"id": "dom_1", "tags": ["clientA", "q3"]})
    )
    respx.patch("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=api_success(data={"id": "dom_1", "tags": ["clientA"]})
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(
        domain_ids=["dom_1"], tags=["q3"], mode="remove"
    ))
    assert result["results"][0]["tags"] == ["clientA"]


@respx.mock
def test_tag_domains_set_skips_read(config):
    """set mode PATCHes directly without a GET."""
    respx.patch("https://api.test.winnr.app/v1/domains/dom_1").mock(
        return_value=api_success(data={"id": "dom_1", "tags": ["fresh"]})
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(
        domain_ids=["dom_1"], tags=["fresh"], mode="set"
    ))
    assert result["updated"] == 1
    assert all(c.request.method == "PATCH" for c in respx.calls)


@respx.mock
def test_tag_domains_partial_failure(config):
    respx.get("https://api.test.winnr.app/v1/domains/dom_ok").mock(
        return_value=api_success(data={"id": "dom_ok", "tags": []})
    )
    respx.patch("https://api.test.winnr.app/v1/domains/dom_ok").mock(
        return_value=api_success(data={"id": "dom_ok", "tags": ["x"]})
    )
    respx.get("https://api.test.winnr.app/v1/domains/dom_missing").mock(
        return_value=api_error(404, "not_found", "Domain not found")
    )
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(
        domain_ids=["dom_ok", "dom_missing"], tags=["x"], mode="add"
    ))
    assert result["updated"] == 1 and result["failed"] == 1


def test_tag_domains_validates_mode(config):
    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    result = json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(
        domain_ids=["dom_1"], tags=["x"], mode="bogus"
    ))
    assert "error" in result
