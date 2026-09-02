"""Regression tests for request shapes that used to diverge from the API.

Each test pins a tool to the exact HTTP call the Winnr API expects, so a
tool can no longer "work" in the MCP layer while 4xx-ing against production.
"""

from __future__ import annotations

import json

import httpx
import respx
import pytest
from mcp.server.fastmcp import FastMCP

from winnr_mcp.client import WinnrClient
from winnr_mcp.config import WinnrConfig
from winnr_mcp.server import _discover_permissions
from winnr_mcp.tools.account import register_account_tools
from winnr_mcp.tools.domains import register_domain_tools
from winnr_mcp.tools.email_users import register_email_user_tools
from winnr_mcp.tools.export import register_export_tools
from winnr_mcp.tools.inbox import register_inbox_tools
from winnr_mcp.tools.jobs import register_job_tools
from winnr_mcp.tools.prewarmed import register_prewarmed_tools
from winnr_mcp.tools.warming import register_warming_tools
from winnr_mcp.tools.webhooks import register_webhook_tools

API = "https://api.test.winnr.app"


def ok(data=None) -> httpx.Response:
    return httpx.Response(200, json={"data": data, "meta": {"request_id": "r"}})


def build(config: WinnrConfig) -> tuple[FastMCP, WinnrClient]:
    mcp = FastMCP("test")
    client = WinnrClient(config)
    for reg in (
        register_account_tools, register_domain_tools, register_email_user_tools,
        register_inbox_tools, register_warming_tools, register_prewarmed_tools,
        register_job_tools, register_export_tools, register_webhook_tools,
    ):
        reg(mcp, client, config)
    return mcp, client


def call(mcp: FastMCP, name: str, **kwargs) -> dict:
    return json.loads(mcp._tool_manager._tools[name].fn(**kwargs))


# ── Every tool carries annotations ─────────────────────────────────────

def test_every_tool_has_annotations(config):
    mcp, _ = build(config)
    for tool in mcp._tool_manager.list_tools():
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is not None, f"{tool.name} missing readOnlyHint"


def test_removed_suggest_tool_is_gone(config):  # noqa: D103
    mcp, _ = build(config)
    assert "winnr_suggest_domains" not in [t.name for t in mcp._tool_manager.list_tools()]


# ── Inbox ───────────────────────────────────────────────────────────────

@respx.mock
def test_get_message_body_sends_mailbox(config):
    route = respx.get(f"{API}/v1/inbox/123/body").mock(
        return_value=ok({"uid": "123", "mailbox": "a@example.com", "body": "hello"})
    )
    mcp, _ = build(config)
    out = call(mcp, "winnr_get_message_body", uid="123", mailbox="A@Example.com")
    assert out["data"]["body"] == "hello"
    assert route.calls[0].request.url.params["mailbox"] == "a@example.com"


def test_get_message_body_requires_mailbox(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_get_message_body", uid="123", mailbox="")
    assert out["error"]["code"] == "invalid_arguments"


@respx.mock
def test_get_message_body_truncates_body_not_json(config):
    big = "x" * 20_000
    respx.get(f"{API}/v1/inbox/1/body").mock(return_value=ok({"uid": "1", "mailbox": "a@b.co", "body": big}))
    mcp, _ = build(config)
    out = call(mcp, "winnr_get_message_body", uid="1", mailbox="a@b.co")  # valid JSON
    assert out["data"]["truncated"] is True
    assert len(out["data"]["body"]) == 10_000
    assert out["data"]["original_length"] == 20_000


@respx.mock
def test_delete_message_uses_account_inbox_route(config):
    route = respx.delete(f"{API}/v1/inbox/55").mock(return_value=ok({"message": "deleted"}))
    mcp, _ = build(config)
    call(mcp, "winnr_delete_message", uid="55", mailbox="a@example.com")
    assert route.calls[0].request.url.params["mailbox"] == "a@example.com"


@respx.mock
def test_list_inbox_excludes_warmup_by_default(config):
    route = respx.get(f"{API}/v1/inbox").mock(return_value=ok([]))
    mcp, _ = build(config)
    call(mcp, "winnr_list_inbox")
    assert route.calls[0].request.url.params["exclude_warmup"] == "true"


# ── Warming ─────────────────────────────────────────────────────────────

@respx.mock
def test_update_warming_settings_uses_api_field_names(config):
    route = respx.patch(f"{API}/v1/warming/eu_1/settings").mock(return_value=ok({"ok": True}))
    mcp, _ = build(config)
    call(mcp, "winnr_update_warming_settings", user_id="eu_1", emails_per_day=10, rampup_speed="Slow")
    body = json.loads(route.calls[0].request.content)
    assert body == {"emails_per_day": 10, "rampup_speed": "slow"}


def test_update_warming_settings_validates(config):
    mcp, _ = build(config)
    assert "error" in call(mcp, "winnr_update_warming_settings", user_id="eu_1", emails_per_day=50)
    assert "error" in call(mcp, "winnr_update_warming_settings", user_id="eu_1", rampup_speed="turbo")
    assert "error" in call(mcp, "winnr_update_warming_settings", user_id="eu_1")


@respx.mock
def test_bulk_create_sends_top_level_domain(config):
    route = respx.post(f"{API}/v1/email-users/bulk").mock(return_value=ok({"job_id": "j1"}))
    mcp, _ = build(config)
    call(
        mcp, "winnr_bulk_create_email_users", domain="Example.com",
        users=[{"username": "Sam", "name": "Sam"}, {"username": "alex"}],
    )
    body = json.loads(route.calls[0].request.content)
    assert body["domain"] == "example.com"
    assert body["users"] == [{"username": "sam", "name": "Sam"}, {"username": "alex", "name": ""}]


def test_bulk_create_rejects_missing_username(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_bulk_create_email_users", domain="example.com", users=[{"name": "x"}])
    assert out["error"]["code"] == "invalid_arguments"


# ── Domains ─────────────────────────────────────────────────────────────

def _search_bulk(results):
    return respx.post(f"{API}/v1/domains/search-bulk").mock(
        return_value=ok({"results": results, "count": len(results)})
    )


def test_purchase_domains_rejects_non_string_domain(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_domains", domains=[{"domain": 123}])
    assert out["error"]["code"] == "invalid_arguments"
    out = call(mcp, "winnr_bulk_create_email_users", domain="a.com", users=[{"username": 5}])
    assert out["error"]["code"] == "invalid_arguments"
    out = call(mcp, "winnr_bulk_create_email_users", domain="a.com", users=[{"username": "a", "password": "short"}])
    assert "8 characters" in out["error"]["message"]


def test_check_dns_provider_caps_at_20(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_check_dns_provider", domains=[f"d{i}.com" for i in range(21)])
    assert out["error"]["code"] == "invalid_arguments"


@respx.mock
def test_list_jobs_filters(config):
    route = respx.get(f"{API}/v1/jobs").mock(return_value=ok([]))
    mcp, _ = build(config)
    call(mcp, "winnr_list_jobs", status="Completed", job_type="domain_setup")
    p = route.calls[0].request.url.params
    assert p["filter[status]"] == "completed" and p["filter[type]"] == "domain_setup" and "cursor" not in p


@respx.mock
def test_list_domains_status_filter(config):
    route = respx.get(f"{API}/v1/domains").mock(return_value=ok([]))
    mcp, _ = build(config)
    call(mcp, "winnr_list_domains", status="complete")
    assert route.calls[0].request.url.params["filter[status]"] == "complete"
    assert "error" in call(mcp, "winnr_list_domains", status="weird")
    assert "error" in call(mcp, "winnr_list_domains", status="active")  # not a real domain status


@respx.mock
def test_connect_domains_manual_dns(config):
    route = respx.post(f"{API}/v1/domains/connect").mock(return_value=ok({}))
    mcp, _ = build(config)
    call(mcp, "winnr_connect_domains", domains=["https://Example.com/"], manual_dns=True)
    body = json.loads(route.calls[0].request.content)
    assert body == {"domains": ["example.com"], "manual_dns": True}
    assert "error" in call(
        mcp, "winnr_connect_domains", domains=["a.com"], manual_dns=True, cloudflare_api_token="t"
    )


@respx.mock
def test_check_dns_provider(config):
    route = respx.post(f"{API}/v1/domains/check-provider").mock(return_value=ok([]))
    mcp, _ = build(config)
    call(mcp, "winnr_check_dns_provider", domains=["Example.com"])
    assert json.loads(route.calls[0].request.content) == {"domains": ["example.com"]}


# ── Pre-warmed ──────────────────────────────────────────────────────────

@respx.mock
def test_blocklist_single_list_goes_on_query_string(config):
    route = respx.post(f"{API}/v1/prewarmed/example.com/blocklist-check").mock(return_value=ok({}))
    mcp, _ = build(config)
    call(mcp, "winnr_check_prewarmed_blocklist", domain="example.com", blocklist="surbl")
    assert route.calls[0].request.url.params["list"] == "surbl"


def test_purchase_prewarmed_batch_rejects_duplicates(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_prewarmed_batch", items=[{"domain": "a.com"}, {"domain": "A.com"}])
    assert "Duplicate" in out["error"]["message"]


# ── Export ──────────────────────────────────────────────────────────────

def test_export_rejects_unknown_format_locally(config):
    mcp, _ = build(config)
    out = call(mcp, "winnr_export_email_users", format="lemlist", all_domains=True)
    assert "lemlist" in out["error"]["message"]
    out = call(mcp, "winnr_export_email_users", format="smartlead")
    assert out["error"]["code"] == "invalid_arguments"


# ── Errors ──────────────────────────────────────────────────────────────

@respx.mock
def test_api_error_is_json_with_code(config):
    respx.get(f"{API}/v1/account").mock(
        return_value=httpx.Response(
            402, json={"error": {"code": "payment_method_required", "message": "No card"}, "meta": {}}
        )
    )
    mcp, _ = build(config)
    out = call(mcp, "winnr_get_account")
    assert out["error"]["status_code"] == 402
    assert out["error"]["code"] == "payment_method_required"
    assert "payment method" in out["error"]["message"].lower()


@respx.mock
def test_read_only_403_explains_token_scope(config):
    respx.delete(f"{API}/v1/domains/d1").mock(
        return_value=httpx.Response(
            403, json={"error": {"code": "insufficient_permissions", "message": "ro"}, "meta": {}}
        )
    )
    mcp, _ = build(config)
    out = call(mcp, "winnr_delete_domain", domain_id="d1")
    assert "read-only" in out["error"]["message"]


@respx.mock
def test_get_retries_once_on_429(config):
    route = respx.get(f"{API}/v1/account")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "rate_limited", "message": "slow"}}),
        ok({"id": "acct"}),
    ]
    mcp, _ = build(config)
    out = call(mcp, "winnr_get_account")
    assert out["data"]["id"] == "acct"
    assert route.call_count == 2


# ── Startup permission discovery ────────────────────────────────────────

@respx.mock
def test_discover_permissions_narrows_read_only_token(config):
    respx.get(f"{API}/v1/account").mock(
        return_value=ok({"id": "acct", "name": "Acme", "plan": "startup",
                         "api_token": {"name": "MCP", "permissions": ["read"]}})
    )
    client = WinnrClient(config)
    _discover_permissions(client, config)
    assert config.permissions == ["read"]
    assert config.account_name == "Acme"
    assert config.token_name == "MCP"


@respx.mock
def test_discover_permissions_keeps_write_token(config):
    respx.get(f"{API}/v1/account").mock(
        return_value=ok({"id": "acct", "api_token": {"permissions": ["read", "write"]}})
    )
    _discover_permissions(WinnrClient(config), config)
    assert config.permissions == ["read", "write"]


@respx.mock
def test_discover_permissions_exits_on_401(config):
    respx.get(f"{API}/v1/account").mock(
        return_value=httpx.Response(401, json={"error": {"code": "unauthorized", "message": "bad"}})
    )
    with pytest.raises(SystemExit):
        _discover_permissions(WinnrClient(config), config)


@respx.mock
def test_discover_permissions_tolerates_outage(config):
    respx.get(f"{API}/v1/account").mock(side_effect=httpx.ConnectError("down"))
    _discover_permissions(WinnrClient(config), config)  # must not raise
    assert config.permissions == ["read", "write"]


# ── Path parameters are always URL-encoded ──────────────────────────────

def test_no_raw_fstring_path_params():
    """Every {param} inside an f"/v1/..." path must go through seg()."""
    import re
    from pathlib import Path
    bad = []
    for f in Path("src/winnr_mcp/tools").glob("*.py"):
        for m in re.finditer(r'f"/v1/[^"]*"', f.read_text()):
            for param in re.findall(r"\{([^}]+)\}", m.group(0)):
                if not param.startswith("seg("):
                    bad.append(f"{f.name}: {m.group(0)}")
    assert not bad, bad


@respx.mock
def test_path_param_with_slash_cannot_change_route(config):
    route = respx.get(url__regex=r".*/v1/inbox/.*").mock(return_value=ok({"uid": "x", "mailbox": "a@b.co", "body": ""}))
    mcp, _ = build(config)
    call(mcp, "winnr_get_message_body", uid="1/../refresh", mailbox="a@b.co")
    sent = str(route.calls[0].request.url)
    assert "/v1/inbox/1%2F%2E%2E%2Frefresh/body" in sent, sent
