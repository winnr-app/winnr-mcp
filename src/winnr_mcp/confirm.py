"""Two-step confirmation for tools that spend money.

A money tool called without a `confirmation_token` does NOT buy anything: it
returns a quote (exact items, live prices, total) plus a token that is an HMAC
over that quote, the account, and an expiry. The assistant shows the quote,
gets an explicit yes, and calls again with the token. The server recomputes
the quote at that moment; if anything changed (price, availability, the item
list) the HMAC no longer matches and the purchase is refused with a fresh quote.

The token is self-contained, so it works on a stateless remote server as long
as every instance shares the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

DEFAULT_TTL_SECONDS = 600


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class Confirmer:
    def __init__(self, secret: bytes | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self.ttl = ttl_seconds

    def _digest(self, kind: str, subject: str, exp: int, payload: Any) -> str:
        msg = "\n".join((kind, subject, str(exp), canonical(payload))).encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def issue(self, kind: str, subject: str, payload: Any) -> tuple[str, int]:
        """Return (token, expires_at_unix)."""
        exp = int(time.time()) + self.ttl
        raw = f"{exp}.{self._digest(kind, subject, exp, payload)}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("="), exp

    def verify(self, token: str, kind: str, subject: str, payload: Any) -> str | None:
        """Return None when valid, else a short reason."""
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode()).decode()
            exp_s, digest = raw.split(".", 1)
            exp = int(exp_s)
        except Exception:  # noqa: BLE001
            return "malformed"
        if exp < int(time.time()):
            return "expired"
        if not hmac.compare_digest(digest, self._digest(kind, subject, exp, payload)):
            return "quote_changed"
        return None
