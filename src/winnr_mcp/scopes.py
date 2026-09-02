"""Scopes: what a session may do.

Three scopes, each implying the previous:

    read      list/inspect anything
    write     create, change, send, delete
    purchase  spend money (buy domains, buy pre-warmed domains, enable warming)

stdio sessions derive scopes from the API token's permissions once at startup.
Remote (OAuth) sessions carry them on the access token, per request. Both feed
`current_scopes()`, which every tool consults before running and which the
tools/list handler uses to hide what the session cannot call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar

READ = "read"
WRITE = "write"
PURCHASE = "purchase"
ALL_SCOPES: tuple[str, ...] = (READ, WRITE, PURCHASE)

SCOPE_DESCRIPTIONS = {
    READ: "See domains, mailboxes, warming stats, replies, jobs and webhooks",
    WRITE: "Create and change mailboxes, domains, warming, webhooks; send replies; delete things",
    PURCHASE: "Spend money: buy domains, buy pre-warmed domains, enable warming",
}

ScopeProvider = Callable[[], frozenset[str]]

# Per-request override (remote server sets this from the access token).
_request_scopes: ContextVar[frozenset[str] | None] = ContextVar("winnr_scopes", default=None)
# Process-wide default (stdio server sets this from the token's permissions).
_default_provider: ScopeProvider = lambda: frozenset()  # noqa: E731


def normalize(scopes: Iterable[str]) -> frozenset[str]:
    """Apply the implication chain and drop unknown values."""
    s = {x.strip().lower() for x in scopes if isinstance(x, str)}
    out: set[str] = set()
    if PURCHASE in s:
        out.update(ALL_SCOPES)
    elif WRITE in s:
        out.update((READ, WRITE))
    elif READ in s:
        out.add(READ)
    return frozenset(out)


def scopes_from_permissions(permissions: Iterable[str], allow_purchases: bool = True) -> frozenset[str]:
    """Map API-token permissions (["read"] / ["read","write"]) to MCP scopes."""
    perms = {p for p in permissions if isinstance(p, str)}
    if "write" in perms:
        return normalize((READ, WRITE, PURCHASE) if allow_purchases else (READ, WRITE))
    return normalize((READ,)) if "read" in perms else frozenset()


def permissions_from_scopes(scopes: Iterable[str]) -> list[str]:
    """Inverse mapping for minting an API token behind an OAuth grant."""
    s = normalize(scopes)
    return ["read", "write"] if WRITE in s else ["read"]


def set_default_scope_provider(provider: ScopeProvider) -> None:
    global _default_provider
    _default_provider = provider


def set_request_scopes(scopes: Iterable[str] | None):
    """Bind scopes for the current request (returns the contextvar token to reset)."""
    return _request_scopes.set(normalize(scopes) if scopes is not None else None)


def reset_request_scopes(token) -> None:
    _request_scopes.reset(token)


def current_scopes() -> frozenset[str]:
    override = _request_scopes.get()
    if override is not None:
        return override
    return _default_provider()
