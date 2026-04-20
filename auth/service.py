from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from utils.time_utils import utc_now


SESSION_COOKIE_NAME = os.getenv("SINTRA_SESSION_COOKIE_NAME", "sintra_session")
SESSION_TTL_HOURS = int(os.getenv("SINTRA_SESSION_TTL_HOURS", "168") or "168")
AUTH_REQUIRED = str(os.getenv("SINTRA_AUTH_REQUIRED", "") or "").lower() in {"1", "true", "yes", "on"}


class _RepositoryProxy:
    def __getattr__(self, name: str) -> Any:
        from storage import repositories

        return getattr(repositories, name)


repo = _RepositoryProxy()


class AuthServiceError(Exception):
    """Framework-neutral auth error translated by API adapters."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class AuthenticatedUser:
    id: str
    email: str = ""
    display_name: str = ""
    avatar_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
        }


def is_auth_required() -> bool:
    return str(os.getenv("SINTRA_AUTH_REQUIRED", "") or "").lower() in {"1", "true", "yes", "on"}


def _session_secret() -> str:
    secret = os.getenv("SINTRA_SESSION_SECRET", "")
    if not secret:
        if is_auth_required():
            raise RuntimeError("SINTRA_SESSION_SECRET is required when SINTRA_AUTH_REQUIRED=1.")
        secret = "dev-only-sintra-session-secret"
    return secret


def hash_session_token(token: str) -> str:
    return hmac.new(_session_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def extract_session_token(request: Any) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.cookies.get(SESSION_COOKIE_NAME, "") or "")


def set_session_cookie(response: Any, token: str) -> None:
    secure = str(os.getenv("SINTRA_SESSION_COOKIE_SECURE", "true") or "true").lower() not in {"0", "false", "no"}
    same_site = os.getenv("SINTRA_SESSION_COOKIE_SAMESITE", "lax") or "lax"
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite=same_site,
        max_age=SESSION_TTL_HOURS * 3600,
    )


def clear_session_cookie(response: Any) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


def verify_google_credential(credential: str) -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise AuthServiceError(503, "GOOGLE_CLIENT_ID is not configured.")
    try:
        from google.auth.transport import requests
        from google.oauth2 import id_token
    except Exception as exc:  # pragma: no cover - dependency failure is environment-specific
        raise AuthServiceError(503, "google-auth is not installed.") from exc
    try:
        payload = id_token.verify_oauth2_token(credential, requests.Request(), client_id)
    except Exception as exc:
        raise AuthServiceError(401, "Invalid Google credential.") from exc

    issuer = str(payload.get("iss", "") or "")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise AuthServiceError(401, "Invalid Google issuer.")
    hosted_domain = os.getenv("GOOGLE_ALLOWED_HOSTED_DOMAIN", "").strip()
    if hosted_domain and payload.get("hd") != hosted_domain:
        raise AuthServiceError(403, "Google account domain is not allowed.")
    if not payload.get("sub"):
        raise AuthServiceError(401, "Google credential missing subject.")
    return payload


def validate_google_csrf(request: Any, body_token: str = "") -> None:
    cookie_token = str(request.cookies.get("g_csrf_token", "") or "")
    if not cookie_token and not body_token:
        return
    if not cookie_token or not body_token or not hmac.compare_digest(cookie_token, body_token):
        raise AuthServiceError(400, "Invalid Google CSRF token.")


def sign_in_with_google_payload(db: Any, payload: dict[str, Any]) -> tuple[Any, Any, str]:
    subject = str(payload.get("sub", "") or "")
    email = str(payload.get("email", "") or "").strip().lower()
    display_name = str(payload.get("name", "") or "").strip()
    avatar_url = str(payload.get("picture", "") or "").strip()
    email_verified = bool(payload.get("email_verified", False))

    identity = repo.get_external_identity(db, "google", subject)
    if identity:
        user = repo.get_user(db, identity.user_id)
        if not user:
            user = repo.upsert_user(
                db,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                metadata_json={"email_verified": email_verified},
            )
            repo.upsert_external_identity(
                db,
                user_id=user.id,
                provider="google",
                provider_subject=subject,
                email=email,
                metadata_json={"email_verified": email_verified, "hd": payload.get("hd", "")},
            )
    else:
        user = repo.get_user_by_email(db, email) if email and email_verified else None
        if not user:
            user = repo.upsert_user(
                db,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                metadata_json={"email_verified": email_verified},
            )
        repo.upsert_external_identity(
            db,
            user_id=user.id,
            provider="google",
            provider_subject=subject,
            email=email,
            metadata_json={"email_verified": email_verified, "hd": payload.get("hd", "")},
        )

    workspace = repo.ensure_default_workspace_for_user(db, user)
    token = secrets.token_urlsafe(32)
    repo.create_auth_session(
        db,
        user_id=user.id,
        session_hash=hash_session_token(token),
        expires_at=utc_now() + timedelta(hours=SESSION_TTL_HOURS),
        metadata_json={"provider": "google"},
    )
    return user, workspace, token


def get_current_user_from_token(db: Any, token: str) -> AuthenticatedUser | None:
    if not token:
        return None
    session = repo.get_active_auth_session_by_hash(db, hash_session_token(token))
    if not session:
        return None
    user = repo.get_user(db, session.user_id)
    if not user or getattr(user, "status", "") != "active":
        return None
    return AuthenticatedUser(
        id=user.id,
        email=getattr(user, "email", "") or "",
        display_name=getattr(user, "display_name", "") or "",
        avatar_url=getattr(user, "avatar_url", "") or "",
    )


def get_current_user_from_request(db: Any, request: Any) -> AuthenticatedUser | None:
    return get_current_user_from_token(db, extract_session_token(request))


def require_workspace_access(db: Any, workspace_id: str, user: AuthenticatedUser | None) -> None:
    if not is_auth_required() and user is None:
        return
    if not user:
        raise AuthServiceError(401, "Authentication required.")
    if not repo.get_workspace_membership(db, workspace_id, user.id):
        raise AuthServiceError(403, "Workspace access denied.")
