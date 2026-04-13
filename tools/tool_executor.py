"""
tools/tool_executor.py  —  Validates, checks connections, logs, and (in future)
executes Composio tool calls.

This is the layer that handler.py / executor.py will call in a later mission
when tool-calling is wired into the chat flow. For now it is STANDALONE — no
existing code imports it, so backward compatibility is guaranteed.

Execution contract (per call):
  1. Validate that tool_name exists in the registry.
  2. Validate that the requesting agent is allowed to use it.
  3. Check whether the workspace/user has a live connection for the toolkit.
  4. If not connected  → return {"status": "connect_required", ...} + log.
  5. If connected      → execute the tool via Composio + log result.
  6. Every attempt (success, failure, connect_required, validation_error)
     produces exactly one ToolCallLog row.
"""
from __future__ import annotations

import json
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
)
from models.tool_call_logs import ToolCallLogModel
from models.tool_connections import ToolConnectionModel
from models.pending_tool_requests import PendingToolRequestModel

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
        "status": status,  # failure | connect_required | validation_error | timeout
        "output": None,
        "error": error,
        "duration_ms": duration_ms,
    }


def _connect_required(
    toolkit: str,
    connect_url: Optional[str],
    resume_token: str,
) -> dict:
    return {
        "status": "connect_required",
        "output": None,
        "error": None,
        "toolkit": toolkit,
        "connect_url": connect_url,
        "resume_token": resume_token,
    }


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
        logger.warning("Failed to write tool_call_log: %s", exc)
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
        logger.warning("Failed to save pending_tool_request: %s", exc)
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
    auth_mode: str = "oauth2",
) -> None:
    """Upsert a local connection record after a remote check confirms active."""
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
            existing.status = "connected"
            existing.connected_account_id = connected_account_id
            existing.updated_at = datetime.utcnow()
        else:
            row = ToolConnectionModel(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                user_id=workspace_id,  # user_id == workspace_id for now
                tool_name="*",         # toolkit-level connection
                toolkit=toolkit.upper(),
                status="connected",
                connected_account_id=connected_account_id,
                auth_mode=auth_mode,
                metadata_json={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(row)
        db.commit()
    except Exception as exc:
        logger.warning("Failed to save local connection: %s", exc)
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
    input_args = input_args or {}
    tool_entry = get_tool(tool_name)

    # ── Step 1: Validate tool exists ──────────────────────────────────────
    if tool_entry is None:
        result = _fail("validation_error", f"Unknown tool: {tool_name}")
        _log_attempt(
            db, workspace_id, agent_key, tool_name, "",
            "validation_error", input_args, error_message=result["error"],
        )
        return result

    toolkit = tool_entry["toolkit"]

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

    # ── Step 3: Check connection (local cache first, then remote) ─────────
    local_conn = _get_local_connection(db, workspace_id, toolkit)
    if local_conn:
        # Fast path — we already know this toolkit is connected
        connected = True
    elif is_available():
        # Slow path — ask Composio
        conn_info = check_connection(workspace_id, toolkit)
        connected = conn_info.get("connected", False)

        # Cache the result locally if connected
        if connected and conn_info.get("connected_account_id"):
            _save_local_connection(
                db, workspace_id, toolkit,
                conn_info["connected_account_id"],
            )
    else:
        # Composio not available — treat as not connected
        connected = False

    # ── Step 4: Not connected → return connect_required ───────────────────
    if not connected:
        resume_token = str(uuid.uuid4())
        connect_url = get_connect_link(workspace_id, toolkit, callback_url)

        # Persist so we can resume after auth
        _save_pending_request(
            db, workspace_id, agent_key, original_input,
            tool_name, toolkit, resume_token,
            conversation_id=conversation_id,
            context_json=context_json,
        )

        elapsed = (time.time() - start) * 1000
        _log_attempt(
            db, workspace_id, agent_key, tool_name, toolkit,
            "connect_required", input_args,
            error_message=f"Auth required for {toolkit}",
            duration_ms=elapsed,
        )

        return _connect_required(toolkit, connect_url, resume_token)

    # ── Step 5: Connected → execute tool via Composio ───────────────────
    try:
        from tools.composio_client import is_available as _composio_ready

        if _composio_ready():
            # Import here to avoid circular deps and to keep Composio optional
            from tools.composio_client import get_session

            session = get_session(workspace_id)
            if session is None:
                raise RuntimeError("Composio session not available")

            # Use Composio's direct tool execution
            from tools.composio_client import _composio_client as _cc
            exec_result = _cc.tools.execute(
                tool_name,
                {
                    "user_id": workspace_id,
                    "arguments": input_args,
                },
            )

            elapsed = (time.time() - start) * 1000

            # Normalise the result to a dict
            if hasattr(exec_result, "dict"):
                output_data = exec_result.dict()
            elif isinstance(exec_result, dict):
                output_data = exec_result
            else:
                output_data = {"raw": str(exec_result)}

            _log_attempt(
                db, workspace_id, agent_key, tool_name, toolkit,
                "success", input_args,
                output_json=output_data,
                duration_ms=elapsed,
            )

            return _ok(output_data, elapsed)
        else:
            # Composio SDK not available — return a descriptive placeholder
            raise RuntimeError("Composio SDK not available for execution")

    except Exception as exc:
        elapsed = (time.time() - start) * 1000

        # If the error is a known "not available" case, return a soft placeholder
        # so the agent can still produce a useful text response.
        error_str = str(exc)
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
                PendingToolRequestModel.status == "pending",
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
        logger.warning("Failed to mark request resumed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
