"""
tools/composio_client.py  —  Composio SDK wrapper for tool schemas,
connection checks, connect links, and action execution.

This module is the only place that talks directly to the Composio SDK.
All callers use the helpers below so the rest of the app can stay insulated
from SDK changes and optional dependency failures.

Updated for Composio SDK 1.0 (composio >= 0.11 / 1.0.0-rc series):
  - `Action` and `App` enums have been removed; actions and apps are plain
    strings (e.g. "GMAIL_SEND_EMAIL", "gmail").
  - `composio_openai.ComposioToolSet` has been replaced by the unified
    `composio.Composio` client with `composio.sdk.OpenAIProvider`.
  - Entity-centric helpers (get_entity / get_connections) are replaced by
    the `client.connected_accounts` and `client.tools` resource APIs.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv

from tools.tool_registry import get_toolkit_app_enum, get_toolkit_runtime_config, get_toolkit_slug

load_dotenv()

logger = logging.getLogger(__name__)

ENTITY_PREFIX = os.getenv("COMPOSIO_ENTITY_ID_PREFIX", "")

# ── Module-level SDK state (lazily initialised) ──────────────────────────────
_imports_loaded = False
_imports_available = False
_import_error = ""
_api_key = ""

# The single Composio client instance, cached per entity_id.
_clients: dict[str, Any] = {}
# A default (entity-less) Composio client for catalog / admin operations.
_default_client: Any = None
_schema_cache: dict[tuple[str, str], dict] = {}


def _entity_id(workspace_id: str) -> str:
    """Build the Composio entity id for a workspace."""
    return f"{ENTITY_PREFIX}{workspace_id}" if ENTITY_PREFIX else workspace_id


def _resolve_callback_url(callback_url: str = "") -> str:
    """Return the callback URL used for Composio connection redirects."""
    if callback_url and callback_url.strip():
        return callback_url.strip()
    for env_key in ("COMPOSIO_CONNECT_CALLBACK_URL", "COMPOSIO_CALLBACK_URL"):
        value = os.getenv(env_key, "").strip()
        if value:
            return value
    return ""


# ── Lazy SDK initialisation ──────────────────────────────────────────────────

def _load_sdk_imports() -> bool:
    """Load Composio SDK imports lazily so the app can boot without them.

    In Composio SDK 1.0 the entry point is ``composio.Composio``.  The old
    ``Action``/``App`` enums and the separate ``composio_openai`` package no
    longer exist.
    """
    global _imports_loaded, _imports_available, _import_error
    global _api_key, _default_client

    if _imports_loaded:
        return _imports_available

    _imports_loaded = True
    _api_key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not _api_key:
        _import_error = "COMPOSIO_API_KEY not set"
        logger.warning("COMPOSIO_API_KEY not set; Composio integrations are disabled.")
        return False

    try:
        from composio import Composio  # noqa: F401 — used via _default_client

        _default_client = Composio(api_key=_api_key)
        _imports_available = True
        logger.info("Composio SDK 1.0 client initialised successfully.")
        return True
    except Exception as exc:
        _import_error = str(exc)
        logger.warning(
            "Composio SDK imports unavailable; install composio to enable tools: %s",
            exc,
        )
        return False


def is_available() -> bool:
    """Return True when the Composio SDK can be used for tool operations."""
    return _load_sdk_imports()


# ── Payload helpers ──────────────────────────────────────────────────────────

def _normalize_sdk_payload(value: Any) -> Any:
    """Convert SDK objects into JSON-serializable Python structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {key: _normalize_sdk_payload(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_sdk_payload(item) for item in value]
    if hasattr(value, "model_dump"):
        return _normalize_sdk_payload(value.model_dump())
    if hasattr(value, "dict"):
        return _normalize_sdk_payload(value.dict())

    data: dict[str, Any] = {}
    for attr in dir(value):
        if attr.startswith("_"):
            continue
        try:
            attr_value = getattr(value, attr)
        except Exception:
            continue
        if callable(attr_value):
            continue
        data[attr] = _normalize_sdk_payload(attr_value)
    return data or str(value)


def _coerce_sequence(value: Any) -> list[Any]:
    """Normalize SDK list-like responses into a plain list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    items = getattr(value, "items", None)
    if isinstance(items, list):
        return items
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        for key in ("items", "data", "tools", "connections"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        return []
    try:
        return list(value)
    except TypeError:
        return []


# ── Client management ────────────────────────────────────────────────────────

def _get_client(user_id: str = "", force_refresh: bool = False) -> Any | None:
    """Return a Composio client instance.

    In SDK 1.0 the client is not scoped to an entity at creation time — the
    user_id / entity_id is passed per-call.  We still cache client instances
    to avoid re-creating them on every request.
    """
    if not is_available():
        return None

    if not user_id:
        return _default_client

    entity_id = _entity_id(user_id)
    if force_refresh:
        _clients.pop(entity_id, None)

    if entity_id not in _clients:
        try:
            from composio import Composio
            _clients[entity_id] = Composio(api_key=_api_key)
            logger.info("Created Composio client for entity_id=%s", entity_id)
        except Exception as exc:
            logger.error(
                "Failed to create Composio client for entity_id=%s: %s",
                entity_id, exc,
            )
            return None

    return _clients[entity_id]


def invalidate_session(user_id: str) -> None:
    """Backward-compatible cache invalidation helper."""
    _clients.pop(_entity_id(user_id), None)
    stale_keys = [cache_key for cache_key in _schema_cache if cache_key[0] == _entity_id(user_id)]
    for cache_key in stale_keys:
        _schema_cache.pop(cache_key, None)


def get_session(user_id: str, force_refresh: bool = False) -> Any | None:
    """Backward-compatible alias for the cached Composio client."""
    return _get_client(user_id, force_refresh=force_refresh)


# ── Resolution helpers ────────────────────────────────────────────────────────

def _resolve_action(tool_name: str) -> str | None:
    """Resolve a tool/action name to the Composio action slug.

    In SDK 1.0 actions are plain strings — no enum lookup is needed.
    """
    return tool_name if tool_name else None


def _resolve_app(toolkit: str) -> str | None:
    """Resolve an internal toolkit key to the Composio toolkit slug.

    In SDK 1.0 apps are plain strings — no enum lookup is needed.
    """
    return get_toolkit_slug(toolkit)


# ── Connection-link helpers ──────────────────────────────────────────────────

def _extract_redirect_url(connection_request: Any) -> Optional[str]:
    """Read a redirect URL from Composio SDK response objects."""
    for attr in ("redirectUrl", "redirect_url", "url", "link"):
        value = getattr(connection_request, attr, None)
        if value:
            return str(value)

    if isinstance(connection_request, dict):
        for key in ("redirectUrl", "redirect_url", "url", "link"):
            value = connection_request.get(key)
            if value:
                return str(value)
    return None


# ── Auth-config lookup ────────────────────────────────────────────────────────

def _find_auth_config_id(client: Any, toolkit_slug: str) -> Optional[str]:
    """Look up the default auth-config id for a toolkit.

    The new SDK flow requires an ``auth_config_id`` when initiating a
    connection.  We list the configs for the toolkit and pick the first
    usable one.

    If no config exists, we attempt to auto-create one using Composio
    managed auth so new deployments don't require manual Composio
    dashboard setup.
    """
    try:
        configs = client.auth_configs.list(toolkit_slug=toolkit_slug)
        config_items = _coerce_sequence(configs)
        for cfg in config_items:
            cfg_id = getattr(cfg, "id", None) or (cfg.get("id") if isinstance(cfg, dict) else None)
            if cfg_id:
                return str(cfg_id)
    except Exception as exc:
        logger.warning(
            "Could not list auth_configs for toolkit_slug=%s: %s",
            toolkit_slug, exc,
        )

    # No config found — try to auto-create with Composio managed auth.
    return _auto_create_auth_config(client, toolkit_slug)


def _auto_create_auth_config(client: Any, toolkit_slug: str) -> Optional[str]:
    """Attempt to create a Composio managed auth config for a toolkit.

    This is called automatically when no auth config exists.  Toolkits that
    don't support Composio managed auth (e.g. Twitter, Tavily) will fail
    gracefully and return None — the caller will show an appropriate
    "setup required" message.
    """
    try:
        result = client.auth_configs.create(
            toolkit=toolkit_slug,
            options={
                "type": "use_composio_managed_auth",
                "name": f"auto-{toolkit_slug}",
            },
        )
        new_id = getattr(result, "id", None)
        if not new_id and isinstance(result, dict):
            new_id = result.get("id")
        if new_id:
            logger.info(
                "Auto-created Composio managed auth config for %s: %s",
                toolkit_slug, new_id,
            )
            return str(new_id)
    except Exception as exc:
        logger.warning(
            "Could not auto-create auth config for %s: %s",
            toolkit_slug, exc,
        )
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def get_toolkit_auth_details(toolkit: str) -> dict:
    """
    Return lightweight availability metadata for a toolkit.

    The newer Composio SDK flow centers on entity connections rather than the
    older auth-config inspection API, so this helper now reports only what the
    rest of the app needs to explain connect-link availability gracefully.
    """
    toolkit_slug = get_toolkit_slug(toolkit)
    app_name = get_toolkit_app_enum(toolkit)
    available = is_available()
    toolkit_config = get_toolkit_runtime_config(toolkit)

    if not available:
        return {
            "available": False,
            "toolkit_slug": toolkit_slug,
            "managed_auth_available": False,
            "supported_auth_schemes": [],
            "auth_config_count": 0,
            "has_usable_auth_config": False,
            "preferred_auth_config_id": None,
            "reason": _import_error or str(toolkit_config.get("setup_message") or "Composio client not available"),
            "error": _import_error or str(toolkit_config.get("setup_message") or "Composio client not available"),
        }

    if not toolkit_slug or not app_name:
        reason = f"No Composio app mapping configured for toolkit '{toolkit}'"
        return {
            "available": False,
            "toolkit_slug": toolkit_slug,
            "managed_auth_available": False,
            "supported_auth_schemes": [],
            "auth_config_count": 0,
            "has_usable_auth_config": False,
            "preferred_auth_config_id": None,
            "reason": reason,
            "error": reason,
        }

    return {
        "available": True,
        "toolkit_slug": toolkit_slug,
        "managed_auth_available": toolkit_config.get("connection_mode", "managed_account") == "managed_account",
        "supported_auth_schemes": [],
        "auth_config_count": 1,
        "has_usable_auth_config": True,
        "preferred_auth_config_id": None,
        "reason": str(toolkit_config.get("setup_message") or "Connection flow available."),
        "error": None,
    }


def list_connected_accounts(user_id: str, toolkit: str = "", force_refresh: bool = False) -> list[dict]:
    if not is_available():
        return []

    toolkit_slug = get_toolkit_slug(toolkit) if toolkit else ""
    client = _get_client(user_id, force_refresh=force_refresh)
    if client is None:
        return []

    entity_id = _entity_id(user_id)
    try:
        low_level = client.client.connected_accounts
        try:
            if toolkit_slug:
                response = low_level.list(user_ids=[entity_id], toolkit_slugs=[toolkit_slug])
            else:
                response = low_level.list(user_ids=[entity_id])
        except TypeError:
            response = low_level.list()
        connections = _coerce_sequence(response)
        normalized: list[dict] = []
        for connection in connections:
            raw_toolkit = getattr(connection, "toolkit", None)
            toolkit_name = ""
            if raw_toolkit is not None:
                toolkit_name = str(getattr(raw_toolkit, "slug", raw_toolkit) or "").lower()
            if not toolkit_name:
                toolkit_name = str(
                    getattr(connection, "appName", "")
                    or getattr(connection, "toolkit_slug", "")
                    or ""
                ).lower()
            if toolkit_slug and toolkit_name and toolkit_name != toolkit_slug:
                continue
            status = str(getattr(connection, "status", "") or "").upper()
            account_id = (
                getattr(connection, "id", None)
                or getattr(connection, "connectedAccountId", None)
                or getattr(connection, "connected_account_id", None)
            )
            normalized.append(
                {
                    "connected_account_id": str(account_id or ""),
                    "toolkit_slug": toolkit_name,
                    "status": "connected" if status == "ACTIVE" else status.lower() or "unknown",
                    "account_alias": str(
                        getattr(connection, "name", None)
                        or getattr(connection, "email", None)
                        or getattr(connection, "clientUniqueUserId", None)
                        or account_id
                        or ""
                    ),
                }
            )
        return normalized
    except Exception as exc:
        logger.error(
            "list_connected_accounts failed for user_id=%s toolkit=%s: %s",
            user_id, toolkit, exc,
        )
        return []


def check_connection(
    user_id: str,
    toolkit: str,
    force_refresh: bool = False,
    preferred_account_id: str = "",
) -> dict:
    """
    Check whether a workspace has an ACTIVE connection for the requested toolkit.

    Uses the SDK 1.0 ``connected_accounts`` resource to list connections and
    filter by toolkit slug and status.
    """
    if not is_available():
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": _import_error or "Composio client not available",
        }

    toolkit_slug = get_toolkit_slug(toolkit)
    if not toolkit_slug:
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": f"No canonical Composio toolkit slug mapping for '{toolkit}'",
        }

    client = _get_client(user_id, force_refresh=force_refresh)
    if client is None:
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": _import_error or "Composio client not available",
        }

    entity_id = _entity_id(user_id)

    try:
        # SDK 1.0: the high-level wrapper has no .list() method.
        # Use the low-level REST client at client.client.connected_accounts
        # which accepts plural filter params: user_ids, toolkit_slugs, statuses.
        low_level = client.client.connected_accounts
        try:
            response = low_level.list(
                user_ids=[entity_id],
                toolkit_slugs=[toolkit_slug],
            )
        except TypeError:
            # Fallback: list without filters and match locally.
            response = low_level.list()

        # The response is typically a pydantic model with an .items attribute,
        # or a list-like.  Normalise it into a plain list.
        connections = _coerce_sequence(response)

        for connection in connections:
            # Extract the toolkit slug. The SDK 1.0 returns an ItemToolkit
            # object (with a .slug attribute) rather than a plain string.
            raw_toolkit = getattr(connection, "toolkit", None)
            toolkit_name = ""
            if raw_toolkit is not None:
                toolkit_name = str(getattr(raw_toolkit, "slug", raw_toolkit) or "").lower()
            if not toolkit_name:
                toolkit_name = str(
                    getattr(connection, "appName", "")
                    or getattr(connection, "toolkit_slug", "")
                    or ""
                ).lower()

            status = str(getattr(connection, "status", "") or "").upper()

            if toolkit_name and toolkit_name != toolkit_slug:
                continue

            account_id = (
                getattr(connection, "id", None)
                or getattr(connection, "connectedAccountId", None)
                or getattr(connection, "connected_account_id", None)
            )
            if preferred_account_id and str(account_id or "") != str(preferred_account_id):
                continue
            if status == "ACTIVE":
                account_label = str(
                    getattr(connection, "name", None)
                    or getattr(connection, "email", None)
                    or getattr(connection, "clientUniqueUserId", None)
                    or account_id
                    or ""
                )
                return {
                    "connected": True,
                    "connected_account_id": str(account_id) if account_id else "active",
                    "account_label": account_label,
                    "is_default": not preferred_account_id,
                    "status": "connected",
                    "error": None,
                }
            # Don't return early on non-ACTIVE connections — keep scanning
            # in case a later connection IS active.

        if preferred_account_id:
            return {
                "connected": False,
                "connected_account_id": None,
                "status": "not_found",
                "error": f"Selected account '{preferred_account_id}' is not connected.",
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
            user_id, toolkit, exc,
        )
        return {
            "connected": False,
            "connected_account_id": None,
            "status": "error",
            "error": str(exc),
        }


def get_connect_link(user_id: str, toolkit: str, callback_url: str = "") -> Optional[str]:
    """
    Initiate a Composio connection flow and return the redirect URL.

    SDK 1.0 uses ``client.connected_accounts.initiate(user_id, auth_config_id, ...)``
    which requires an ``auth_config_id``.  We look it up from the toolkit slug.
    """
    if not is_available():
        logger.warning("Cannot generate connect link; Composio is unavailable.")
        return None

    toolkit_config = get_toolkit_runtime_config(toolkit)
    if toolkit_config.get("connection_mode") == "custom_key":
        logger.warning("Cannot generate OAuth connect link for custom-key toolkit=%s", toolkit)
        return None

    toolkit_slug = _resolve_app(toolkit)
    if not toolkit_slug:
        logger.error(
            "Cannot generate connect link; no toolkit slug for toolkit=%s", toolkit,
        )
        return None

    client = _get_client(user_id)
    if client is None:
        return None

    try:
        # Find the auth config for this toolkit.
        auth_config_id = _find_auth_config_id(client, toolkit_slug)
        if not auth_config_id:
            logger.error(
                "Cannot generate connect link; no auth_config found for toolkit_slug=%s",
                toolkit_slug,
            )
            return None

        entity_id = _entity_id(user_id)
        redirect_url = _resolve_callback_url(callback_url)

        # SDK 1.0: initiate a connected-account flow.
        # allow_multiple=True permits reconnecting even when existing
        # accounts are present for this user + auth config pair.
        kwargs: dict[str, Any] = {"allow_multiple": True}
        if redirect_url:
            kwargs["callback_url"] = redirect_url

        request = client.connected_accounts.initiate(
            user_id=entity_id,
            auth_config_id=auth_config_id,
            **kwargs,
        )
        return _extract_redirect_url(request)
    except Exception as exc:
        logger.error(
            "get_connect_link failed for user_id=%s toolkit=%s: %s",
            user_id, toolkit, exc,
        )
        return None


def get_tool_schemas(user_id: str, tool_names: list[str] | None = None) -> list[dict]:
    """
    Fetch OpenAI-formatted Composio tool schemas for a workspace.

    SDK 1.0 uses ``client.tools.get(user_id, tools=[...])`` which returns an
    ``OpenAIToolCollection`` (a list-like of tool dicts).
    """
    if not is_available():
        return []

    client = _get_client(user_id)
    if client is None:
        return []

    actions = [name for name in (tool_names or []) if name]
    if not actions:
        return []

    entity_id = _entity_id(user_id)

    try:
        tools = _coerce_sequence(
            client.tools.get(user_id=entity_id, tools=actions)
        )
        schemas: list[dict] = []
        for tool in tools:
            normalized = _normalize_sdk_payload(tool)
            if isinstance(normalized, dict):
                tool_name = str(
                    normalized.get("function", {}).get("name")
                    or normalized.get("name")
                    or normalized.get("slug")
                    or ""
                )
                if tool_name:
                    _schema_cache[(entity_id, tool_name)] = normalized
                schemas.append(normalized)
        return schemas
    except Exception as exc:
        logger.error("get_tool_schemas failed for user_id=%s: %s", user_id, exc)
        return []


def get_live_tool_schema(user_id: str, tool_name: str, force_refresh: bool = False) -> dict:
    if not tool_name:
        return {}
    entity_id = _entity_id(user_id or "__catalog__")
    cache_key = (entity_id, tool_name)
    if not force_refresh and cache_key in _schema_cache:
        return dict(_schema_cache[cache_key])
    schemas = get_tool_schemas(user_id or "__catalog__", [tool_name])
    for schema in schemas:
        schema_name = str(
            schema.get("function", {}).get("name")
            or schema.get("name")
            or schema.get("slug")
            or ""
        )
        if schema_name == tool_name:
            return dict(schema)
    return {}


def validate_tool_slug(tool_name: str) -> dict:
    """
    Validate a tool/action name by attempting to fetch its Composio schema.
    """
    if not is_available():
        return {
            "available": False,
            "exists": False,
            "error": _import_error or "Composio client not available",
        }

    try:
        schemas = get_tool_schemas("__catalog__", [tool_name])
        return {
            "available": True,
            "exists": bool(schemas),
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "exists": False,
            "error": str(exc),
        }


def execute_tool(
    user_id: str,
    tool_name: str,
    arguments: dict | None = None,
    connected_account_id: str | None = None,
) -> dict:
    """
    Execute a Composio action directly for a workspace entity.

    SDK 1.0 uses ``client.tools.execute(slug, arguments, user_id=...)``
    instead of the old ``toolset.execute_action(action, params)`` pattern.

    Parameters:
        user_id              — The workspace / entity id.
        tool_name            — Composio action slug (e.g. "GMAIL_FETCH_EMAILS").
        arguments            — Dict of parameters to send to the tool.
        connected_account_id — If known, the Composio connected-account id
                               obtained from ``check_connection``.  Passing this
                               avoids the "toolkit version not specified" error
                               that occurs in manual (non-framework) execution.
    """
    client = _get_client(user_id)
    if client is None:
        raise RuntimeError(_import_error or "Composio client not available")

    action = _resolve_action(tool_name)
    if not action:
        raise ValueError(f"Invalid tool name: {tool_name}")

    params = arguments or {}
    entity_id = _entity_id(user_id)

    try:
        # SDK 1.0: tools.execute(slug, arguments, ...)
        # When executing outside a framework integration (OpenAI/LangChain
        # wrappers), the SDK requires either:
        #   - a specific ``version`` string, or
        #   - ``connected_account_id`` so the SDK can resolve the version, or
        #   - ``dangerously_skip_version_check=True`` to bypass the check.
        # We pass both connected_account_id (when available) AND the skip
        # flag as a safety net so execution never fails on version lookup.
        exec_kwargs: dict[str, Any] = {
            "user_id": entity_id,
            "dangerously_skip_version_check": True,
        }
        if connected_account_id:
            exec_kwargs["connected_account_id"] = connected_account_id

        result = client.tools.execute(
            slug=action,
            arguments=params,
            **exec_kwargs,
        )
    except Exception as exc:
        logger.error(
            "execute_tool failed for user_id=%s tool_name=%s: %s",
            user_id, tool_name, exc,
        )
        raise

    normalized = _normalize_sdk_payload(result)
    if isinstance(normalized, dict):
        return {
            "data": normalized.get("data", normalized),
            "error": normalized.get("error"),
            "raw": normalized,
        }

    return {
        "data": normalized,
        "error": None,
        "raw": normalized,
    }
