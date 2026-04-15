"""
utils/logging_utils.py  —  Minimal structured logging helpers for the critical path.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
from typing import Any, Iterator


_request_id_var = contextvars.ContextVar("request_id", default="")
_workspace_id_var = contextvars.ContextVar("workspace_id", default="")
_configured = False

_SECRET_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "connect_url",
    "resume_token",
)


def configure_logging(level: str | None = None) -> None:
    """Best-effort bootstrap for app logging."""
    global _configured
    if _configured:
        return
    try:
        log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
        root = logging.getLogger()
        if not root.handlers:
            logging.basicConfig(level=log_level, format="%(message)s")
        else:
            root.setLevel(log_level)
        _configured = True
    except Exception:
        # Logging bootstrap must never block app startup.
        pass


def current_request_id() -> str:
    return _request_id_var.get("")


def current_workspace_id() -> str:
    return _workspace_id_var.get("")


@contextlib.contextmanager
def request_context(request_id: str = "", workspace_id: str = "") -> Iterator[None]:
    request_token = _request_id_var.set(str(request_id or "").strip())
    workspace_token = _workspace_id_var.set(str(workspace_id or "").strip())
    try:
        yield
    finally:
        _request_id_var.reset(request_token)
        _workspace_id_var.reset(workspace_token)


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 160 else f"{value[:157]}..."
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item) for item in list(value)[:10]]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(val) for key, val in list(value.items())[:20]}
    return str(value)


def sanitize_context(context: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (context or {}).items():
        key_str = str(key)
        lowered = key_str.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS):
            clean[key_str] = "[redacted]"
            continue
        clean[key_str] = _sanitize_value(value)
    return clean


def _build_payload(event: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"event": event}
    merged = sanitize_context(context)
    request_id = str(merged.pop("request_id", "") or current_request_id()).strip()
    workspace_id = str(merged.pop("workspace_id", "") or current_workspace_id()).strip()
    if request_id:
        payload["request_id"] = request_id
    if workspace_id:
        payload["workspace_id"] = workspace_id
    for key, value in merged.items():
        if value in ("", None, [], {}):
            continue
        payload[key] = value
    return payload


def log_event(logger: Any, level: int, event: str, **context: Any) -> None:
    try:
        payload = _build_payload(event, context)
        logger.log(level, json.dumps(payload, default=str, ensure_ascii=True))
    except Exception:
        pass


def log_exception(logger: Any, event: str, exc: BaseException, level: int = logging.ERROR, **context: Any) -> None:
    try:
        payload = _build_payload(
            event,
            {
                **context,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        logger.log(level, json.dumps(payload, default=str, ensure_ascii=True), exc_info=True)
    except Exception:
        pass
