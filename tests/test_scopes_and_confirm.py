"""Scopes, tools/list filtering, two-step purchase confirmation, wait-for-job."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
import respx

from tests.conftest import scoped
from winnr_mcp import scopes as sc
from winnr_mcp.client import ClientProxy, WinnrClient
from winnr_mcp.confirm import Confirmer
from winnr_mcp.server import build_mcp, visible_tools
from winnr_mcp.tools._common import TOOL_SCOPES

API = "https://api.test.winnr.app"
TOTAL_TOOLS = 55
READ_TOOLS = 26  # 25 + winnr_wait_for_job
WRITE_TOOLS = 51  # everything but the 4 money tools
MONEY_TOOLS = {"winnr_purchase_domains", "winnr_purchase_prewarmed", "winnr_purchase_prewarmed_batch", "winnr_enable_warming"}


def ok(data=None) -> httpx.Response:
    return httpx.Response(200, json={"data": data, "meta": {"request_id": "r"}})


def build(config):
    client = WinnrClient(config, account_id="acct_1")
    return build_mcp(client, config), client


def call(mcp, name, **kwargs):
    fn = mcp._tool_manager._tools[name].fn
    out = fn(**kwargs)
    return json.loads(out)


# ── scopes ──────────────────────────────────────────────────────────────

def test_normalize_implication_chain():
    assert sc.normalize(["purchase"]) == {"read", "write", "purchase"}
    assert sc.normalize(["write"]) == {"read", "write"}
    assert sc.normalize(["READ "]) == {"read"}
    assert sc.normalize(["bogus"]) == frozenset()


def test_scopes_from_permissions():
    assert sc.scopes_from_permissions(["read"]) == {"read"}
    assert sc.scopes_from_permissions(["read", "write"]) == {"read", "write", "purchase"}
    assert sc.scopes_from_permissions(["read", "write"], allow_purchases=False) == {"read", "write"}
    assert sc.permissions_from_scopes(["purchase"]) == ["read", "write"]
    assert sc.permissions_from_scopes(["read"]) == ["read"]


def test_every_tool_declares_a_scope(config):
    mcp, _ = build(config)
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert names == set(TOOL_SCOPES) & names and len(names) == TOTAL_TOOLS
    assert {n for n, s in TOOL_SCOPES.items() if s == sc.PURCHASE} == MONEY_TOOLS


def test_visible_tools_per_scope(config):
    mcp, _ = build(config)
    with scoped("read"):
        assert len(visible_tools(mcp)) == READ_TOOLS
        assert "winnr_export_email_users" not in visible_tools(mcp)
        assert "winnr_get_webhook_secret" not in visible_tools(mcp)
    with scoped("read", "write"):
        assert len(visible_tools(mcp)) == WRITE_TOOLS
        assert not MONEY_TOOLS & set(visible_tools(mcp))
    with scoped("purchase"):
        assert len(visible_tools(mcp)) == TOTAL_TOOLS


def test_read_only_hint_matches_scope(config):
    """readOnlyHint tools are read-scoped and vice versa (except the secret reader)."""
    mcp, _ = build(config)
    for t in mcp._tool_manager.list_tools():
        if t.name == "winnr_get_webhook_secret":
            assert TOOL_SCOPES[t.name] == sc.WRITE
            continue
        assert (TOOL_SCOPES[t.name] == sc.READ) == bool(t.annotations.readOnlyHint), t.name


def test_list_tools_handler_is_filtered(config):
    """The MCP tools/list handler (what clients see) honours the session's scopes."""
    from mcp.types import ListToolsRequest
    mcp, _ = build(config)
    handler = mcp._mcp_server.request_handlers[ListToolsRequest]
    with scoped("read"):
        result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    names = {t.name for t in result.root.tools}
    assert len(names) == READ_TOOLS and "winnr_delete_domain" not in names


@respx.mock
def test_call_without_scope_is_refused_before_any_request(config):
    route = respx.delete(f"{API}/v1/domains/d1").mock(return_value=ok({}))
    mcp, _ = build(config)
    with scoped("read"):
        out = call(mcp, "winnr_delete_domain", domain_id="d1")
    assert out["error"]["code"] == "insufficient_scope" and out["error"]["needed_scope"] == "write"
    assert not route.called
    with scoped("read", "write"):
        out = call(mcp, "winnr_enable_warming", user_ids=["u1"])
    assert out["error"]["needed_scope"] == "purchase"


def test_client_proxy_forwards(config):
    a = WinnrClient(config, account_id="A")
    proxy = ClientProxy(lambda: a)
    assert proxy.account_id == "A"


# ── Confirmer ───────────────────────────────────────────────────────────

def test_confirmer_roundtrip_and_tamper():
    c = Confirmer(b"secret", ttl_seconds=60)
    tok, exp = c.issue("k", "acct", {"total": 12.0, "domains": ["a.com"]})
    assert exp > time.time()
    assert c.verify(tok, "k", "acct", {"domains": ["a.com"], "total": 12.0}) is None  # key order irrelevant
    assert c.verify(tok, "k", "acct", {"domains": ["a.com"], "total": 13.0}) == "quote_changed"
    assert c.verify(tok, "other", "acct", {"domains": ["a.com"], "total": 12.0}) == "quote_changed"
    assert c.verify(tok, "k", "acct2", {"domains": ["a.com"], "total": 12.0}) == "quote_changed"
    assert c.verify("garbage", "k", "acct", {}) == "malformed"
    assert Confirmer(b"other").verify(tok, "k", "acct", {"domains": ["a.com"], "total": 12.0}) == "quote_changed"


def test_confirmer_expiry():
    c = Confirmer(b"s", ttl_seconds=-1)
    tok, _ = c.issue("k", "a", {})
    assert c.verify(tok, "k", "a", {}) == "expired"


# ── winnr_purchase_domains two-step ─────────────────────────────────────

def _search(results):
    return respx.post(f"{API}/v1/domains/search-bulk").mock(return_value=ok({"results": results}))


@respx.mock
def test_purchase_domains_quote_then_confirm(config):
    search = _search([{"domain": "acmehq.com", "available": True, "price": 12}])
    purchase = respx.post(f"{API}/v1/domains/purchase").mock(
        return_value=httpx.Response(202, json={"data": {"job_id": "j9"}})
    )
    mcp, _ = build(config)
    order = [{"domain": "AcmeHQ.com", "users": [{"username": "sam", "name": "Sam"}], "bogus": 1}]
    q = call(mcp, "winnr_purchase_domains", domains=order)
    assert q["quote"]["total"] == 12.0 and q["quote"]["prices"] == {"acmehq.com": 12.0}
    assert "confirmation_token" in q and not purchase.called
    out = call(mcp, "winnr_purchase_domains", domains=order, confirmation_token=q["confirmation_token"])
    assert out["data"]["job_id"] == "j9"
    assert search.call_count == 2  # re-quoted at confirm time
    body = json.loads(purchase.calls[0].request.content)
    assert body["async"] is True
    assert body["domains"][0] == {"domain": "acmehq.com", "users": [{"username": "sam", "name": "Sam"}], "price": 12.0}


@respx.mock
def test_purchase_domains_refused_if_price_moved_between_quote_and_confirm(config):
    search = _search([{"domain": "acmehq.com", "available": True, "price": 12}])
    purchase = respx.post(f"{API}/v1/domains/purchase")
    mcp, _ = build(config)
    q = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}])
    search.mock(return_value=ok({"results": [{"domain": "acmehq.com", "available": True, "price": 25}]}))
    out = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}], confirmation_token=q["confirmation_token"])
    assert out["error"]["code"] == "confirmation_invalid" and "quote_changed" in out["error"]["message"]
    assert out["error"]["quote"]["total"] == 25.0
    assert not purchase.called


@respx.mock
def test_purchase_domains_refused_when_taken_or_unpriced(config):
    _search([{"domain": "acmehq.com", "available": False, "error": "taken"}])
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}])
    assert out["error"]["code"] == "unavailable"


@respx.mock
def test_purchase_domains_fails_closed_when_search_fails(config):
    respx.post(f"{API}/v1/domains/search-bulk").mock(return_value=httpx.Response(500, json={"error": {"code": "x", "message": "boom"}}))
    purchase = respx.post(f"{API}/v1/domains/purchase")
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}])
    assert out["error"]["code"] == "price_check_failed" and not purchase.called


def test_purchase_domains_rejects_bad_tokens(config):
    with respx.mock:
        _search([{"domain": "acmehq.com", "available": True, "price": 12}])
        mcp, _ = build(config)
        out = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}], confirmation_token="nope")
        assert out["error"]["code"] == "confirmation_invalid" and "malformed" in out["error"]["message"]


def test_purchase_domains_token_is_account_bound(config):
    with respx.mock:
        _search([{"domain": "acmehq.com", "available": True, "price": 12}])
        mcp, client = build(config)
        q = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}])
        client.account_id = "someone_else"
        out = call(mcp, "winnr_purchase_domains", domains=[{"domain": "acmehq.com"}], confirmation_token=q["confirmation_token"])
        assert out["error"]["code"] == "confirmation_invalid"


# ── winnr_enable_warming two-step ───────────────────────────────────────

@respx.mock
def test_enable_warming_quote_then_confirm(config):
    route = respx.post(f"{API}/v1/warming/enable").mock(return_value=ok({"enabled": 2}))
    mcp, _ = build(config)
    q = call(mcp, "winnr_enable_warming", user_ids=["eu_2", "eu_1"], emails_per_day=15, rampup_speed="Fast")
    assert q["quote"]["monthly_total"] == 1.2 and q["quote"]["mailboxes"] == ["eu_1", "eu_2"]
    assert not route.called
    out = call(mcp, "winnr_enable_warming", user_ids=["eu_1", "eu_2"], emails_per_day=15, rampup_speed="fast",
               confirmation_token=q["confirmation_token"])
    assert out["data"]["enabled"] == 2
    assert json.loads(route.calls[0].request.content) == {
        "user_ids": ["eu_1", "eu_2"], "settings": {"emails_per_day": 15, "rampup_speed": "fast"}}
    # different mailbox list → token no longer matches
    out = call(mcp, "winnr_enable_warming", user_ids=["eu_1"], emails_per_day=15, rampup_speed="fast",
               confirmation_token=q["confirmation_token"])
    assert out["error"]["code"] == "confirmation_invalid"


# ── pre-warmed two-step ─────────────────────────────────────────────────

@respx.mock
def test_purchase_prewarmed_quote_then_confirm(config):
    respx.get(f"{API}/v1/prewarmed/x.com").mock(return_value=ok({"domain": "x.com", "address_count": 5}))
    route = respx.post(f"{API}/v1/prewarmed/purchase").mock(return_value=ok({"status": "provisioned"}))
    mcp, _ = build(config)
    q = call(mcp, "winnr_purchase_prewarmed", domain="x.com", custom_usernames=["a", "b", "c"])
    assert q["quote"]["monthly_total"] == 9.0 and not route.called
    out = call(mcp, "winnr_purchase_prewarmed", domain="x.com", custom_usernames=["a", "b", "c"],
               confirmation_token=q["confirmation_token"])
    assert out["data"]["status"] == "provisioned"
    assert json.loads(route.calls[0].request.content) == {"domain": "x.com", "custom_usernames": ["a", "b", "c"], "address_count": 3}


@respx.mock
def test_purchase_prewarmed_refuses_more_than_available(config):
    respx.get(f"{API}/v1/prewarmed/x.com").mock(return_value=ok({"domain": "x.com", "address_count": 4}))
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_prewarmed", domain="x.com", address_count=10)
    assert out["error"]["code"] == "unavailable"


@respx.mock
def test_purchase_prewarmed_sold_out_is_unavailable(config):
    respx.get(f"{API}/v1/prewarmed/x.com").mock(return_value=httpx.Response(404, json={"error": {"code": "not_found", "message": "gone"}}))
    mcp, _ = build(config)
    out = call(mcp, "winnr_purchase_prewarmed", domain="x.com", address_count=3)
    assert out["error"]["code"] == "unavailable"


@respx.mock
def test_purchase_prewarmed_batch_quote_then_confirm(config):
    respx.get(url__regex=r".*/v1/prewarmed/[ab](%2E|\.)com$").mock(return_value=ok({"address_count": 10}))
    route = respx.post(f"{API}/v1/prewarmed/purchase-batch").mock(return_value=ok({"ok": True}))
    mcp, _ = build(config)
    items = [{"domain": "a.com", "address_count": 3}, {"domain": "b.com", "address_count": "5"}]
    q = call(mcp, "winnr_purchase_prewarmed_batch", items=items)
    assert q["quote"]["monthly_total"] == 24.0
    out = call(mcp, "winnr_purchase_prewarmed_batch", items=items, confirmation_token=q["confirmation_token"])
    assert out["data"]["ok"] is True
    assert json.loads(route.calls[0].request.content) == {"items": [
        {"domain": "a.com", "address_count": 3}, {"domain": "b.com", "address_count": 5}]}


# ── winnr_wait_for_job ──────────────────────────────────────────────────

class FakeCtx:
    def __init__(self):
        self.progress = []

    async def report_progress(self, progress, total=None, message=None):
        self.progress.append((progress, total, message))


@respx.mock
def test_wait_for_job_polls_until_terminal(config, monkeypatch):
    monkeypatch.setattr("winnr_mcp.tools.jobs.POLL_SECONDS", 0.0)
    route = respx.get(f"{API}/v1/jobs/j1")
    route.side_effect = [
        ok({"job_id": "j1", "status": "queued"}),
        ok({"job_id": "j1", "status": "in_progress", "progress": {"completed": 1, "total": 4}}),
        ok({"job_id": "j1", "status": "completed", "result": {"x": 1}}),
    ]
    mcp, _ = build(config)
    ctx = FakeCtx()
    out = json.loads(asyncio.run(mcp._tool_manager._tools["winnr_wait_for_job"].fn(job_id="j1", ctx=ctx, timeout_seconds=30)))
    assert out["data"]["status"] == "completed" and out["polls"] == 3
    assert [p[0] for p in ctx.progress] == [5.0, 25.0, 100.0]


@respx.mock
def test_wait_for_job_times_out_with_snapshot(config, monkeypatch):
    monkeypatch.setattr("winnr_mcp.tools.jobs.POLL_SECONDS", 0.0)
    respx.get(f"{API}/v1/jobs/j1").mock(return_value=ok({"job_id": "j1", "status": "in_progress"}))
    mcp, _ = build(config)
    out = json.loads(asyncio.run(mcp._tool_manager._tools["winnr_wait_for_job"].fn(job_id="j1", ctx=FakeCtx(), timeout_seconds=1)))
    assert out["timed_out"] is True and out["data"]["status"] == "in_progress"


def test_wait_for_job_is_capped_by_config(config):
    config.max_wait_seconds = 25
    mcp, _ = build(config)
    assert "1-25" in mcp._tool_manager._tools["winnr_wait_for_job"].description


# ── resources & prompts ─────────────────────────────────────────────────

@respx.mock
def test_resources_and_prompts_registered(config):
    respx.get(f"{API}/v1/account").mock(return_value=ok({"id": "acct_1"}))
    mcp, _ = build(config)
    uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
    assert {"winnr://account", "winnr://usage", "winnr://domains", "winnr://warming/overview"} <= uris
    templates = {t.uri_template for t in mcp._resource_manager.list_templates()}
    assert "winnr://domains/{domain_id}/dns-records" in templates
    names = {p.name for p in mcp._prompt_manager.list_prompts()}
    assert {"winnr_setup_infrastructure", "winnr_health_check", "winnr_reply_triage", "winnr_connect_own_domain", "winnr_scale_up"} <= names
    content = asyncio.run(mcp.read_resource("winnr://account"))
    assert json.loads(list(content)[0].content)["data"]["id"] == "acct_1"
    with scoped():  # no scopes at all
        content = asyncio.run(mcp.read_resource("winnr://account"))
        assert json.loads(list(content)[0].content)["error"]["code"] == "insufficient_scope"
    prompt = asyncio.run(mcp.get_prompt("winnr_setup_infrastructure", {"company": "Acme", "mailboxes": "10"}))
    assert "acmehq.com" in prompt.messages[0].content.text


@pytest.mark.parametrize("name", ["winnr_health_check", "winnr_reply_triage", "winnr_connect_own_domain", "winnr_scale_up"])
def test_prompts_render(config, name):
    mcp, _ = build(config)
    args = {"domain": "x.com"} if name == "winnr_connect_own_domain" else ({"mailboxes": "5"} if name == "winnr_scale_up" else {})
    prompt = asyncio.run(mcp.get_prompt(name, args))
    assert "winnr_" in prompt.messages[0].content.text
