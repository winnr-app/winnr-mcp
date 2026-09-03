"""ASGI application for the remote server (Streamable HTTP + OAuth)."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from winnr_mcp import __version__
from winnr_mcp import scopes as sc
from winnr_mcp.client import ClientProxy, WinnrClient
from winnr_mcp.config import DEFAULT_API_URL, WinnrConfig
from winnr_mcp.confirm import Confirmer
from winnr_mcp.remote.firebase_auth import FirebaseAuth
from winnr_mcp.remote.oauth import WinnrOAuthProvider
from winnr_mcp.remote.store import DynamoStore, MemoryStore, OAuthStore
from winnr_mcp.server import build_mcp

# API Gateway HTTP APIs cut responses at 30s.
REMOTE_MAX_WAIT_SECONDS = 25


class NotAuthenticated(RuntimeError):
    pass


class PublicClientFormCompat:
    """Inject an empty `client_secret` into /revoke posts that omit it.

    Every MCP host registers as a public client (no secret), but the SDK's
    RevocationRequest model marks `client_secret` required, so a spec-compliant
    public client gets 400 invalid_request instead of a revocation. We add the
    empty field before the handler parses the form; client authentication is
    unaffected because the registered client genuinely has no secret.
    """

    def __init__(self, app, paths: tuple[str, ...] = ("/revoke",)) -> None:
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in self.paths
        ):
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)

        if b"client_secret=" not in body:
            body = body + (b"&" if body else b"") + b"client_secret="
            headers = []
            for key, value in scope.get("headers", []):
                if key.lower() == b"content-length":
                    value = str(len(body)).encode()
                headers.append((key, value))
            scope = {**scope, "headers": headers}

        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


def _confirm_secret() -> bytes:
    raw = os.environ.get("WINNR_CONFIRM_SECRET", "").strip()
    if raw:
        return raw.encode()
    import boto3

    return boto3.client("ssm", region_name="us-east-1").get_parameter(
        Name="/winnr/mcp/CONFIRM_SECRET", WithDecryption=True
    )["Parameter"]["Value"].encode()


def create_app(
    *,
    store: OAuthStore | None = None,
    firebase: FirebaseAuth | None = None,
    issuer: str | None = None,
    dashboard_url: str | None = None,
    api_url: str | None = None,
    confirm_secret: bytes | None = None,
) -> Starlette:
    issuer = (issuer or os.environ.get("WINNR_MCP_ISSUER", "https://mcp.winnr.app")).rstrip("/")
    dashboard_url = (dashboard_url or os.environ.get("WINNR_DASHBOARD_URL", "https://app.winnr.app")).rstrip("/")
    api_url = (api_url or os.environ.get("WINNR_API_URL", DEFAULT_API_URL)).rstrip("/")
    if store is None:
        table = os.environ.get("WINNR_MCP_TABLE", "").strip()
        store = DynamoStore(table) if table else MemoryStore()
    firebase = firebase or FirebaseAuth()
    provider = WinnrOAuthProvider(store=store, firebase=firebase, dashboard_url=dashboard_url)
    issuer_host = urlparse(issuer).netloc

    config = WinnrConfig(
        api_token="wnr_remote_placeholder_never_used",
        api_url=api_url,
        confirmer=Confirmer(confirm_secret or _confirm_secret()),
        max_wait_seconds=REMOTE_MAX_WAIT_SECONDS,
    )

    # One WinnrClient per api token, reused across requests in a warm container.
    clients: dict[str, tuple[WinnrClient, float]] = {}

    def resolve() -> WinnrClient:
        access = get_access_token()
        if access is None:
            raise NotAuthenticated("no access token on this request")
        session = provider.session(access.token)
        if session is None:
            raise NotAuthenticated("access token expired or revoked")
        entry = clients.get(session.api_token)
        if entry and entry[1] > time.time():
            return entry[0]
        if len(clients) > 256:
            for _, (c, _) in clients.items():
                c.close()
            clients.clear()
        client = WinnrClient(config, api_token=session.api_token, account_id=session.account_id)
        clients[session.api_token] = (client, time.time() + 15 * 60)
        return client

    def request_scopes() -> frozenset[str]:
        access = get_access_token()
        return sc.normalize(access.scopes) if access else frozenset()

    sc.set_default_scope_provider(request_scopes)

    mcp = build_mcp(
        ClientProxy(resolve),
        config,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(f"{issuer}/mcp"),
            service_documentation_url=AnyHttpUrl(f"{dashboard_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=list(sc.ALL_SCOPES),
                default_scopes=list(sc.ALL_SCOPES),
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[sc.READ],
        ),
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection: only our own host may reach /mcp, and only
        # the dashboard may drive it from a browser. API Gateway forwards the
        # custom-domain Host header, so this matches in production too.
        transport_security=TransportSecuritySettings(
            allowed_hosts=[issuer_host, f"{issuer_host}:443"],
            allowed_origins=[dashboard_url, issuer],
        ),
    )

    # ── consent endpoints used by the dashboard ──────────────────────────

    @mcp.custom_route("/oauth/request/{request_id}", methods=["GET"])
    async def oauth_request(request: Request) -> Response:
        info = provider.pending_request(request.path_params["request_id"])
        if not info:
            return JSONResponse({"error": "expired", "message": "This sign-in request has expired. Start again from your AI assistant."}, status_code=404)
        return JSONResponse(info)

    @mcp.custom_route("/oauth/approve", methods=["POST"])
    async def oauth_approve(request: Request) -> Response:
        try:
            body: dict[str, Any] = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        request_id = str(body.get("request_id") or "")
        id_token = str(body.get("id_token") or "")
        decision = "approve" if body.get("decision") == "approve" else "deny"
        scopes = body.get("scopes")
        if not request_id or (decision == "approve" and not id_token):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        try:
            if decision == "approve":
                identity = firebase.identify(id_token)
            else:
                from winnr_mcp.remote.firebase_auth import Identity

                identity = Identity(uid="", email=None, account_id="")
            redirect_to = provider.approve(
                request_id,
                identity,
                scopes if isinstance(scopes, list) else None,
                decision,
            )
        except ValueError as exc:
            return JSONResponse({"error": "rejected", "message": str(exc)}, status_code=400)
        return JSONResponse({"redirect_to": redirect_to})

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> Response:
        return JSONResponse({"ok": True, "version": __version__})

    @mcp.custom_route("/", methods=["GET"])
    async def index(_: Request) -> Response:
        return JSONResponse({
            "name": "Winnr MCP",
            "version": __version__,
            "mcp_endpoint": f"{issuer}/mcp",
            "setup": f"{dashboard_url}/mcp",
            "authorization_server": f"{issuer}/.well-known/oauth-authorization-server",
        })

    app = mcp.streamable_http_app()
    app.add_middleware(PublicClientFormCompat)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[dashboard_url],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )
    # Expose to the handler/tests without re-creating.
    app.state.provider = provider
    app.state.mcp = mcp
    return app


def error_json(message: str) -> str:
    return json.dumps({"error": {"message": message, "code": "not_authenticated", "status_code": 401}})
