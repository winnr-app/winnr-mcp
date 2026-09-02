"""Tests for MCP tool registration and behavior."""

from __future__ import annotations

import json

import httpx
import respx

from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.tools.account import register_account_tools
from winnr_mcp.tools.domains import register_domain_tools
from winnr_mcp.tools.email_users import register_email_user_tools
from winnr_mcp.tools.inbox import register_inbox_tools
from winnr_mcp.tools.warming import register_warming_tools
from winnr_mcp.tools.jobs import register_job_tools
from winnr_mcp.tools.prewarmed import register_prewarmed_tools
from winnr_mcp.tools.export import register_export_tools
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


# ── Permission gating tests ────────────────────────────────────────────


def test_all_tools_have_winnr_prefix(config):
    """All tools start with winnr_ prefix."""
    mcp, client = make_mcp_and_client(config)
    register_account_tools(mcp, client, config)
    register_domain_tools(mcp, client, config)
    register_email_user_tools(mcp, client, config)
    register_inbox_tools(mcp, client, config)
    register_warming_tools(mcp, client, config)
    register_prewarmed_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    register_webhook_tools(mcp, client, config)
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
    register_prewarmed_tools(mcp, client, config)
    register_job_tools(mcp, client, config)
    register_export_tools(mcp, client, config)
    register_webhook_tools(mcp, client, config)
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
def test_winnr_export_email_users(config):
    """winnr_export_email_users sends format."""
    route = respx.post("https://api.test.winnr.app/v1/export").mock(
        return_value=api_success(data={"download_url": "https://s3.amazonaws.com/..."})
    )
    mcp, client = make_mcp_and_client(config)
    register_export_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_export_email_users"].fn(format="smartlead", all_domains=True)
    data = json.loads(result)
    assert "download_url" in data["data"]
    body = json.loads(route.calls[0].request.content)
    assert body == {"format": "smartlead", "getAllDomains": True}


# ── winnr_tag_domains ───────────────────────────────────────────────────

def test_tag_domains_registered_and_gated(config):
    from tests.conftest import scoped
    from winnr_mcp.server import install_scope_filter, visible_tools

    mcp, client = make_mcp_and_client(config)
    register_domain_tools(mcp, client, config)
    install_scope_filter(mcp)
    assert "winnr_tag_domains" in visible_tools(mcp)
    with scoped("read"):
        assert "winnr_tag_domains" not in visible_tools(mcp)
        assert json.loads(mcp._tool_manager._tools["winnr_tag_domains"].fn(domain_ids=["d"], tags=["x"]))["error"]["code"] == "insufficient_scope"


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


# ── Pre-warmed marketplace tests ────────────────────────────────────────


@respx.mock
def test_winnr_browse_prewarmed(config):
    """winnr_browse_prewarmed returns the listing payload."""
    route = respx.get("https://api.test.winnr.app/v1/prewarmed/browse").mock(
        return_value=api_success(
            data={
                "domains": [{"domain": "example.com", "avg_health_score": 96, "warming_days": 41}],
                "pagination": {"page": 1, "per_page": 50, "total": 1},
            }
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_browse_prewarmed"].fn()
    data = json.loads(result)
    assert data["data"]["domains"][0]["domain"] == "example.com"
    # Defaults are sent as query params
    assert route.calls[0].request.url.params["sort_by"] == "health"
    assert route.calls[0].request.url.params["per_page"] == "50"


def test_winnr_browse_prewarmed_rejects_bad_sort(config):
    """An invalid sort_by is rejected locally without an API call."""
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_browse_prewarmed"].fn(sort_by="price")
    assert "sort_by must be one of" in result


@respx.mock
def test_winnr_browse_prewarmed_clamps_per_page(config):
    """per_page is clamped to the API maximum of 1000."""
    route = respx.get("https://api.test.winnr.app/v1/prewarmed/browse").mock(
        return_value=api_success(data={"domains": [], "pagination": {}})
    )
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    mcp._tool_manager._tools["winnr_browse_prewarmed"].fn(per_page=5000)
    assert route.calls[0].request.url.params["per_page"] == "1000"


@respx.mock
def test_winnr_check_prewarmed_blocklist_single_list(config):
    """A single blocklist is passed through as the `list` query param."""
    route = respx.post(
        "https://api.test.winnr.app/v1/prewarmed/example.com/blocklist-check"
    ).mock(return_value=api_success(data={"domain": "example.com", "status": "clean"}))
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_check_prewarmed_blocklist"].fn(
        domain="example.com", blocklist="surbl"
    )
    assert json.loads(result)["data"]["status"] == "clean"
    assert route.calls[0].request.url.params["list"] == "surbl"


def test_winnr_check_prewarmed_blocklist_rejects_unknown_list(config):
    """An unknown blocklist name is rejected locally."""
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_check_prewarmed_blocklist"].fn(
        domain="example.com", blocklist="not_a_list"
    )
    assert "Unknown blocklist" in result


def test_winnr_purchase_prewarmed_batch_rejects_oversize_order(config):
    """More than 25 items is rejected locally, before charging anything."""
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    items = [{"domain": f"example{i}.com", "address_count": 3} for i in range(26)]
    result = mcp._tool_manager._tools["winnr_purchase_prewarmed_batch"].fn(items=items)
    assert "at most 25 domains" in result


def test_winnr_purchase_prewarmed_batch_rejects_empty_order(config):
    """An empty order is rejected locally."""
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_purchase_prewarmed_batch"].fn(items=[])
    assert "at least one order item" in result


@respx.mock
def test_winnr_cancel_prewarmed_api_error_passthrough(config):
    """Cancelling inside the 90-day term surfaces the API's explanation."""
    respx.post("https://api.test.winnr.app/v1/prewarmed/cancel").mock(
        return_value=api_error(
            400, "bad_request", "This domain is under a 3-month minimum term until 2026-10-25."
        )
    )
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_cancel_prewarmed"].fn(domain="example.com")
    assert "minimum term" in result


@respx.mock
def test_winnr_list_my_prewarmed(config):
    """winnr_list_my_prewarmed returns owned domains."""
    respx.get("https://api.test.winnr.app/v1/prewarmed/my-domains").mock(
        return_value=api_success(data={"domains": [{"name": "example.com"}]})
    )
    mcp, client = make_mcp_and_client(config)
    register_prewarmed_tools(mcp, client, config)
    result = mcp._tool_manager._tools["winnr_list_my_prewarmed"].fn()
    assert json.loads(result)["data"]["domains"][0]["name"] == "example.com"
