"""End-to-end OAuth 2.1 + Streamable HTTP flow against the remote app (in-memory store, fake Firebase)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from winnr_mcp.remote.app import create_app
from winnr_mcp.remote.firebase_auth import Identity
from winnr_mcp.remote.store import MemoryStore

ISSUER = "https://mcp.test"
DASH = "https://app.test"
API = "https://api.test.winnr.app"


class FakeFirebase:
    def __init__(self):
        self.tokens: dict[tuple[str, str], dict] = {}
        self.identities = {"good": Identity(uid="uid1", email="a@b.co", account_id="acct_1")}

    def identify(self, id_token):
        if id_token not in self.identities:
            raise ValueError("Your sign-in could not be verified.")
        return self.identities[id_token]

    def mint_api_token(self, account_id, uid, name, permissions, client_id):
        tid = f"tok{len(self.tokens) + 1}"
        raw = f"wnr_{account_id}_{secrets.token_hex(12)}"
        self.tokens[(account_id, tid)] = {"name": name, "permissions": permissions, "active": True, "raw": raw, "uid": uid, "client_id": client_id}
        return tid, raw

    def api_token_active(self, account_id, token_id):
        return self.tokens.get((account_id, token_id), {}).get("active", False)

    def deactivate_api_token(self, account_id, token_id):
        self.tokens[(account_id, token_id)]["active"] = False


@pytest.fixture
def env():
    fb = FakeFirebase()
    app = create_app(store=MemoryStore(), firebase=fb, issuer=ISSUER, dashboard_url=DASH, api_url=API, confirm_secret=b"s")
    with TestClient(app, base_url=ISSUER) as client:
        yield client, fb


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def register(client, name="Claude"):
    r = client.post("/register", json={"client_name": name, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                                       "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]})
    assert r.status_code == 201, r.text
    return r.json()


def grant(client, fb, scopes=None, approve_scopes=None, decision="approve", id_token="good"):
    """Run authorize → consent → code → token. Returns the token response (dict) or the redirect error."""
    reg = register(client)
    verifier, challenge = pkce()
    params = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz",
              "resource": f"{ISSUER}/mcp"}
    if scopes:
        params["scope"] = " ".join(scopes)
    r = client.get("/authorize", params=params, follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    location = r.headers["location"]
    assert location.startswith(f"{DASH}/mcp/authorize?request=")
    request_id = parse_qs(urlparse(location).query)["request"][0]

    info = client.get(f"/oauth/request/{request_id}")
    assert info.status_code == 200 and info.json()["client_name"] == "Claude"

    body = {"request_id": request_id, "id_token": id_token, "decision": decision}
    if approve_scopes is not None:
        body["scopes"] = approve_scopes
    r = client.post("/oauth/approve", json=body)
    if r.status_code != 200:
        return {"approve_error": r.json(), "status": r.status_code}
    redirect_to = r.json()["redirect_to"]
    q = parse_qs(urlparse(redirect_to).query)
    assert q["state"] == ["xyz"]
    if "error" in q:
        return {"error": q["error"][0]}
    r = client.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": reg["client_id"],
                                    "redirect_uri": reg["redirect_uris"][0], "code_verifier": verifier, "resource": f"{ISSUER}/mcp"})
    assert r.status_code == 200, r.text
    out = r.json()
    out["_client_id"] = reg["client_id"]
    out["_code"] = q["code"][0]
    out["_verifier"] = verifier
    out["_redirect_uri"] = reg["redirect_uris"][0]
    return out


def rpc(client, token, method, params=None, id=1):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json"},
                    json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})
    return r


def initialize(client, token):
    r = rpc(client, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ── metadata ────────────────────────────────────────────────────────────

def test_well_known_documents(env):
    client, _ = env
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"].rstrip("/") == ISSUER and asm["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert asm["registration_endpoint"] == f"{ISSUER}/register" and "S256" in asm["code_challenge_methods_supported"]
    assert set(asm["scopes_supported"]) == {"read", "write", "purchase"}
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == f"{ISSUER}/mcp" and ISSUER in prm["authorization_servers"][0]
    assert client.get("/healthz").json()["ok"] is True


def test_unauthenticated_mcp_gets_401_with_resource_metadata(env):
    client, _ = env
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


# ── full grant ──────────────────────────────────────────────────────────

@respx.mock
def test_full_flow_grant_tools_and_scoped_calls(env):
    client, fb = env
    respx.get(f"{API}/v1/account").mock(return_value=httpx.Response(200, json={"data": {"id": "acct_1", "name": "Acme"}}))
    tok = grant(client, fb)
    assert tok["token_type"].lower() == "bearer" and set(tok["scope"].split()) == {"read", "write", "purchase"}
    # backing api token minted with write permission and a recognisable name
    (_, minted), = fb.tokens.items()
    assert minted["name"] == "MCP · Claude" and minted["permissions"] == ["read", "write"] and minted["uid"] == "uid1"

    init = initialize(client, tok["access_token"])
    assert init["serverInfo"]["name"] == "Winnr" and "Winnr provisions" in init["instructions"]
    tools = rpc(client, tok["access_token"], "tools/list").json()["result"]["tools"]
    assert len(tools) == 55
    r = rpc(client, tok["access_token"], "tools/call", {"name": "winnr_get_account", "arguments": {}})
    body = r.json()["result"]
    assert json.loads(body["content"][0]["text"])["data"]["name"] == "Acme"
    # the API call carried the minted api token
    sent = respx.calls.last.request.headers["authorization"]
    assert sent == f"Bearer {minted['raw']}"


def test_read_only_consent_hides_write_tools(env):
    client, fb = env
    tok = grant(client, fb, approve_scopes=["read"])
    assert tok["scope"] == "read"
    (_, minted), = fb.tokens.items()
    assert minted["permissions"] == ["read"]
    initialize(client, tok["access_token"])
    tools = rpc(client, tok["access_token"], "tools/list").json()["result"]["tools"]
    assert len(tools) == 26 and not any(t["name"] == "winnr_delete_domain" for t in tools)
    r = rpc(client, tok["access_token"], "tools/call", {"name": "winnr_delete_domain", "arguments": {"domain_id": "d"}})
    assert "insufficient_scope" in r.json()["result"]["content"][0]["text"]


def test_consent_cannot_escalate_beyond_requested(env):
    client, fb = env
    tok = grant(client, fb, scopes=["read", "write"], approve_scopes=["read", "write", "purchase"])
    assert set(tok["scope"].split()) == {"read", "write"}


def test_deny_redirects_with_access_denied(env):
    client, fb = env
    out = grant(client, fb, decision="deny")
    assert out == {"error": "access_denied"} and not fb.tokens


def test_bad_id_token_is_rejected(env):
    client, fb = env
    out = grant(client, fb, id_token="nope")
    assert out["status"] == 400 and "verified" in out["approve_error"]["message"] and not fb.tokens


def test_expired_or_unknown_request(env):
    client, _ = env
    assert client.get("/oauth/request/nope").status_code == 404
    r = client.post("/oauth/approve", json={"request_id": "nope", "id_token": "good", "decision": "approve"})
    assert r.status_code == 400 and "expired" in r.json()["message"]


def test_code_is_single_use_and_pkce_enforced(env):
    client, fb = env
    tok = grant(client, fb)
    r = client.post("/token", data={"grant_type": "authorization_code", "code": tok["_code"], "client_id": tok["_client_id"],
                                    "redirect_uri": tok["_redirect_uri"], "code_verifier": tok["_verifier"]})
    assert r.status_code == 400  # replay
    # wrong verifier on a fresh grant
    reg = register(client)
    _, challenge = pkce()
    r = client.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": reg["redirect_uris"][0],
                                         "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False)
    rid = parse_qs(urlparse(r.headers["location"]).query)["request"][0]
    redirect_to = client.post("/oauth/approve", json={"request_id": rid, "id_token": "good", "decision": "approve"}).json()["redirect_to"]
    code = parse_qs(urlparse(redirect_to).query)["code"][0]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                    "redirect_uri": reg["redirect_uris"][0], "code_verifier": "wrong" * 10})
    assert r.status_code == 400


def test_refresh_rotates_and_revoke_kills_backing_token(env):
    client, fb = env
    tok = grant(client, fb)
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["_client_id"]})
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] != tok["access_token"] and new["refresh_token"] != tok["refresh_token"]
    # old pair is dead
    assert rpc(client, tok["access_token"], "tools/list").status_code == 401
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["_client_id"]})
    assert r.status_code == 400
    # cannot escalate on refresh
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": new["refresh_token"], "client_id": tok["_client_id"], "scope": "purchase"})
    assert r.status_code == 200  # purchase was granted originally → fine
    newer = r.json()
    # revoke (no client_secret field — public clients do not send one) → backing api token dead
    r = client.post("/revoke", data={"token": newer["refresh_token"], "client_id": tok["_client_id"]})
    assert r.status_code == 200
    (_, minted), = fb.tokens.items()
    assert minted["active"] is False
    assert rpc(client, newer["access_token"], "tools/list").status_code == 401


def test_dashboard_revocation_stops_access_within_cache_ttl(env, monkeypatch):
    client, fb = env
    tok = grant(client, fb)
    initialize(client, tok["access_token"])
    (key, minted), = fb.tokens.items()
    minted["active"] = False
    monkeypatch.setattr("winnr_mcp.remote.oauth.SESSION_CACHE_TTL", 0)
    client.app.state.provider._session_cache.clear()
    assert rpc(client, tok["access_token"], "tools/list").status_code == 401


def test_registered_clients_are_public(env):
    client, _ = env
    reg = client.post("/register", json={"client_name": "X", "redirect_uris": ["https://x.example/cb"],
                                         "token_endpoint_auth_method": "client_secret_post"}).json()
    assert reg.get("client_secret") in (None, "") and reg["token_endpoint_auth_method"] == "none"


def test_cors_allows_dashboard_only(env):
    client, _ = env
    r = client.options("/oauth/approve", headers={"Origin": DASH, "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == DASH
    r = client.options("/oauth/approve", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") is None


def test_revoke_works_with_and_without_client_secret_field(env):
    """The SDK model requires client_secret; public clients omit it. Both must work."""
    client, fb = env
    for extra in ({}, {"client_secret": ""}, {"token_type_hint": "refresh_token"}):
        tok = grant(client, fb)
        r = client.post("/revoke", data={"token": tok["refresh_token"], "client_id": tok["_client_id"], **extra})
        assert r.status_code == 200, r.text
        assert rpc(client, tok["access_token"], "tools/list").status_code == 401
    assert all(t["active"] is False for t in fb.tokens.values())


def test_revoke_access_token_also_kills_refresh(env):
    client, fb = env
    tok = grant(client, fb)
    r = client.post("/revoke", data={"token": tok["access_token"], "client_id": tok["_client_id"]})
    assert r.status_code == 200
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["_client_id"]})
    assert r.status_code == 400


def test_another_client_cannot_revoke_or_refresh_someone_elses_token(env):
    client, fb = env
    tok = grant(client, fb)
    other = register(client, name="Evil")
    r = client.post("/revoke", data={"token": tok["refresh_token"], "client_id": other["client_id"]})
    assert r.status_code == 200  # RFC 7009: unknown token → 200, but nothing revoked
    assert all(t["active"] for t in fb.tokens.values())
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": other["client_id"]})
    assert r.status_code == 400
    assert rpc(client, tok["access_token"], "tools/list").status_code in (200, 400)


def test_lambda_handler_survives_repeated_invocations(monkeypatch):
    """Regression: the session manager may only start once, but Mangum ran the
    lifespan per invocation, so every request after the first 500'd."""
    monkeypatch.setenv("WINNR_MCP_ISSUER", ISSUER)
    monkeypatch.setenv("WINNR_DASHBOARD_URL", DASH)
    monkeypatch.setenv("WINNR_API_URL", API)
    monkeypatch.setenv("WINNR_CONFIRM_SECRET", "s")
    monkeypatch.delenv("WINNR_MCP_TABLE", raising=False)
    import importlib

    module = importlib.import_module("winnr_mcp.remote.lambda_handler")
    module = importlib.reload(module)

    def event(path: str, method: str = "GET", body: str | None = None) -> dict:
        return {
            "version": "2.0",
            "rawPath": path,
            "rawQueryString": "",
            "headers": {"host": "mcp.test", "content-type": "application/json"},
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": "1.2.3.4"}},
            "body": body,
            "isBase64Encoded": False,
        }

    for _ in range(3):
        resp = module.handler(event("/healthz"), None)
        assert resp["statusCode"] == 200, resp
        resp = module.handler(event("/.well-known/oauth-authorization-server"), None)
        assert resp["statusCode"] == 200, resp
    # an unauthenticated MCP call still reaches the transport (401, not 500)
    resp = module.handler(event("/mcp", "POST", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})), None)
    assert resp["statusCode"] == 401, resp


def test_dynamo_store_roundtrip_has_no_decimals():
    """Regression: DynamoDB returns numbers as Decimal, which JSONResponse cannot serialise."""
    import json as _json
    from decimal import Decimal
    from unittest.mock import MagicMock

    from winnr_mcp.remote.store import DynamoStore, _dynamo_safe

    stored = _dynamo_safe({"created_at": 1_700_000_000, "price": 12.5, "nested": {"n": 3}, "list": [1, 2], "empty": ""})
    assert isinstance(stored["price"], Decimal) and stored["empty"] is None

    store = DynamoStore.__new__(DynamoStore)
    store._table = MagicMock()
    store._table.get_item.return_value = {"Item": {"pk": "k", "data": stored, "ttl": 9_999_999_999}}
    item = store.get("k")
    assert _json.dumps(item)  # would raise on a Decimal
    assert item["created_at"] == 1_700_000_000 and item["price"] == 12.5 and item["nested"]["n"] == 3

    store._table.get_item.return_value = {"Item": {"pk": "k", "data": stored, "ttl": 1}}
    assert store.get("k") is None  # expired items are hidden even before the TTL sweeper runs
