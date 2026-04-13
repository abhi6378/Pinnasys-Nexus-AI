"""
tools/composio_client.py  —  Composio SDK wrapper: session management,
connection checks, and Connect Link generation.

This module is the ONLY place that imports the Composio SDK. All other
modules go through the functions here.

Design decisions:
  - Lazy initialization: the Composio client is created on first use, so
    the app can start even if COMPOSIO_API_KEY is not set (tools are just
    unavailable).
  - Session cache: sessions are cached per user_id in a process-level dict.
    Composio sessions are immutable and don't expire, so this is safe.
  - Graceful degradation: every public function catches exceptions and
    returns a safe default so that callers never crash.

This module does NOT execute tools — that's tool_executor.py.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Internal state ────────────────────────────────────────────────────────────

_composio_client = None          # Composio instance — created lazily
_sessions: dict[str, object] = {}  # user_id → Session
_initialized = False
_api_key: str = ""


# ── Initialization ────────────────────────────────────────────────────────────

def _ensure_client():
    """
    Lazily initialize the Composio client.
    Called internally before any SDK operation.

    Returns True if the client is ready, False otherwise.
    """
    global _composio_client, _initialized, _api_key

    if _initialized:
        return _composio_client is not None

    _initialized = True
    _api_key = os.getenv("COMPOSIO_API_KEY", "")

    if not _api_key:
        logger.warning(
            "COMPOSIO_API_KEY not set — tool integrations disabled. "
            "Set the key in .env to enable Composio tools."
        )
        return False

    try:
        from composio import Composio
        _composio_client = Composio(api_key=_api_key)
        logger.info("Composio client initialized successfully.")
        return True
    except ImportError:
        logger.warning(
            "composio package not installed — tool integrations disabled. "
            "Run: pip install composio"
        )
        return False
    except Exception as exc:
        logger.error("Failed to initialize Composio client: %s", exc)
        return False


def is_available() -> bool:
    """Return True if the Composio SDK is ready to use."""
    return _ensure_client()


# ── Session management ────────────────────────────────────────────────────────

def get_session(user_id: str):
    """
    Get or create a Composio session for the given user_id.

    Sessions are cached for the lifetime of the process.  On failure
    returns None so callers can handle gracefully.

    In the current architecture, user_id == workspace_id.
    """
    if not _ensure_client():
        return None

    if user_id in _sessions:
        return _sessions[user_id]

    try:
        session = _composio_client.create(user_id=user_id)
        _sessions[user_id] = session
        logger.info("Created Composio session for user_id=%s", user_id)
        return session
    except Exception as exc:
        logger.error(
            "Failed to create Composio session for user_id=%s: %s",
            user_id, exc
        )
        return None


def invalidate_session(user_id: str) -> None:
    """
    Remove a cached session so the next call to get_session() creates fresh.
    Useful after a user revokes or adds connections.
    """
    _sessions.pop(user_id, None)


# ── Connection check ─────────────────────────────────────────────────────────

def check_connection(user_id: str, toolkit: str) -> dict:
    """
    Check whether the user has an active connected account for a toolkit.

    Returns:
        {
            "connected": bool,
            "connected_account_id": str | None,
            "status": "connected" | "pending" | "not_found" | "error",
            "error": str | None,
        }
    """
    if not _ensure_client():
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": "Composio client not available",
        }

    try:
        # Composio SDK: list connected accounts for the user, filtered by app
        accounts = _composio_client.connected_accounts.list(
            user_id=user_id,
        )
        # Filter by toolkit name
        for acct in accounts:
            # Account objects have an `app_name` or similar attribute
            acct_app = getattr(acct, "app_name", "") or getattr(acct, "appName", "") or ""
            if acct_app.upper() == toolkit.upper():
                acct_id = getattr(acct, "id", "") or getattr(acct, "connected_account_id", "")
                status_val = getattr(acct, "status", "connected")
                if str(status_val).lower() in ("active", "connected"):
                    return {
                        "connected": True,
                        "connected_account_id": str(acct_id),
                        "status": "connected",
                        "error": None,
                    }
                else:
                    return {
                        "connected": False,
                        "connected_account_id": str(acct_id),
                        "status": "pending",
                        "error": None,
                    }

        return {
            "connected": False,
            "connected_account_id": None,
            "status": "not_found",
            "error": None,
        }

    except Exception as exc:
        logger.error(
            "check_connection failed for user_id=%s toolkit=%s: %s",
            user_id, toolkit, exc
        )
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": str(exc),
        }


# ── Connect Link generation ──────────────────────────────────────────────────

def get_connect_link(
    user_id: str,
    toolkit: str,
    callback_url: str = "",
) -> Optional[str]:
    """
    Generate a Composio Connect Link URL for the given user + toolkit.

    The user clicks this link to complete OAuth / enter an API key.
    After completion, Composio stores the connection server-side and
    (optionally) redirects to callback_url.

    Returns the URL string, or None on failure.
    """
    if not _ensure_client():
        logger.warning("Cannot generate connect link — Composio not available.")
        return None

    try:
        session = get_session(user_id)
        if session is None:
            return None

        # Use Composio's initiate_connection (or equivalent) to get a link
        conn_request = _composio_client.connected_accounts.initiate(
            user_id=user_id,
            app_name=toolkit.upper(),
            **({"callback_url": callback_url} if callback_url else {}),
        )

        # The response object should contain the redirect URL
        url = (
            getattr(conn_request, "redirect_url", None)
            or getattr(conn_request, "redirectUrl", None)
            or getattr(conn_request, "url", None)
        )

        if url:
            logger.info(
                "Generated connect link for user_id=%s toolkit=%s",
                user_id, toolkit
            )
            return str(url)

        logger.warning(
            "Composio returned no redirect URL for user_id=%s toolkit=%s",
            user_id, toolkit
        )
        return None

    except Exception as exc:
        logger.error(
            "get_connect_link failed for user_id=%s toolkit=%s: %s",
            user_id, toolkit, exc
        )
        return None


# ── Tool schema fetching (for future function-calling integration) ────────────

def get_tool_schemas(user_id: str, tool_names: list[str] | None = None) -> list[dict]:
    """
    Fetch Composio tool schemas for the user's session.

    Returns a list of tool definition dicts (Composio format).
    An empty list if unavailable.

    NOT used by tool_executor yet — provided for Phase 3 (LLM function-calling).
    """
    session = get_session(user_id)
    if session is None:
        return []

    try:
        tools = session.tools()
        if tool_names:
            name_set = set(tool_names)
            tools = [t for t in tools if getattr(t, "name", "") in name_set]
        return tools
    except Exception as exc:
        logger.error("get_tool_schemas failed for user_id=%s: %s", user_id, exc)
        return []
