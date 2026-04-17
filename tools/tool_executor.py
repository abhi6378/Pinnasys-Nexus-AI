"""
tools/tool_executor.py  —  Validates, checks connections, logs, and (in future)
executes Composio tool calls.

This is the layer that handler.py / executor.py will call in a later mission
when tool-calling is wired into the chat flow. For now it is STANDALONE — no
existing code imports it, so backward compatibility is guaranteed.

Execution contract (per call):
  1. Validate that tool_name exists in the registry.
  2. Validate that the requesting agent is allowed to use it.
  3. Validate that the tool exists in the Composio catalog when the SDK is available.
  4. Validate required parameters after aliases/defaults are applied.
  5. Check whether the workspace/user has a live connection for the toolkit.
  6. If not connected  → return {"status": "connect_required", ...} + log.
  7. If connected      → execute the tool via Composio + log result.
  8. Every attempt (success, failure, connect_required, validation_error)
     produces exactly one ToolCallLog row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Optional

try:
    from sqlalchemy.orm import Session as DBSession
except Exception:  # pragma: no cover - fallback for constrained test environments
    DBSession = object

try:
    from tools.tool_registry import (
        get_tool,
        get_tool_approval_requirement,
        get_tool_idempotency_fields,
        get_tool_schema,
        get_toolkit_runtime_config,
        is_agent_allowed,
        normalize_tool_input,
        validate_tool_input,
    )
except ImportError:
    from tools.tool_registry import get_tool, is_agent_allowed

    def normalize_tool_input(tool_name: str, input_args: dict | None) -> dict:
        tool_entry = get_tool(tool_name) or {}
        normalized = dict(input_args or {})
        for source_key, target_key in tool_entry.get("param_aliases", {}).items():
            if source_key in normalized and target_key not in normalized:
                normalized[target_key] = normalized.pop(source_key)
        for key, value in tool_entry.get("default_params", {}).items():
            normalized.setdefault(key, value)
        return normalized

    def validate_tool_input(tool_name: str, input_args: dict | None) -> tuple[dict, list[str]]:
        return normalize_tool_input(tool_name, input_args), []

    def get_tool_schema(tool_name: str) -> dict:
        tool_entry = get_tool(tool_name) or {}
        return dict(tool_entry.get("schema", {}))

    def get_tool_approval_requirement(tool_name: str):
        return type(
            "ApprovalRequirement",
            (),
            {"required": False, "risk_level": "low", "reason": "", "mode": "auto", "to_dict": lambda self: {
                "required": False,
                "risk_level": "low",
                "reason": "",
                "categories": [],
                "mode": "auto",
            }},
        )()

    def get_tool_idempotency_fields(tool_name: str) -> tuple[str, ...]:
        return ()

    def get_toolkit_runtime_config(toolkit: str) -> dict:
        return {}
from tools.composio_client import (
    is_available,
    check_connection,
    get_connect_link,
    get_live_tool_schema,
    get_toolkit_auth_details,
    validate_tool_slug,
    execute_tool,
)
from models.tool_call_logs import ToolCallLogModel
from models.tool_connections import ToolConnectionModel
from models.pending_tool_requests import PendingToolRequestModel
from models.tool_idempotency_records import ToolIdempotencyRecordModel
try:
    from storage import repositories as repo
except Exception:  # pragma: no cover - isolated unit tests may stub model modules only
    repo = None
from utils.logging_utils import log_event, log_exception
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)
CONNECTOR_STATUS_TTL_SECONDS = int(os.getenv("CONNECTOR_STATUS_TTL_SECONDS", "900") or "900")


def _can_use_repo_helpers(db: DBSession) -> bool:
    return repo is not None and hasattr(db, "bind")


# ── Result builders ───────────────────────────────────────────────────────────

def _ok(output: dict, duration_ms: float = 0.0) -> dict:
    return {
        "status": "success",
        "output": output,
        "error": None,
        "duration_ms": duration_ms,
    }


def _fail(status: str, error: str, duration_ms: float = 0.0) -> dict:
    return {
        "status": status,
        "output": None,
        "error": error,
        "duration_ms": duration_ms,
    }


def _connect_required(
    toolkit: str,
    connect_url: Optional[str],
    resume_token: str,
    error: Optional[str] = None,
) -> dict:
    if connect_url:
        status = "connect_required"
    else:
        status = "auth_unavailable"

    return {
        "status": status,
        "output": None,
        "error": error if error else (
            "Integration unavailable. Please try again later."
            if not connect_url else None
        ),
        "toolkit": toolkit,
        "connect_url": connect_url,
        "resume_token": resume_token if connect_url else "",
    }


def _build_auth_unavailable_error(toolkit: str, auth_details: dict, conn_info: dict) -> str:
    """Explain why a connection link cannot be generated for this toolkit."""
    if conn_info.get("error"):
        return str(conn_info["error"])

    toolkit_config = get_toolkit_runtime_config(toolkit)
    custom_msg = toolkit_config.get("setup_message")
    if custom_msg:
        return str(custom_msg)

    if not auth_details:
        return (
            f"The {toolkit} integration is not yet configured. "
            f"An administrator needs to set up auth credentials in the Composio "
            f"dashboard for this toolkit."
        )

    if auth_details.get("reason"):
        return str(auth_details["reason"])

    return (
        f"The {toolkit} integration is currently unavailable. "
        f"Please check the Composio dashboard to verify the toolkit is configured."
    )


def _extract_parameter_schema(live_schema: dict) -> dict:
    if not isinstance(live_schema, dict):
        return {}
    function_block = live_schema.get("function", {})
    parameters = function_block.get("parameters")
    if isinstance(parameters, dict):
        return parameters
    schema = live_schema.get("schema")
    if isinstance(schema, dict):
        return schema
    return {}


def _validate_against_live_schema(input_args: dict, live_schema: dict) -> list[str]:
    errors: list[str] = []
    parameter_schema = _extract_parameter_schema(live_schema)
    if not parameter_schema:
        return errors

    required = list(parameter_schema.get("required") or [])
    properties = dict(parameter_schema.get("properties") or {})
    for key in required:
        if key not in input_args:
            errors.append(f"Missing required parameter: {key}")
    for key, spec in properties.items():
        if key not in input_args:
            continue
        value = input_args[key]
        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"Parameter '{key}' must be a string.")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"Parameter '{key}' must be an integer.")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"Parameter '{key}' must be a list.")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"Parameter '{key}' must be an object.")
    return errors


def _get_runtime_schema_cache(context_json: dict | None) -> dict:
    if not isinstance(context_json, dict):
        return {}
    return context_json.setdefault("tool_schema_cache", {})


def _get_cached_live_schema(context_json: dict | None, cache_key: str) -> dict:
    schema_cache = _get_runtime_schema_cache(context_json)
    cached = schema_cache.get(cache_key)
    if isinstance(cached, dict):
        return dict(cached)
    return {}


def _set_cached_live_schema(context_json: dict | None, cache_key: str, schema: dict) -> dict:
    schema_cache = _get_runtime_schema_cache(context_json)
    schema_cache[cache_key] = dict(schema or {})
    return dict(schema or {})


def _serialize_idempotency_payload(tool_name: str, input_args: dict, context_json: dict | None = None) -> tuple[str, str]:
    explicit_key = str((context_json or {}).get("idempotency_key", "") or "").strip()
    fields = tuple(get_tool_idempotency_fields(tool_name) or ())
    if fields:
        payload = {field: input_args.get(field) for field in fields if field in input_args}
    else:
        payload = dict(input_args or {})
    normalized = json.dumps(payload, sort_keys=True, default=str)
    computed_key = hashlib.sha256(f"{tool_name}:{normalized}".encode("utf-8")).hexdigest()[:24]
    return explicit_key or computed_key, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _get_idempotency_record(
    db: DBSession,
    workspace_id: str,
    tool_name: str,
    idempotency_key: str,
) -> ToolIdempotencyRecordModel | None:
    if not idempotency_key:
        return None
    try:
        if _can_use_repo_helpers(db):
            try:
                return repo.get_tool_idempotency_record(db, workspace_id, tool_name, idempotency_key)
            except Exception:
                pass
        return (
            db.query(ToolIdempotencyRecordModel)
            .filter(
                ToolIdempotencyRecordModel.workspace_id == workspace_id,
                ToolIdempotencyRecordModel.tool_name == tool_name,
                ToolIdempotencyRecordModel.idempotency_key == idempotency_key,
            )
            .first()
        )
    except Exception:
        return None


def _upsert_idempotency_record(
    db: DBSession,
    workspace_id: str,
    tool_name: str,
    idempotency_key: str,
    *,
    input_hash: str = "",
    status: str = "",
    pending_request_id: str = "",
    tool_call_log_id: str = "",
    output_json: dict | None = None,
    error_message: str = "",
    completed: bool = False,
) -> ToolIdempotencyRecordModel | None:
    if not idempotency_key:
        return None
    try:
        if _can_use_repo_helpers(db):
            try:
                return repo.update_tool_idempotency_record(
                    db,
                    workspace_id,
                    tool_name,
                    idempotency_key,
                    input_hash=input_hash,
                    status=status,
                    pending_request_id=pending_request_id,
                    tool_call_log_id=tool_call_log_id,
                    output_json=output_json,
                    error_message=error_message,
                    completed=completed,
                )
            except Exception:
                pass
        row = _get_idempotency_record(db, workspace_id, tool_name, idempotency_key)
        if not row:
            row = ToolIdempotencyRecordModel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
                input_hash=input_hash,
                status=status or "pending",
                pending_request_id=pending_request_id,
                tool_call_log_id=tool_call_log_id,
                output_json=dict(output_json or {}),
                error_message=error_message,
                created_at=utc_now(),
                updated_at=utc_now(),
                completed_at=utc_now() if completed else None,
            )
            db.add(row)
        else:
            if input_hash:
                row.input_hash = input_hash
            if status:
                row.status = status
            if pending_request_id:
                row.pending_request_id = pending_request_id
            if tool_call_log_id:
                row.tool_call_log_id = tool_call_log_id
            if output_json is not None:
                row.output_json = dict(output_json or {})
            if error_message:
                row.error_message = error_message
            row.updated_at = utc_now()
            if completed:
                row.completed_at = utc_now()
        db.commit()
        return row
    except Exception as exc:
        log_exception(
            logger,
            "tool.idempotency_upsert_failed",
            exc,
            workspace_id=workspace_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _get_pending_request_by_id(db: DBSession, request_id: str) -> PendingToolRequestModel | None:
    if not request_id:
        return None
    try:
        if _can_use_repo_helpers(db):
            try:
                return repo.get_pending_tool_request_by_id(db, request_id)
            except Exception:
                pass
        return (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.id == request_id)
            .first()
        )
    except Exception:
        return None


def _approval_granted(context_json: dict | None, idempotency_key: str = "") -> bool:
    if not isinstance(context_json, dict):
        return False
    if bool(context_json.get("approval_granted")):
        return True
    granted_keys = set(str(item or "") for item in list(context_json.get("approved_idempotency_keys", []) or []))
    return bool(idempotency_key and idempotency_key in granted_keys)


# ── Logging helper ────────────────────────────────────────────────────────────

def _log_attempt(
    db: DBSession,
    workspace_id: str,
    agent_key: str,
    tool_name: str,
    toolkit: str,
    status: str,
    input_json: dict,
    output_json: dict | None = None,
    error_message: str = "",
    duration_ms: float = 0.0,
    idempotency_key: str = "",
    pending_kind: str = "",
    approval_required: bool = False,
) -> ToolCallLogModel:
    """
    Write one row to tool_call_logs. Called for EVERY attempt, regardless
    of outcome. Never raises — swallows DB errors with a log warning.
    """
    try:
        log = ToolCallLogModel(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            agent_key=agent_key,
            tool_name=tool_name,
            toolkit=toolkit,
            status=status,
            idempotency_key=idempotency_key,
            pending_kind=pending_kind,
            approval_required=bool(approval_required),
            input_json=input_json,
            output_json=output_json or {},
            error_message=error_message,
            duration_ms=duration_ms,
            created_at=utc_now(),
        )
        db.add(log)
        db.commit()
        return log
    except Exception as exc:
        log_exception(
            logger,
            "tool.audit_log_failed",
            exc,
            workspace_id=workspace_id,
            agent_name=agent_key,
            tool_name=tool_name,
            toolkit=toolkit,
            error_type=status,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ── Pending-request helper ────────────────────────────────────────────────────

def _save_pending_request(
    db: DBSession,
    workspace_id: str,
    agent_key: str,
    original_input: str,
    tool_name: str,
    toolkit: str,
    resume_token: str,
    conversation_id: str = "",
    context_json: dict | None = None,
    pending_kind: str = "auth",
    idempotency_key: str = "",
    approval_requirement: dict | None = None,
    approved: bool = False,
) -> PendingToolRequestModel | None:
    """
    Persist the original request so it can be resumed after auth completes.
    """
    try:
        if _can_use_repo_helpers(db):
            try:
                return repo.save_pending_tool_request(
                    db,
                    workspace_id,
                    agent_key=agent_key,
                    original_input=original_input,
                    requested_tool=tool_name,
                    requested_toolkit=toolkit,
                    resume_token=resume_token,
                    conversation_id=conversation_id,
                    context_json=context_json,
                    pending_kind=pending_kind,
                    idempotency_key=idempotency_key,
                    approval_requirement_json=approval_requirement,
                    approved=approved,
                )
            except Exception:
                pass
        existing = (
            db.query(PendingToolRequestModel)
            .filter(
                PendingToolRequestModel.workspace_id == workspace_id,
                PendingToolRequestModel.agent_key == agent_key,
                PendingToolRequestModel.original_input == original_input,
                PendingToolRequestModel.requested_tool == tool_name,
                PendingToolRequestModel.requested_toolkit == toolkit,
                PendingToolRequestModel.pending_kind == pending_kind,
                PendingToolRequestModel.status.in_(["pending", "resumed"]),
            )
            .first()
        )
        if existing:
            existing.resume_token = resume_token
            existing.context_json = context_json or {}
            existing.conversation_id = conversation_id or existing.conversation_id
            existing.pending_kind = pending_kind
            existing.idempotency_key = idempotency_key or getattr(existing, "idempotency_key", "")
            existing.approval_requirement_json = dict(approval_requirement or getattr(existing, "approval_requirement_json", {}) or {})
            existing.approved = bool(approved)
            existing.approved_at = utc_now() if approved else getattr(existing, "approved_at", None)
            existing.updated_at = utc_now()
            db.commit()
            return existing
        row = PendingToolRequestModel(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_key=agent_key,
            original_input=original_input,
            requested_tool=tool_name,
            requested_toolkit=toolkit,
            resume_token=resume_token,
            status="pending",
            pending_kind=pending_kind,
            idempotency_key=idempotency_key or "",
            approval_requirement_json=dict(approval_requirement or {}),
            approved=bool(approved),
            approved_at=utc_now() if approved else None,
            context_json=context_json or {},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.commit()
        return row
    except Exception as exc:
        log_exception(
            logger,
            "tool.pending_request_save_failed",
            exc,
            workspace_id=workspace_id,
            agent_name=agent_key,
            tool_name=tool_name,
            toolkit=toolkit,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ── Local connection cache helpers ────────────────────────────────────────────

def _get_local_connection(
    db: DBSession, workspace_id: str, toolkit: str, preferred_account_id: str = ""
) -> Optional[ToolConnectionModel]:
    """Check local DB cache for an active connection."""
    try:
        if _can_use_repo_helpers(db):
            try:
                if preferred_account_id:
                    return repo.get_tool_connection(
                        db,
                        workspace_id,
                        toolkit=toolkit,
                        connected_account_id=preferred_account_id,
                        status="connected",
                    )
                rows = repo.list_tool_connections(db, workspace_id, toolkit=toolkit, status="connected")
                return rows[0] if rows else None
            except Exception:
                pass
        query = (
            db.query(ToolConnectionModel)
            .filter(
                ToolConnectionModel.workspace_id == workspace_id,
                ToolConnectionModel.toolkit == toolkit.upper(),
                ToolConnectionModel.status == "connected",
            )
        )
        if preferred_account_id:
            query = query.filter(ToolConnectionModel.connected_account_id == preferred_account_id)
        return query.order_by(ToolConnectionModel.is_default.desc(), ToolConnectionModel.updated_at.desc()).first()
    except Exception:
        return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_local_connection_fresh(row: ToolConnectionModel | None) -> bool:
    if row is None:
        return False
    last_verified_at = _as_utc(
        getattr(row, "last_verified_at", None)
        or getattr(row, "updated_at", None)
    )
    if last_verified_at is None:
        return False
    return (utc_now() - last_verified_at).total_seconds() <= CONNECTOR_STATUS_TTL_SECONDS


def _get_runtime_connection_cache(context_json: dict | None) -> dict:
    if not isinstance(context_json, dict):
        return {}
    return context_json.setdefault("connector_runtime_cache", {})


def _save_local_connection(
    db: DBSession,
    workspace_id: str,
    toolkit: str,
    connected_account_id: str,
    status: str = "connected",
    auth_mode: str = "oauth2",
    account_label: str = "",
    is_default: bool = False,
) -> None:
    """Upsert local connection state after a live Composio connection check."""
    try:
        if _can_use_repo_helpers(db):
            try:
                repo.upsert_tool_connection(
                    db,
                    workspace_id,
                    toolkit=toolkit,
                    connected_account_id=connected_account_id,
                    tool_name="*",
                    status=status,
                    auth_mode=auth_mode,
                    account_label=account_label,
                    is_default=is_default,
                    last_verified_at=utc_now(),
                    status_updated_at=utc_now(),
                    metadata_json={},
                )
                return
            except Exception:
                pass
        query = (
            db.query(ToolConnectionModel)
            .filter(
                ToolConnectionModel.workspace_id == workspace_id,
                ToolConnectionModel.toolkit == toolkit.upper(),
            )
        )
        if connected_account_id:
            query = query.filter(ToolConnectionModel.connected_account_id == connected_account_id)
        else:
            query = query.filter(ToolConnectionModel.connected_account_id == "")
        existing = query.first()
        if existing:
            existing.status = status
            existing.connected_account_id = connected_account_id
            existing.account_label = account_label or getattr(existing, "account_label", "")
            existing.is_default = bool(is_default or getattr(existing, "is_default", False))
            existing.last_verified_at = utc_now()
            existing.status_updated_at = utc_now()
            existing.updated_at = utc_now()
        else:
            row = ToolConnectionModel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                user_id=workspace_id,
                tool_name="*",
                toolkit=toolkit.upper(),
                status=status,
                connected_account_id=connected_account_id,
                account_label=account_label,
                is_default=bool(is_default),
                auth_mode=auth_mode,
                metadata_json={},
                last_verified_at=utc_now(),
                status_updated_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
        db.commit()
    except Exception as exc:
        log_exception(
            logger,
            "tool.local_connection_save_failed",
            exc,
            workspace_id=workspace_id,
            toolkit=toolkit,
        )
        try:
            db.rollback()
        except Exception:
            pass


# ── Main entry point ──────────────────────────────────────────────────────────

def attempt_tool_call(
    tool_name: str,
    agent_key: str,
    workspace_id: str,
    db: DBSession,
    input_args: dict | None = None,
    original_input: str = "",
    conversation_id: str = "",
    context_json: dict | None = None,
    callback_url: str = "",
    selected_account_id: str = "",
) -> dict:
    """
    Full validation → connection check → execute pipeline for a single
    tool call.

    Parameters:
        tool_name        — Composio action slug (must exist in TOOL_REGISTRY)
        agent_key        — The agent requesting the call (must be allowed)
        workspace_id     — Current workspace (= Composio user_id)
        db               — SQLAlchemy session for logging
        input_args       — Arguments to pass to the tool (for future execution)
        original_input   — User's original message (saved if auth is needed)
        conversation_id  — Conversation row id (for resume linking)
        context_json     — Snapshot of brain_context etc. (for resume)
        callback_url     — Where to redirect after OAuth completes

    Returns:
        {
            "status": "success" | "failure" | "connect_required"
                      | "validation_error" | "timeout",
            "output": dict | None,
            "error":  str | None,
            ...plus extra keys for connect_required (connect_url, resume_token)
        }
    """
    start = time.time()
    log_event(
        logger,
        logging.INFO,
        "tool.execute.start",
        workspace_id=workspace_id,
        agent_name=agent_key,
        tool_name=tool_name,
    )
    tool_entry = get_tool(tool_name)

    # ── Step 1: Validate tool exists ──────────────────────────────────────
    if tool_entry is None:
        result = _fail("invalid_tool", f"Unknown tool: {tool_name}")
        _log_attempt(
            db, workspace_id, agent_key, tool_name, "",
            "invalid_tool", input_args, error_message=result["error"],
        )
        return result

    toolkit = tool_entry["toolkit"]
    input_args = normalize_tool_input(tool_name, input_args or {})
    approval_requirement = get_tool_approval_requirement(tool_name)
    write_action = bool(tool_entry.get("write_action"))
    idempotency_key = ""
    input_hash = ""
    is_retry = bool(context_json and context_json.get("is_retry"))

    # ── Step 2: Validate agent permission ─────────────────────────────────
    if not is_agent_allowed(tool_name, agent_key):
        result = _fail(
            "validation_error",
            f"Agent '{agent_key}' is not allowed to use tool '{tool_name}'",
        )
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "validation_error", input_args, error_message=result["error"],
        )
        return result

    # ── Step 3: Validate tool exists in Composio ──────────────────────────
    if is_available():
        catalog_check = validate_tool_slug(tool_name)
        if catalog_check.get("available") and not catalog_check.get("exists"):
            error_msg = (
                f"Tool '{tool_name}' is not available in the current Composio catalog."
            )
            _log_attempt(
                db, workspace_id, agent_key, tool_name, toolkit,
                "invalid_tool", input_args, error_message=error_msg,
            )
            return _fail("invalid_tool", error_msg)
        if not catalog_check.get("available") and catalog_check.get("error"):
            log_event(
                logger,
                logging.WARNING,
                "tool.catalog_validation_skipped",
                workspace_id=workspace_id,
                agent_name=agent_key,
                tool_name=tool_name,
                error_type="catalog_unavailable",
            )

    # ── Step 4: Validate parameters using local schema and live schema ────
    input_args, validation_errors = validate_tool_input(tool_name, input_args)
    if is_available():
        schema_cache_key = f"{workspace_id}:{tool_name}"
        live_schema = _get_cached_live_schema(context_json, schema_cache_key)
        if not live_schema:
            live_schema = get_live_tool_schema(workspace_id, tool_name) or get_live_tool_schema("__catalog__", tool_name)
            _set_cached_live_schema(context_json, schema_cache_key, live_schema)
        validation_errors.extend(_validate_against_live_schema(input_args, live_schema))
    else:
        live_schema = {}

    if validation_errors:
        unique_errors = list(dict.fromkeys(validation_errors))
        result = _fail(
            "validation_error",
            f"Invalid parameters for tool '{tool_name}': {' '.join(unique_errors)}"
        )
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "validation_error", input_args, error_message=result["error"],
        )
        return result

    # ── Step 4.5: Prepare durable idempotency for external writes ────────
    idempotency_record = None
    if write_action:
        idempotency_key, input_hash = _serialize_idempotency_payload(tool_name, input_args, context_json)
        idempotency_record = _get_idempotency_record(db, workspace_id, tool_name, idempotency_key)
        if not idempotency_record:
            if _can_use_repo_helpers(db):
                try:
                    idempotency_record = repo.claim_tool_idempotency_record(
                        db,
                        workspace_id,
                        tool_name,
                        idempotency_key,
                        input_hash=input_hash,
                        status="in_progress",
                    )
                except Exception:
                    idempotency_record = None
            if not idempotency_record:
                idempotency_record = _upsert_idempotency_record(
                    db,
                    workspace_id,
                    tool_name,
                    idempotency_key,
                    input_hash=input_hash,
                    status="in_progress",
                )
        if idempotency_record and getattr(idempotency_record, "input_hash", "") and idempotency_record.input_hash != input_hash:
            error_message = "The same idempotency key was reused for a different write payload."
            _log_attempt(
                db, workspace_id, agent_key, tool_name, toolkit,
                "validation_error", input_args, error_message=error_message,
                idempotency_key=idempotency_key,
                approval_required=approval_requirement.required,
            )
            return _fail("validation_error", error_message)

        if idempotency_record and getattr(idempotency_record, "status", "") == "success":
            cached_output = dict(getattr(idempotency_record, "output_json", {}) or {})
            log_event(
                logger,
                logging.INFO,
                "tool.idempotency_replay",
                workspace_id=workspace_id,
                agent_name=agent_key,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
            )
            return {
                **_ok(cached_output, 0.0),
                "toolkit": toolkit,
                "idempotency_key": idempotency_key,
                "idempotent_replay": True,
            }

        if idempotency_record and getattr(idempotency_record, "status", "") in {"pending_auth", "pending_approval", "in_progress"}:
            pending_request = _get_pending_request_by_id(db, getattr(idempotency_record, "pending_request_id", "") or "")
            pending_status = getattr(idempotency_record, "status", "")
            if pending_status == "pending_auth" and not is_retry and pending_request:
                return {
                    **_connect_required(
                        toolkit,
                        get_connect_link(workspace_id, toolkit, callback_url),
                        getattr(pending_request, "resume_token", ""),
                    ),
                    "pending_kind": getattr(pending_request, "pending_kind", "auth"),
                    "idempotency_key": idempotency_key,
                }
            if pending_status == "pending_approval" and not _approval_granted(context_json, idempotency_key) and pending_request:
                return {
                    "status": "validation_error",
                    "output": None,
                    "error": "Approval is still required before this action can run.",
                    "toolkit": toolkit,
                    "resume_token": getattr(pending_request, "resume_token", ""),
                    "approval_required": True,
                    "approval_requirement": dict(getattr(pending_request, "approval_requirement_json", {}) or approval_requirement.to_dict()),
                    "pending_kind": "approval",
                    "idempotency_key": idempotency_key,
                }
        elif idempotency_record and getattr(idempotency_record, "status", "") not in {"success", "pending_auth", "pending_approval", "in_progress"}:
            _upsert_idempotency_record(
                db,
                workspace_id,
                tool_name,
                idempotency_key,
                input_hash=input_hash,
                status="in_progress",
            )

    # ── Step 5: Check connection (local cache, then remote live check) ───
    requires_auth = tool_entry.get("requires_auth", True)
    conn_info: dict = {}
    selected_account_id = str(selected_account_id or "")

    if not requires_auth:
        # Tool does not require dynamic OAuth
        connected = True
    else:
        local_connection = _get_local_connection(db, workspace_id, toolkit, preferred_account_id=selected_account_id)
        runtime_cache = _get_runtime_connection_cache(context_json)
        cache_key = f"{toolkit.upper()}::{selected_account_id or '*'}"
        cached_conn_info = runtime_cache.get(cache_key)
        if cached_conn_info and not is_retry:
            conn_info = dict(cached_conn_info)
            connected = bool(conn_info.get("connected", False))
        elif local_connection and not is_retry and _is_local_connection_fresh(local_connection):
            conn_info = {
                "connected": True,
                "connected_account_id": local_connection.connected_account_id,
                "status": local_connection.status,
                "error": None,
            }
            connected = True
            runtime_cache[cache_key] = dict(conn_info)
            log_event(
                logger,
                logging.INFO,
                "tool.connection_cache_hit",
                workspace_id=workspace_id,
                agent_name=agent_key,
                toolkit=toolkit,
                tool_name=tool_name,
            )
        else:
            connected = False

        if not connected and is_available():
            conn_info = check_connection(
                workspace_id,
                toolkit,
                force_refresh=is_retry,
                preferred_account_id=selected_account_id,
            )
            connected = conn_info.get("connected", False)
            runtime_cache[cache_key] = dict(conn_info)
            _save_local_connection(
                db,
                workspace_id,
                toolkit,
                conn_info.get("connected_account_id") or selected_account_id or "",
                status="connected" if connected else conn_info.get("status", "pending"),
                account_label=str(conn_info.get("account_label", "") or ""),
                is_default=bool(conn_info.get("is_default", False)),
            )
            if connected:
                log_event(
                    logger,
                    logging.INFO,
                    "tool.connection_verified",
                    workspace_id=workspace_id,
                    agent_name=agent_key,
                    toolkit=toolkit,
                    tool_name=tool_name,
                )
        elif not connected:
            # Composio not available — caller should show auth/setup failure,
            # not pretend the tool request succeeded.
            connected = False

    # ── Step 6: Not connected → return connect_required ───────────────────
    if not connected:
        resume_token = str(uuid.uuid4())
        auth_details = get_toolkit_auth_details(toolkit) if is_available() else {}
        connect_url = get_connect_link(workspace_id, toolkit, callback_url)
        error_detail = None
        if not connect_url:
            error_detail = _build_auth_unavailable_error(toolkit, auth_details, conn_info)

        # Persist only resumable requests.
        pending_row = None
        if connect_url:
            pending_row = _save_pending_request(
                db, workspace_id, agent_key, original_input,
                tool_name, toolkit, resume_token,
                conversation_id=conversation_id,
                context_json=context_json,
                pending_kind="auth",
                idempotency_key=idempotency_key,
                approval_requirement=approval_requirement.to_dict(),
            )
            if write_action and pending_row is not None:
                _upsert_idempotency_record(
                    db,
                    workspace_id,
                    tool_name,
                    idempotency_key,
                    input_hash=input_hash,
                    status="pending_auth",
                    pending_request_id=getattr(pending_row, "id", ""),
                )

        elapsed = (time.time() - start) * 1000
        missing_status = "connect_required" if connect_url else "auth_unavailable"
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            missing_status, input_args,
            error_message=error_detail or conn_info.get("error") or f"Auth required for {toolkit}",
            duration_ms=elapsed,
            idempotency_key=idempotency_key,
            pending_kind="auth" if connect_url else "",
            approval_required=approval_requirement.required,
        )
        if write_action and not connect_url:
            _upsert_idempotency_record(
                db,
                workspace_id,
                tool_name,
                idempotency_key,
                input_hash=input_hash,
                status="failure",
                error_message=error_detail or conn_info.get("error") or f"Auth unavailable for {toolkit}",
            )

        return _connect_required(
            toolkit,
            connect_url,
            resume_token,
            error=error_detail,
        )

    # ── Step 6.5: Approval gate for risky writes ────────────────────────
    if write_action and approval_requirement.required and not _approval_granted(context_json, idempotency_key):
        resume_token = str(uuid.uuid4())
        approval_payload = approval_requirement.to_dict()
        pending_row = _save_pending_request(
            db,
            workspace_id,
            agent_key,
            original_input,
            tool_name,
            toolkit,
            resume_token,
            conversation_id=conversation_id,
            context_json=context_json,
            pending_kind="approval",
            idempotency_key=idempotency_key,
            approval_requirement=approval_payload,
        )
        _upsert_idempotency_record(
            db,
            workspace_id,
            tool_name,
            idempotency_key,
            input_hash=input_hash,
            status="pending_approval",
            pending_request_id=getattr(pending_row, "id", "") if pending_row else "",
        )
        elapsed = (time.time() - start) * 1000
        _log_attempt(
            db,
            workspace_id,
            agent_key,
            tool_name,
            toolkit,
            "validation_error",
            input_args,
            error_message="Approval required before executing this action.",
            duration_ms=elapsed,
            idempotency_key=idempotency_key,
            pending_kind="approval",
            approval_required=True,
        )
        return {
            "status": "validation_error",
            "output": None,
            "error": "Approval required before executing this action.",
            "duration_ms": elapsed,
            "toolkit": toolkit,
            "resume_token": resume_token,
            "approval_required": True,
            "approval_requirement": approval_payload,
            "pending_kind": "approval",
            "idempotency_key": idempotency_key,
        }

    # ── Step 7: Connected → execute tool via Composio ────────────────────
    try:
        if not is_available():
            raise RuntimeError("Composio SDK not available for execution")

        exec_result = execute_tool(
            workspace_id,
            tool_name,
            arguments=input_args,
            connected_account_id=conn_info.get("connected_account_id"),
        )
        elapsed = (time.time() - start) * 1000
        output_data = exec_result.get("data", exec_result)
        error_text = exec_result.get("error")

        if error_text:
            call_log = _log_attempt(
                db, workspace_id, agent_key, tool_name, toolkit,
                "failure", input_args,
                output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
                error_message=str(error_text),
                duration_ms=elapsed,
                idempotency_key=idempotency_key,
                approval_required=approval_requirement.required,
            )
            if write_action:
                _upsert_idempotency_record(
                    db,
                    workspace_id,
                    tool_name,
                    idempotency_key,
                    input_hash=input_hash,
                    status="failure",
                    tool_call_log_id=getattr(call_log, "id", ""),
                    output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
                    error_message=str(error_text),
                )
            return _fail("failure", str(error_text), elapsed)

        response_len = len(str(output_data))
        param_summary = ", ".join(input_args.keys()) if input_args else "none"
        log_event(
            logger,
            logging.INFO,
            "tool.execute.success",
            workspace_id=workspace_id,
            agent_name=agent_key,
            tool_name=tool_name,
            toolkit=toolkit,
            duration_ms=round(elapsed, 2),
            input_fields=param_summary,
            output_length=response_len,
        )

        call_log = _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "success", input_args,
            output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
            duration_ms=elapsed,
            idempotency_key=idempotency_key,
            approval_required=approval_requirement.required,
        )
        if write_action:
            _upsert_idempotency_record(
                db,
                workspace_id,
                tool_name,
                idempotency_key,
                input_hash=input_hash,
                status="success",
                tool_call_log_id=getattr(call_log, "id", ""),
                output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
                completed=True,
            )

        return {
            **_ok(output_data if isinstance(output_data, dict) else {"data": output_data}, elapsed),
            "toolkit": toolkit,
            "idempotency_key": idempotency_key,
        }

    except Exception as exc:
        elapsed = (time.time() - start) * 1000

        # If the error is a known "not available" case, return a soft placeholder
        # so the agent can still produce a useful text response.
        error_str = str(exc)
        log_exception(
            logger,
            "tool.execute.failed",
            exc,
            workspace_id=workspace_id,
            agent_name=agent_key,
            tool_name=tool_name,
            toolkit=toolkit,
            duration_ms=round(elapsed, 2),
        )
        call_log = _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "failure", input_args,
            error_message=error_str,
            duration_ms=elapsed,
            idempotency_key=idempotency_key,
            approval_required=approval_requirement.required,
        )
        if write_action:
            _upsert_idempotency_record(
                db,
                workspace_id,
                tool_name,
                idempotency_key,
                input_hash=input_hash,
                status="failure",
                tool_call_log_id=getattr(call_log, "id", ""),
                error_message=error_str,
            )

        return _fail("failure", error_str, elapsed)


# ── Resume helper (for after OAuth completes) ─────────────────────────────────

def get_pending_request(
    db: DBSession, resume_token: str
) -> Optional[PendingToolRequestModel]:
    """Look up a pending request by its resume token."""
    try:
        row = None
        if _can_use_repo_helpers(db):
            try:
                row = repo.get_pending_tool_request_by_resume_token(db, resume_token)
            except Exception:
                row = None
        if row is None:
            row = (
                db.query(PendingToolRequestModel)
                .filter(
                    PendingToolRequestModel.resume_token == resume_token,
                    PendingToolRequestModel.status.in_(["pending", "resumed"]),
                )
                .first()
            )
        if row and getattr(row, "status", "") in {"pending", "resumed"}:
            return row
        return None
    except Exception:
        return None


def mark_request_resumed(db: DBSession, resume_token: str) -> None:
    """Mark a pending request as resumed (auth completed, re-executing)."""
    try:
        row = None
        if _can_use_repo_helpers(db):
            try:
                row = repo.get_pending_tool_request_by_resume_token(db, resume_token)
            except Exception:
                row = None
        if row is None:
            row = (
                db.query(PendingToolRequestModel)
                .filter(PendingToolRequestModel.resume_token == resume_token)
                .first()
            )
        if row and getattr(row, "pending_kind", "auth") == "approval" and not getattr(row, "approved", False):
            return
        if _can_use_repo_helpers(db):
            try:
                repo.transition_pending_tool_request(
                    db,
                    resume_token,
                    to_status="resumed",
                    allowed_statuses=("pending", "resumed"),
                    require_approved=False,
                )
                return
            except Exception:
                pass
        if row:
            row.status = "resumed"
            row.updated_at = utc_now()
            db.commit()
    except Exception as exc:
        log_exception(
            logger,
            "tool.pending_request_resume_failed",
            exc,
            resume_token=resume_token,
        )
        try:
            db.rollback()
        except Exception:
            pass


def mark_request_completed(db: DBSession, resume_token: str) -> None:
    """Mark a pending request as completed after a successful resume."""
    try:
        if _can_use_repo_helpers(db):
            try:
                repo.transition_pending_tool_request(
                    db,
                    resume_token,
                    to_status="completed",
                    allowed_statuses=("pending", "resumed"),
                )
                return
            except Exception:
                pass
        row = (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.resume_token == resume_token)
            .first()
        )
        if row:
            row.status = "completed"
            row.updated_at = utc_now()
            db.commit()
    except Exception as exc:
        log_exception(
            logger,
            "tool.pending_request_complete_failed",
            exc,
            resume_token=resume_token,
        )
        try:
            db.rollback()
        except Exception:
            pass


def mark_request_approved(db: DBSession, resume_token: str) -> None:
    """Mark a pending approval request as explicitly approved."""
    try:
        if _can_use_repo_helpers(db):
            try:
                repo.approve_pending_tool_request(db, resume_token)
                return
            except Exception:
                pass
        row = (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.resume_token == resume_token)
            .first()
        )
        if row:
            row.approved = True
            row.approved_at = utc_now()
            row.updated_at = utc_now()
            context_json = dict(getattr(row, "context_json", {}) or {})
            context_json["approval_granted"] = True
            granted = list(context_json.get("approved_idempotency_keys", []) or [])
            idempotency_key = str(getattr(row, "idempotency_key", "") or "")
            if idempotency_key and idempotency_key not in granted:
                granted.append(idempotency_key)
            context_json["approved_idempotency_keys"] = granted
            row.context_json = context_json
            db.commit()
    except Exception as exc:
        log_exception(
            logger,
            "tool.pending_request_approve_failed",
            exc,
            resume_token=resume_token,
        )
        try:
            db.rollback()
        except Exception:
            pass
