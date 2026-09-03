"""Firebase-backed identity for the remote server.

Two jobs: (1) verify the dashboard's Firebase ID token at consent time and map
the user to their Winnr account; (2) mint / deactivate the `api_tokens` doc
that backs an OAuth grant, in exactly the shape winnr-api creates them, so the
grant shows up on the API page, is revocable there, and stamps last_used_at.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

TOKEN_PREFIX = "wnr"
SECRET_LENGTH = 24
_CHARSET = string.ascii_letters + string.digits


@dataclass
class Identity:
    uid: str
    email: str | None
    account_id: str


class FirebaseAuth:
    def __init__(self) -> None:
        self._db = None

    # ── setup ────────────────────────────────────────────────────────────

    def _init(self) -> None:
        if self._db is not None:
            return
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            encoded = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
            if not encoded:
                import boto3

                encoded = boto3.client("ssm", region_name="us-east-1").get_parameter(
                    Name="/winnr/frontend/FIREBASE_SERVICE_ACCOUNT_KEY", WithDecryption=True
                )["Parameter"]["Value"]
            info = json.loads(base64.b64decode(encoded).decode("utf-8"))
            firebase_admin.initialize_app(credentials.Certificate(info))
        self._db = firestore.client()

    @property
    def db(self):
        self._init()
        return self._db

    # ── identity ─────────────────────────────────────────────────────────

    def identify(self, id_token: str) -> Identity:
        """Verify a Firebase ID token and resolve the user's Winnr account.

        Raises ValueError with a user-safe message on any failure.
        """
        self._init()
        from firebase_admin import auth as fb_auth

        try:
            decoded = fb_auth.verify_id_token(id_token, check_revoked=False)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Your sign-in could not be verified. Sign in to the dashboard again.") from exc
        uid = decoded.get("uid") or decoded.get("sub")
        if not uid:
            raise ValueError("Your sign-in could not be verified.")
        user_doc = self.db.collection("users").document(uid).get()
        data = user_doc.to_dict() if user_doc.exists else None
        account_id = _extract_account_id(data or {})
        if not account_id:
            raise ValueError("This login has no Winnr account attached yet. Finish signup in the dashboard first.")
        return Identity(uid=uid, email=decoded.get("email") or (data or {}).get("email"), account_id=account_id)

    # ── api tokens backing grants ────────────────────────────────────────

    def mint_api_token(self, account_id: str, uid: str, name: str, permissions: list[str], client_id: str) -> tuple[str, str]:
        """Create an api_tokens doc like winnr-api does. Returns (token_id, raw_token)."""
        secret = "".join(secrets.choice(_CHARSET) for _ in range(SECRET_LENGTH))
        raw = f"{TOKEN_PREFIX}_{account_id}_{secret}"
        doc: dict[str, Any] = {
            "name": name[:100],
            "token_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "token_prefix": raw[:12],
            "permissions": list(permissions),
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "last_used_at": None,
            "expires_at": None,
            "created_by": uid,
            "source": "mcp_oauth",
            "oauth_client_id": client_id,
        }
        _, ref = self.db.collection("accounts").document(account_id).collection("api_tokens").add(doc)
        return ref.id, raw

    def api_token_active(self, account_id: str, token_id: str) -> bool:
        snap = self.db.collection("accounts").document(account_id).collection("api_tokens").document(token_id).get()
        return bool(snap.exists and (snap.to_dict() or {}).get("is_active", False))

    def deactivate_api_token(self, account_id: str, token_id: str) -> None:
        ref = self.db.collection("accounts").document(account_id).collection("api_tokens").document(token_id)
        ref.update({"is_active": False, "revoked_at": datetime.now(timezone.utc)})


def _extract_account_id(user_data: dict[str, Any]) -> str | None:
    """Same rules as winnr-api's auth middleware (account_id string, or `account` ref/path)."""
    account_id = user_data.get("account_id")
    if account_id and isinstance(account_id, str):
        return account_id
    ref = user_data.get("account")
    if ref is None:
        return None
    if hasattr(ref, "id"):
        return ref.id
    if isinstance(ref, str):
        return ref.split("/")[-1] if "/" in ref else ref
    return None
