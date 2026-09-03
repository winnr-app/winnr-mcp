"""OAuth 2.1 authorization server for the remote Winnr MCP server.

The MCP SDK provides the HTTP endpoints (/authorize, /token, /register,
/revoke, the two .well-known documents) and PKCE verification; this class
supplies the state behind them. The login itself is the Winnr dashboard: the
authorize step redirects the user there, the dashboard shows a consent screen,
and posts back an approval signed by the user's Firebase ID token.

Every grant is backed by a real `api_tokens` document on the account
("MCP · <client name>"), so it appears on the API page, can be revoked there,
and stamps last_used_at like any other token. The OAuth access/refresh tokens
are opaque handles onto that document.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from winnr_mcp import scopes as sc
from winnr_mcp.remote.firebase_auth import FirebaseAuth, Identity
from winnr_mcp.remote.store import OAuthStore

REQUEST_TTL = 10 * 60
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 30 * 24 * 60 * 60
CLIENT_TTL = 180 * 24 * 60 * 60
SESSION_CACHE_TTL = 60


@dataclass
class Session:
    """What a tool call needs to act for the user behind an access token."""

    account_id: str
    api_token: str
    api_token_id: str
    scopes: frozenset[str]
    client_id: str


class WinnrOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, store: OAuthStore, firebase: FirebaseAuth, dashboard_url: str) -> None:
        self.store = store
        self.firebase = firebase
        self.dashboard_url = dashboard_url.rstrip("/")
        self._session_cache: dict[str, tuple[Session, float]] = {}

    # ── clients ──────────────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        item = self.store.get(f"client#{client_id}")
        if not item:
            return None
        return OAuthClientInformationFull.model_validate(item["client"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Public clients only: every MCP host (claude.ai, Cursor, ...) is a
        # public client using PKCE. A client_secret would just be one more
        # thing to leak.
        client_info.client_secret = None
        client_info.token_endpoint_auth_method = "none"
        self.store.put(
            f"client#{client_info.client_id}",
            {"client": client_info.model_dump(mode="json", exclude_none=True), "registered_at": int(time.time())},
            ttl_seconds=CLIENT_TTL,
        )

    # ── authorize → dashboard consent → code ─────────────────────────────

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        requested = sc.normalize(params.scopes or sc.ALL_SCOPES)
        if not requested:
            raise AuthorizeError(error="invalid_scope", error_description="No valid scopes requested")
        request_id = secrets.token_urlsafe(24)
        self.store.put(
            f"req#{request_id}",
            {
                "client_id": client.client_id,
                "client_name": client.client_name or "",
                "client_uri": str(client.client_uri) if client.client_uri else None,
                "logo_uri": str(client.logo_uri) if client.logo_uri else None,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "state": params.state,
                "code_challenge": params.code_challenge,
                "scopes": sorted(requested),
                "resource": params.resource,
                "created_at": int(time.time()),
            },
            ttl_seconds=REQUEST_TTL,
        )
        return f"{self.dashboard_url}/mcp/authorize?request={request_id}"

    def pending_request(self, request_id: str) -> dict[str, Any] | None:
        """What the consent screen shows. Never includes the code challenge or state."""
        item = self.store.get(f"req#{request_id}")
        if not item:
            return None
        return {
            "client_name": item.get("client_name") or "An MCP client",
            "client_uri": item.get("client_uri"),
            "logo_uri": item.get("logo_uri"),
            "redirect_host": urlparse(item["redirect_uri"]).hostname,
            "scopes": item["scopes"],
            "scope_descriptions": {s: sc.SCOPE_DESCRIPTIONS[s] for s in item["scopes"]},
            "expires_at": item["created_at"] + REQUEST_TTL,
        }

    def approve(self, request_id: str, identity: Identity, approved_scopes: list[str] | None, decision: str) -> str:
        """Turn a consent decision into the redirect back to the client."""
        item = self.store.get(f"req#{request_id}")
        if not item:
            raise ValueError("This sign-in request has expired. Start again from your AI assistant.")
        self.store.delete(f"req#{request_id}")
        if decision != "approve":
            return construct_redirect_uri(item["redirect_uri"], error="access_denied", state=item.get("state"))

        requested = sc.normalize(item["scopes"])
        granted = sc.normalize(approved_scopes) if approved_scopes is not None else requested
        granted = granted & requested
        if sc.READ not in granted:
            raise ValueError("At least read access is required.")

        code = secrets.token_urlsafe(32)
        self.store.put(
            f"code#{code}",
            {
                "code": code,
                "client_id": item["client_id"],
                "client_name": item.get("client_name") or "",
                "redirect_uri": item["redirect_uri"],
                "redirect_uri_provided_explicitly": item.get("redirect_uri_provided_explicitly", True),
                "code_challenge": item["code_challenge"],
                "scopes": sorted(granted),
                "resource": item.get("resource"),
                "account_id": identity.account_id,
                "user_id": identity.uid,
                "expires_at": int(time.time()) + CODE_TTL,
            },
            ttl_seconds=CODE_TTL,
        )
        return construct_redirect_uri(item["redirect_uri"], code=code, state=item.get("state"))

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        item = self.store.get(f"code#{authorization_code}")
        if not item or item["client_id"] != client.client_id or item["expires_at"] < time.time():
            return None
        return AuthorizationCode(
            code=item["code"],
            scopes=item["scopes"],
            expires_at=float(item["expires_at"]),
            client_id=item["client_id"],
            code_challenge=item["code_challenge"],
            redirect_uri=item["redirect_uri"],
            redirect_uri_provided_explicitly=bool(item.get("redirect_uri_provided_explicitly", True)),
            resource=item.get("resource"),
        )

    # ── tokens ───────────────────────────────────────────────────────────

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        item = self.store.get(f"code#{authorization_code.code}")
        if not item or item["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Authorization code is unknown or already used")
        self.store.delete(f"code#{authorization_code.code}")  # single use

        scopes = sc.normalize(item["scopes"])
        client_name = item.get("client_name") or client.client_name or "MCP client"
        token_id, raw = self.firebase.mint_api_token(
            account_id=item["account_id"],
            uid=item["user_id"],
            name=f"MCP · {client_name}",
            permissions=sc.permissions_from_scopes(scopes),
            client_id=client.client_id,
        )
        return self._issue_tokens(
            client_id=client.client_id,
            account_id=item["account_id"],
            api_token_id=token_id,
            api_token=raw,
            scopes=scopes,
            resource=item.get("resource"),
        )

    def _issue_tokens(self, *, client_id: str, account_id: str, api_token_id: str, api_token: str,
                      scopes: frozenset[str], resource: str | None) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = int(time.time())
        base = {
            "client_id": client_id,
            "account_id": account_id,
            "api_token_id": api_token_id,
            "api_token": api_token,
            "scopes": sorted(scopes),
            "resource": resource,
        }
        self.store.put(f"access#{access}", {**base, "token": access, "expires_at": now + ACCESS_TTL, "refresh": refresh},
                       ttl_seconds=ACCESS_TTL)
        self.store.put(f"refresh#{refresh}", {**base, "token": refresh, "expires_at": now + REFRESH_TTL, "access": access},
                       ttl_seconds=REFRESH_TTL)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=" ".join(sorted(scopes)),
            refresh_token=refresh,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        item = self.store.get(f"refresh#{refresh_token}")
        if not item or item["client_id"] != client.client_id or item["expires_at"] < time.time():
            return None
        return RefreshToken(token=item["token"], client_id=item["client_id"], scopes=item["scopes"], expires_at=int(item["expires_at"]))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        item = self.store.get(f"refresh#{refresh_token.token}")
        if not item or item["client_id"] != client.client_id:
            raise TokenError("invalid_grant", "Refresh token is unknown or already used")
        granted = sc.normalize(item["scopes"])
        wanted = sc.normalize(scopes) if scopes else granted
        if not wanted <= granted:
            raise TokenError("invalid_scope", "Cannot escalate scopes on refresh")
        # Rotate: the old pair dies with this exchange.
        self.store.delete(f"refresh#{refresh_token.token}")
        if item.get("access"):
            self.store.delete(f"access#{item['access']}")
            self._session_cache.pop(item["access"], None)
        if not self.firebase.api_token_active(item["account_id"], item["api_token_id"]):
            raise TokenError("invalid_grant", "Access was revoked in the Winnr dashboard. Connect again.")
        return self._issue_tokens(
            client_id=client.client_id,
            account_id=item["account_id"],
            api_token_id=item["api_token_id"],
            api_token=item["api_token"],
            scopes=wanted,
            resource=item.get("resource"),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        session = self.session(token)
        if session is None:
            return None
        item = self.store.get(f"access#{token}")  # already validated by session(); need expiry/resource
        return AccessToken(
            token=token,
            client_id=session.client_id,
            scopes=sorted(session.scopes),
            expires_at=int(item["expires_at"]) if item else None,
            resource=item.get("resource") if item else None,
        )

    def session(self, access_token: str) -> Session | None:
        """Resolve (and briefly cache) the account/api-token behind an access token.

        Also re-checks that the backing api_tokens doc is still active, so a
        token revoked on the API page stops working within a minute.
        """
        cached = self._session_cache.get(access_token)
        if cached and cached[1] > time.time():
            return cached[0]
        item = self.store.get(f"access#{access_token}")
        if not item or item["expires_at"] < time.time():
            self._session_cache.pop(access_token, None)
            return None
        if not self.firebase.api_token_active(item["account_id"], item["api_token_id"]):
            self.store.delete(f"access#{access_token}")
            self._session_cache.pop(access_token, None)
            return None
        session = Session(
            account_id=item["account_id"],
            api_token=item["api_token"],
            api_token_id=item["api_token_id"],
            scopes=sc.normalize(item["scopes"]),
            client_id=item["client_id"],
        )
        if len(self._session_cache) > 512:
            self._session_cache.clear()
        self._session_cache[access_token] = (session, time.time() + SESSION_CACHE_TTL)
        return session

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, RefreshToken):
            item = self.store.get(f"refresh#{token.token}")
            self.store.delete(f"refresh#{token.token}")
            if item:
                if item.get("access"):
                    self.store.delete(f"access#{item['access']}")
                    self._session_cache.pop(item["access"], None)
                # The grant is gone for good: kill the backing API token too.
                try:
                    self.firebase.deactivate_api_token(item["account_id"], item["api_token_id"])
                except Exception:  # noqa: BLE001 — best effort; the token is orphaned but harmless
                    pass
        else:
            item = self.store.get(f"access#{token.token}")
            self.store.delete(f"access#{token.token}")
            self._session_cache.pop(token.token, None)
            if item and item.get("refresh"):
                self.store.delete(f"refresh#{item['refresh']}")
                try:
                    self.firebase.deactivate_api_token(item["account_id"], item["api_token_id"])
                except Exception:  # noqa: BLE001
                    pass
