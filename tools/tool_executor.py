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

import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as DBSession

from tools.tool_registry import get_tool, is_agent_allowed
from tools.composio_client import (
    is_available,
    check_connection,
    get_connect_link,
    get_toolkit_auth_details,
    validate_tool_slug,
    execute_tool,
)
from models.tool_call_logs import ToolCallLogModel
from models.tool_connections import ToolConnectionModel
from models.pending_tool_requests import PendingToolRequestModel
from utils.logging_utils import log_event, log_exception

logger = logging.getLogger(__name__)


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


def _normalize_input_args(tool_entry: dict, input_args: dict) -> dict:
    """
    Apply tool-specific alias and default handling before validation/execution.

    This keeps the validation layer strict without requiring the LLM to guess
    exact schema field names like ``recipient_email`` or ``calendarId``.
    """
    normalized = dict(input_args or {})

    for source_key, target_key in tool_entry.get("param_aliases", {}).items():
        if source_key in normalized and target_key not in normalized:
            normalized[target_key] = normalized.pop(source_key)

    for key, value in tool_entry.get("default_params", {}).items():
        normalized.setdefault(key, value)

    return normalized


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


# Toolkits that require custom API keys (no Composio managed OAuth)
_CUSTOM_KEY_TOOLKITS: dict[str, str] = {
    "TAVILY": (
        "Tavily requires an API key. Please add your Tavily API key in the "
        "Composio dashboard under the Tavily toolkit settings, then connect "
        "your account."
    ),
    "TWITTER": (
        "X/Twitter requires developer API credentials (API Key, API Secret, "
        "Access Token, Access Token Secret). Please configure them in the "
        "Composio dashboard under the Twitter toolkit, then connect your account."
    ),
}


def _build_auth_unavailable_error(toolkit: str, auth_details: dict, conn_info: dict) -> str:
    """Explain why a connection link cannot be generated for this toolkit."""
    if conn_info.get("error"):
        return str(conn_info["error"])

    # Toolkit-specific messages for custom-key integrations
    custom_msg = _CUSTOM_KEY_TOOLKITS.get(toolkit.upper())
    if custom_msg:
        return custom_msg

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
            input_json=input_json,
            output_json=output_json or {},
            error_message=error_message,
            duration_ms=duration_ms,
            created_at=datetime.utcnow(),
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
) -> PendingToolRequestModel | None:
    """
    Persist the original request so it can be resumed after auth completes.
    """
    try:
        existing = (
            db.query(PendingToolRequestModel)
            .filter(
                PendingToolRequestModel.workspace_id == workspace_id,
                PendingToolRequestModel.agent_key == agent_key,
                PendingToolRequestModel.original_input == original_input,
                PendingToolRequestModel.requested_tool == tool_name,
                PendingToolRequestModel.requested_toolkit == toolkit,
                PendingToolRequestModel.status.in_(["pending", "resumed"]),
            )
            .first()
        )
        if existing:
            existing.resume_token = resume_token
            existing.context_json = context_json or {}
            existing.conversation_id = conversation_id or existing.conversation_id
            existing.updated_at = datetime.utcnow()
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
            context_json=context_json or {},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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
    db: DBSession, workspace_id: str, toolkit: str
) -> Optional[ToolConnectionModel]:
    """Check local DB cache for an active connection."""
    try:
        return (
            db.query(ToolConnectionModel)
            .filter(
                ToolConnectionModel.workspace_id == workspace_id,
                ToolConnectionModel.toolkit == toolkit.upper(),
                ToolConnectionModel.status == "connected",
            )
            .first()
        )
    except Exception:
        return None


def _save_local_connection(
    db: DBSession,
    workspace_id: str,
    toolkit: str,
    connected_account_id: str,
    status: str = "connected",
    auth_mode: str = "oauth2",
) -> None:
    """Upsert local connection state after a live Composio connection check."""
    try:
        existing = (
            db.query(ToolConnectionModel)
            .filter(
                ToolConnectionModel.workspace_id == workspace_id,
                ToolConnectionModel.toolkit == toolkit.upper(),
            )
            .first()
        )
        if existing:
            existing.status = status
            existing.connected_account_id = connected_account_id
            existing.updated_at = datetime.utcnow()
        else:
            row = ToolConnectionModel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                user_id=workspace_id,  # user_id == workspace_id for now
                tool_name="*",         # toolkit-level connection
                toolkit=toolkit.upper(),
                status=status,
                connected_account_id=connected_account_id,
                auth_mode=auth_mode,
                metadata_json={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
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
    input_args = _normalize_input_args(tool_entry, input_args or {})

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

    # ── Step 4: Validate expected parameters ──────────────────────────────
    expected = tool_entry.get("expected_params", [])
    missing = [p for p in expected if p not in input_args]
    if missing:
        result = _fail(
            "validation_error",
            f"Missing required parameters for tool '{tool_name}': {', '.join(missing)}"
        )
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "validation_error", input_args, error_message=result["error"],
        )
        return result


    # ── Step 5: Check connection (local cache, then remote live check) ───
    requires_auth = tool_entry.get("requires_auth", True)
    conn_info: dict = {}

    # Detect retry: if coming from a resume path, force fresh connection check
    is_retry = bool(context_json and context_json.get("is_retry"))

    if not requires_auth:
        # Tool does not require dynamic OAuth
        connected = True
    else:
        local_connection = _get_local_connection(db, workspace_id, toolkit)
        if local_connection and not is_retry:
            conn_info = {
                "connected": True,
                "connected_account_id": local_connection.connected_account_id,
                "status": local_connection.status,
                "error": None,
            }
            connected = True
        else:
            connected = False

        if is_available():
            conn_info = check_connection(workspace_id, toolkit, force_refresh=is_retry)
            connected = conn_info.get("connected", False)
            _save_local_connection(
                db,
                workspace_id,
                toolkit,
                conn_info.get("connected_account_id") or "",
                status="connected" if connected else conn_info.get("status", "pending"),
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
        else:
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
        if connect_url:
            _save_pending_request(
                db, workspace_id, agent_key, original_input,
                tool_name, toolkit, resume_token,
                conversation_id=conversation_id,
                context_json=context_json,
            )

        elapsed = (time.time() - start) * 1000
        missing_status = "connect_required" if connect_url else "auth_unavailable"
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            missing_status, input_args,
            error_message=error_detail or conn_info.get("error") or f"Auth required for {toolkit}",
            duration_ms=elapsed,
        )

        return _connect_required(
            toolkit,
            connect_url,
            resume_token,
            error=error_detail,
        )

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
            _log_attempt(
                db, workspace_id, agent_key, tool_name, toolkit,
                "failure", input_args,
                output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
                error_message=str(error_text),
                duration_ms=elapsed,
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

        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "success", input_args,
            output_json=output_data if isinstance(output_data, dict) else {"data": output_data},
            duration_ms=elapsed,
        )

        return _ok(output_data if isinstance(output_data, dict) else {"data": output_data}, elapsed)

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
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "failure", input_args,
            error_message=error_str,
            duration_ms=elapsed,
        )

        return _fail("failure", error_str, elapsed)


# ── Resume helper (for after OAuth completes) ─────────────────────────────────

def get_pending_request(
    db: DBSession, resume_token: str
) -> Optional[PendingToolRequestModel]:
    """Look up a pending request by its resume token."""
    try:
        return (
            db.query(PendingToolRequestModel)
            .filter(
                PendingToolRequestModel.resume_token == resume_token,
                PendingToolRequestModel.status.in_(["pending", "resumed"]),
            )
            .first()
        )
    except Exception:
        return None


def mark_request_resumed(db: DBSession, resume_token: str) -> None:
    """Mark a pending request as resumed (auth completed, re-executing)."""
    try:
        row = (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.resume_token == resume_token)
            .first()
        )
        if row:
            row.status = "resumed"
            row.updated_at = datetime.utcnow()
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
        row = (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.resume_token == resume_token)
            .first()
        )
        if row:
            row.status = "completed"
            row.updated_at = datetime.utcnow()
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
