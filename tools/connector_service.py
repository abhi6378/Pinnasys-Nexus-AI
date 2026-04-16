from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from models.contracts import ConnectorAccountSummary, ConnectorContext, ConnectorStatusSummary
from storage import repositories as repo
from tools.composio_client import get_connect_link, list_connected_accounts
from tools.tool_registry import (
    get_toolkit_label,
    get_toolkit_metadata,
    list_toolkits,
    normalize_toolkit_key,
)
from utils.logging_utils import log_event, log_exception
from utils.time_utils import utc_now


logger = logging.getLogger(__name__)
CONNECTOR_STATUS_TTL_SECONDS = int(os.getenv("CONNECTOR_STATUS_TTL_SECONDS", "900") or "900")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat(value: datetime | None) -> str:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized else ""


def _is_stale(last_verified_at: datetime | None) -> bool:
    normalized = _as_utc(last_verified_at)
    if normalized is None:
        return True
    return (utc_now() - normalized).total_seconds() > CONNECTOR_STATUS_TTL_SECONDS


def _cache_get(request_cache: dict | None, key: str):
    if request_cache is None:
        return None
    return request_cache.get(key)


def _cache_set(request_cache: dict | None, key: str, value):
    if request_cache is None:
        return value
    request_cache[key] = value
    return value


def normalize_connector_context(value: Any = None) -> ConnectorContext:
    connector = ConnectorContext.from_value(value)
    normalized_toolkit = normalize_toolkit_key(
        connector.selected_connector_key or connector.selected_toolkit
    )
    connector.selected_toolkit = normalized_toolkit or connector.selected_toolkit
    connector.selected_connector_key = normalized_toolkit or connector.selected_connector_key or connector.selected_toolkit
    if connector.is_auto():
        return ConnectorContext()
    connector.mode = "manual"
    connector.enforce_toolkit = True
    if connector.selected_account_id:
        connector.enforce_account = True
    return connector


def build_connector_context(
    *,
    selected_toolkit: str = "",
    selected_account_id: str = "",
    selected_account_alias: str = "",
    mode: str = "auto",
    source: str = "chat_input",
) -> ConnectorContext:
    return normalize_connector_context(
        {
            "mode": mode,
            "selected_toolkit": selected_toolkit,
            "selected_connector_key": selected_toolkit,
            "selected_account_id": selected_account_id,
            "selected_account_alias": selected_account_alias,
            "enforce_toolkit": bool(selected_toolkit and mode == "manual"),
            "enforce_account": bool(selected_account_id),
            "source": source,
        }
    )


def load_persisted_connector_context(workspace_id: str, db) -> ConnectorContext:
    try:
        row = repo.get_workspace_connector_preference(db, workspace_id)
    except Exception:
        row = None
    if not row:
        return ConnectorContext()
    return normalize_connector_context(
        {
            "mode": getattr(row, "mode", "auto"),
            "selected_toolkit": getattr(row, "selected_toolkit", ""),
            "selected_connector_key": getattr(row, "selected_toolkit", ""),
            "selected_account_id": getattr(row, "selected_account_id", ""),
            "selected_account_alias": getattr(row, "selected_account_alias", ""),
            "source": getattr(row, "source", "persisted_default") or "persisted_default",
        }
    )


def persist_connector_context(workspace_id: str, connector_context: ConnectorContext | dict | None, db) -> ConnectorContext:
    connector = normalize_connector_context(connector_context)
    repo.upsert_workspace_connector_preference(
        db,
        workspace_id,
        mode=connector.mode,
        selected_toolkit=connector.selected_toolkit,
        selected_account_id=connector.selected_account_id,
        selected_account_alias=connector.selected_account_alias,
        source=connector.source or "persisted_default",
    )
    return connector


def _normalize_account_entry(
    toolkit: str,
    value: dict | Any,
    *,
    source: str,
    selected_account_id: str = "",
) -> ConnectorAccountSummary:
    data = dict(value or {})
    account_id = str(data.get("connected_account_id") or data.get("id") or "").strip()
    alias = str(
        data.get("account_alias")
        or data.get("account_label")
        or data.get("display_label")
        or data.get("label")
        or data.get("name")
        or data.get("email")
        or account_id
        or ""
    ).strip()
    last_verified_at = data.get("last_verified_at")
    if isinstance(last_verified_at, str):
        last_verified_text = last_verified_at
        stale = False
    else:
        last_verified_text = _isoformat(last_verified_at)
        stale = _is_stale(last_verified_at)
    return ConnectorAccountSummary(
        toolkit=toolkit.upper(),
        connected_account_id=account_id,
        account_alias=alias or "Connected account",
        display_label=alias or account_id or "Connected account",
        status=str(data.get("status", "connected") or "connected"),
        is_default=bool(data.get("is_default", False)),
        is_selected=bool(account_id and selected_account_id and account_id == selected_account_id),
        source=source,
        last_verified_at=last_verified_text,
        stale=stale,
    )


def _load_local_accounts(
    workspace_id: str,
    toolkit: str,
    db,
    *,
    include_disconnected: bool,
    selected_account_id: str = "",
) -> list[ConnectorAccountSummary]:
    rows = repo.list_tool_connections(db, workspace_id, toolkit=toolkit)
    accounts: list[ConnectorAccountSummary] = []
    seen: set[str] = set()
    for row in rows:
        account_id = str(getattr(row, "connected_account_id", "") or "")
        if account_id in seen:
            continue
        status = str(getattr(row, "status", "") or "pending")
        if not include_disconnected and status != "connected":
            continue
        metadata_json = dict(getattr(row, "metadata_json", {}) or {})
        account = _normalize_account_entry(
            toolkit,
            {
                "connected_account_id": account_id,
                "account_alias": metadata_json.get("account_alias") or getattr(row, "account_label", ""),
                "display_label": getattr(row, "account_label", "") or metadata_json.get("account_alias", ""),
                "status": status,
                "is_default": bool(getattr(row, "is_default", False)),
                "last_verified_at": getattr(row, "last_verified_at", None) or getattr(row, "updated_at", None),
            },
            source="local_cache",
            selected_account_id=selected_account_id,
        )
        seen.add(account_id)
        accounts.append(account)
    accounts.sort(key=lambda item: (item.status != "connected", not item.is_default, item.display_label.lower()))
    return accounts


def _sync_remote_accounts(
    workspace_id: str,
    toolkit: str,
    db,
    *,
    force_refresh: bool,
    selected_account_id: str = "",
) -> list[ConnectorAccountSummary]:
    started = time.perf_counter()
    remote_accounts = list_connected_accounts(workspace_id, toolkit, force_refresh=force_refresh)
    now = utc_now()
    normalized: list[ConnectorAccountSummary] = []
    for index, account in enumerate(remote_accounts):
        summary = _normalize_account_entry(
            toolkit,
            {
                **dict(account or {}),
                "last_verified_at": now,
                "is_default": index == 0,
            },
            source="remote_refresh",
            selected_account_id=selected_account_id,
        )
        normalized.append(summary)
        repo.upsert_tool_connection(
            db,
            workspace_id,
            toolkit=toolkit,
            connected_account_id=summary.connected_account_id,
            status=summary.status,
            tool_name="*",
            auth_mode=str(get_toolkit_metadata(toolkit).get("auth_mode", "oauth2") or "oauth2"),
            account_label=summary.display_label,
            is_default=summary.is_default,
            last_verified_at=now,
            status_updated_at=now,
            metadata_json={"account_alias": summary.account_alias},
        )
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    log_event(
        logger,
        logging.INFO,
        "connector.accounts.remote_refresh",
        workspace_id=workspace_id,
        toolkit=toolkit,
        duration_ms=elapsed,
        account_count=len(normalized),
    )
    return normalized


def list_connector_accounts(
    workspace_id: str,
    toolkit: str,
    db,
    *,
    include_disconnected: bool = False,
    refresh: bool = False,
    request_cache: dict | None = None,
    selected_account_id: str = "",
    allow_remote: bool | None = None,
) -> list[dict]:
    selected_toolkit = normalize_toolkit_key(toolkit)
    if not selected_toolkit:
        return []

    cache_key = (
        f"connector_accounts:{workspace_id}:{selected_toolkit}:{include_disconnected}:"
        f"{refresh}:{selected_account_id}:{allow_remote}"
    )
    cached = _cache_get(request_cache, cache_key)
    if cached is not None:
        return [item.to_dict() if isinstance(item, ConnectorAccountSummary) else dict(item) for item in cached]

    local_accounts = _load_local_accounts(
        workspace_id,
        selected_toolkit,
        db,
        include_disconnected=include_disconnected,
        selected_account_id=selected_account_id,
    )
    should_allow_remote = bool(allow_remote) or refresh
    should_refresh = refresh or not local_accounts
    if selected_account_id and not any(
        account.connected_account_id == selected_account_id for account in local_accounts
    ):
        should_refresh = should_allow_remote
    elif should_allow_remote and any(account.stale for account in local_accounts):
        should_refresh = True

    accounts = local_accounts
    if should_refresh and should_allow_remote:
        try:
            accounts = _sync_remote_accounts(
                workspace_id,
                selected_toolkit,
                db,
                force_refresh=refresh,
                selected_account_id=selected_account_id,
            )
        except Exception as exc:
            log_exception(
                logger,
                "connector.accounts.remote_refresh_failed",
                exc,
                workspace_id=workspace_id,
                toolkit=selected_toolkit,
            )
            accounts = local_accounts

    if not include_disconnected:
        accounts = [account for account in accounts if account.status == "connected"]

    if not accounts:
        log_event(
            logger,
            logging.INFO,
            "connector.accounts.local_miss",
            workspace_id=workspace_id,
            toolkit=selected_toolkit,
            remote_attempted=bool(should_refresh and should_allow_remote),
        )
    else:
        log_event(
            logger,
            logging.INFO,
            "connector.accounts.local_hit",
            workspace_id=workspace_id,
            toolkit=selected_toolkit,
            account_count=len(accounts),
            remote_attempted=bool(should_refresh and should_allow_remote),
        )
    _cache_set(request_cache, cache_key, accounts)
    return [account.to_dict() for account in accounts]


def _build_status_summary(
    workspace_id: str,
    toolkit: str,
    db,
    *,
    connector_context: ConnectorContext,
    refresh: bool = False,
    request_cache: dict | None = None,
    include_connect_url: bool = False,
    allow_remote: bool = False,
) -> ConnectorStatusSummary:
    selected_toolkit = normalize_toolkit_key(toolkit)
    toolkit_meta = get_toolkit_metadata(selected_toolkit)
    accounts = [
        ConnectorAccountSummary.from_value(item)
        for item in list_connector_accounts(
            workspace_id,
            selected_toolkit,
            db,
            include_disconnected=True,
            refresh=refresh,
            request_cache=request_cache,
            selected_account_id=connector_context.selected_account_id,
            allow_remote=allow_remote,
        )
    ]
    connected_accounts = [account for account in accounts if account.status == "connected"]
    account_count = len(connected_accounts)
    effective_account = next(
        (account for account in connected_accounts if account.connected_account_id == connector_context.selected_account_id),
        None,
    )
    validation_status = "ok"
    stale_selection = False
    status_reason = ""
    if connector_context.selected_account_id and not effective_account:
        stale_selection = True
        validation_status = "stale_account"
        status_reason = (
            f"The selected {get_toolkit_label(selected_toolkit)} account is unavailable. "
            "Please reconnect or choose another account."
        )
    elif connector_context.mode == "manual" and account_count > 1 and not connector_context.selected_account_id:
        validation_status = "account_required"
        status_reason = (
            f"Select which {get_toolkit_label(selected_toolkit)} account to use before a live action runs."
        )
    if not effective_account and len(connected_accounts) == 1:
        effective_account = connected_accounts[0]

    connect_url = None
    if include_connect_url and not connected_accounts:
        connect_url = get_connect_link(workspace_id, selected_toolkit)

    last_verified_values = [
        account.last_verified_at
        for account in accounts
        if account.last_verified_at
    ]
    summary = ConnectorStatusSummary(
        toolkit=selected_toolkit,
        connector_key=selected_toolkit,
        label=get_toolkit_label(selected_toolkit),
        slug=str(toolkit_meta.get("slug", "") or ""),
        connected=bool(connected_accounts),
        status="connected" if connected_accounts else "not_connected",
        source="local_cache" if accounts and all(account.source == "local_cache" for account in accounts) else ("remote_refresh" if accounts else "local_cache"),
        validation_status=validation_status,
        status_reason=status_reason,
        stale=bool(accounts and all(account.stale for account in accounts)),
        stale_selection=stale_selection,
        account_required=validation_status == "account_required",
        account_count=account_count,
        selected_account_id=connector_context.selected_account_id,
        selected_account_alias=connector_context.selected_account_alias,
        effective_account_id=effective_account.connected_account_id if effective_account else "",
        effective_account_alias=effective_account.account_alias if effective_account else "",
        connect_url=connect_url,
        setup_message=str(toolkit_meta.get("setup_message", "") or ""),
        connection_mode=str(toolkit_meta.get("connection_mode", "") or ""),
        auth_mode=str(toolkit_meta.get("auth_mode", "") or ""),
        last_verified_at=max(last_verified_values) if last_verified_values else "",
        accounts=accounts,
    )
    return summary


def get_connector_status_summary(
    workspace_id: str,
    toolkit: str,
    db,
    *,
    connector_context: ConnectorContext | dict | None = None,
    refresh: bool = False,
    request_cache: dict | None = None,
    include_connect_url: bool = False,
    allow_remote: bool = False,
) -> ConnectorStatusSummary:
    selected_toolkit = normalize_toolkit_key(toolkit)
    if not selected_toolkit:
        return ConnectorStatusSummary(validation_status="invalid_toolkit")
    cache_key = (
        f"connector_status:{workspace_id}:{selected_toolkit}:{refresh}:{include_connect_url}:"
        f"{allow_remote}:{ConnectorContext.from_value(connector_context).selected_account_id}"
    )
    cached = _cache_get(request_cache, cache_key)
    if cached is not None:
        return ConnectorStatusSummary.from_value(cached)
    summary = _build_status_summary(
        workspace_id,
        selected_toolkit,
        db,
        connector_context=normalize_connector_context(connector_context),
        refresh=refresh,
        request_cache=request_cache,
        include_connect_url=include_connect_url,
        allow_remote=allow_remote,
    )
    return _cache_set(request_cache, cache_key, summary)


def list_workspace_connectors(
    workspace_id: str,
    db,
    *,
    refresh: bool = False,
    request_cache: dict | None = None,
    selected_toolkit: str = "",
    include_connect_url: bool = False,
) -> list[dict]:
    normalized_selected_toolkit = normalize_toolkit_key(selected_toolkit)
    connector_rows: list[dict] = []
    for toolkit in list_toolkits():
        summary = get_connector_status_summary(
            workspace_id,
            toolkit,
            db,
            connector_context=build_connector_context(
                selected_toolkit=normalized_selected_toolkit,
                selected_account_id="",
                mode="manual" if normalized_selected_toolkit else "auto",
                source="system_inferred",
            ),
            refresh=refresh and toolkit == normalized_selected_toolkit,
            request_cache=request_cache,
            include_connect_url=include_connect_url and toolkit == normalized_selected_toolkit,
            allow_remote=toolkit == normalized_selected_toolkit,
        )
        connector_rows.append(summary.to_dict())
    return connector_rows


def validate_connector_context(
    connector_context: ConnectorContext | dict | None,
    workspace_id: str,
    db,
    *,
    request_cache: dict | None = None,
    refresh: bool = False,
) -> tuple[ConnectorContext, ConnectorStatusSummary, str]:
    normalized = normalize_connector_context(connector_context)
    if normalized.is_auto():
        return ConnectorContext(), ConnectorStatusSummary(), ""

    toolkit_key = normalize_toolkit_key(normalized.selected_toolkit or normalized.selected_connector_key)
    if not toolkit_key or not get_toolkit_metadata(toolkit_key):
        normalized.validation_status = "invalid_toolkit"
        return normalized, ConnectorStatusSummary(validation_status="invalid_toolkit"), (
            f"Selected connector '{normalized.selected_toolkit or normalized.selected_connector_key}' is not supported."
        )

    normalized.selected_toolkit = toolkit_key
    normalized.selected_connector_key = toolkit_key
    normalized.display_label = get_toolkit_label(toolkit_key)

    summary = get_connector_status_summary(
        workspace_id,
        toolkit_key,
        db,
        connector_context=normalized,
        refresh=refresh,
        request_cache=request_cache,
        include_connect_url=not normalized.connected,
        allow_remote=bool(normalized.selected_account_id) or refresh,
    )
    normalized.validation_status = summary.validation_status
    normalized.stale_selection = summary.stale_selection
    normalized.status_reason = summary.status_reason
    normalized.available_account_count = summary.account_count
    normalized.connected = summary.connected
    normalized.effective_account_id = summary.effective_account_id
    normalized.effective_account_alias = summary.effective_account_alias

    if summary.effective_account_id and not normalized.selected_account_id and summary.account_count == 1:
        normalized.selected_account_id = summary.effective_account_id
        normalized.selected_account_alias = summary.effective_account_alias
        normalized.enforce_account = True
    elif summary.stale_selection and summary.account_count == 1 and summary.effective_account_id:
        normalized.selected_account_id = summary.effective_account_id
        normalized.selected_account_alias = summary.effective_account_alias
        normalized.stale_selection = False
        normalized.validation_status = "ok"
        normalized.status_reason = ""

    if summary.validation_status == "invalid_toolkit":
        return normalized, summary, f"Selected connector '{toolkit_key}' is not supported."
    return normalized, summary, ""
