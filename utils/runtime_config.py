"""
Runtime configuration guards for production-sensitive startup paths.
"""
from __future__ import annotations

import os
from typing import Iterable


TRUE_VALUES = {"1", "true", "yes", "on"}
PLACEHOLDER_SESSION_SECRETS = {
    "",
    "replace-with-a-long-random-secret",
    "dev-only-sintra-session-secret",
    "change-me",
    "changeme",
    "your-session-secret",
}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in TRUE_VALUES


def is_auth_required() -> bool:
    return env_flag("SINTRA_AUTH_REQUIRED", False)


def allow_insecure_dev_auth() -> bool:
    return env_flag("SINTRA_ALLOW_INSECURE_DEV_AUTH", False)


def allow_schema_bootstrap() -> bool:
    return env_flag("SINTRA_ALLOW_SCHEMA_BOOTSTRAP", False)


def allow_legacy_schema_fallbacks() -> bool:
    return env_flag("SINTRA_ALLOW_LEGACY_SCHEMA_FALLBACKS", False)


def allow_composio_version_check_bypass() -> bool:
    return env_flag("COMPOSIO_ALLOW_VERSION_CHECK_BYPASS", False)


def auth_required_schema_strict() -> bool:
    return is_auth_required() and not allow_insecure_dev_auth()


def parse_allowed_origins(value: str | None = None) -> list[str]:
    raw = value if value is not None else (os.getenv("SINTRA_ALLOWED_ORIGINS", "*") or "*")
    return [origin.strip() for origin in str(raw).split(",") if origin.strip()]


def validate_cors_config(allowed_origins: Iterable[str] | None = None) -> None:
    origins = list(allowed_origins if allowed_origins is not None else parse_allowed_origins())
    if is_auth_required() and "*" in origins and not allow_insecure_dev_auth():
        raise RuntimeError(
            "SINTRA_ALLOWED_ORIGINS='*' is not allowed with credentials when "
            "SINTRA_AUTH_REQUIRED=1. Set explicit origins or use "
            "SINTRA_ALLOW_INSECURE_DEV_AUTH=1 for local development only."
        )


def validate_session_config() -> None:
    if not is_auth_required():
        return
    secret = str(os.getenv("SINTRA_SESSION_SECRET", "") or "").strip()
    if secret.lower() in PLACEHOLDER_SESSION_SECRETS or len(secret) < 32:
        raise RuntimeError(
            "SINTRA_SESSION_SECRET must be a non-placeholder secret of at least "
            "32 characters when SINTRA_AUTH_REQUIRED=1."
        )
    secure_cookie = env_flag("SINTRA_SESSION_COOKIE_SECURE", True)
    if not secure_cookie and not allow_insecure_dev_auth():
        raise RuntimeError(
            "SINTRA_SESSION_COOKIE_SECURE=false is not allowed when "
            "SINTRA_AUTH_REQUIRED=1 unless SINTRA_ALLOW_INSECURE_DEV_AUTH=1."
        )


def validate_schema_bootstrap_config() -> None:
    if is_auth_required() and allow_schema_bootstrap() and not allow_insecure_dev_auth():
        raise RuntimeError(
            "SINTRA_ALLOW_SCHEMA_BOOTSTRAP=1 is not allowed when "
            "SINTRA_AUTH_REQUIRED=1. Run Alembic migrations before startup."
        )


def validate_production_config(*, allowed_origins: Iterable[str] | None = None) -> None:
    validate_cors_config(allowed_origins)
    validate_session_config()
    validate_schema_bootstrap_config()


def should_fail_on_revision_drift() -> bool:
    return is_auth_required() and not allow_insecure_dev_auth()


def legacy_schema_fallback_allowed(table_name: str = "") -> bool:
    if allow_legacy_schema_fallbacks():
        return True
    return not is_auth_required()
