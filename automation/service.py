from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from helpers.configs import AGENTS
from models.contracts import ConnectorContext, ExecutionPolicy, RetryPolicy, ScheduleSpec, ScheduledTaskPayload
from storage import repositories as repo
from tools.connector_service import normalize_connector_context, validate_connector_context
from utils.time_utils import utc_now
from workflows.engine import WORKFLOWS

from automation.schedule import compute_next_run_at, parse_datetime, validate_schedule_spec
from automation.keys import build_run_idempotency_key, build_run_key


VALID_TARGET_KINDS = {"workflow", "agent", "direct_action"}


def _task_schedule_dict(task) -> dict:
    return {
        "schedule_type": getattr(task, "schedule_type", "once"),
        "timezone": getattr(task, "timezone", "UTC") or "UTC",
        "start_at": getattr(task, "start_at", None),
        "end_at": getattr(task, "end_at", None),
        "cron_expression": getattr(task, "cron_expression", "") or "",
        "interval_seconds": int(getattr(task, "interval_seconds", 0) or 0),
    }


def _validate_target(payload: ScheduledTaskPayload) -> list[str]:
    errors: list[str] = []
    if payload.target_kind not in VALID_TARGET_KINDS:
        errors.append("target_kind must be one of: workflow, agent, direct_action.")
    if payload.target_kind == "workflow" and payload.target_name not in WORKFLOWS:
        errors.append(f"Unknown workflow target: {payload.target_name}.")
    if payload.target_kind == "agent" and payload.target_name not in AGENTS:
        errors.append(f"Unknown agent target: {payload.target_name}.")
    if payload.target_kind == "direct_action" and not (payload.force_agent or payload.target_name or payload.user_input):
        errors.append("direct_action targets require target_name, force_agent, or user_input.")
    if not payload.user_input.strip():
        errors.append("Scheduled automations require user_input.")
    return errors


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    return dict(value or {})


def task_to_dict(task) -> dict:
    return {
        "id": getattr(task, "id", ""),
        "workspace_id": getattr(task, "workspace_id", ""),
        "actor_user_id": getattr(task, "actor_user_id", None),
        "membership_id": getattr(task, "membership_id", None),
        "task_kind": getattr(task, "task_kind", "automation"),
        "target_kind": getattr(task, "target_kind", ""),
        "target_name": getattr(task, "target_name", ""),
        "status": getattr(task, "status", ""),
        "schedule_type": getattr(task, "schedule_type", ""),
        "cron_expression": getattr(task, "cron_expression", "") or "",
        "interval_seconds": int(getattr(task, "interval_seconds", 0) or 0),
        "timezone": getattr(task, "timezone", "UTC") or "UTC",
        "start_at": str(getattr(task, "start_at", "") or ""),
        "end_at": str(getattr(task, "end_at", "") or ""),
        "next_run_at": str(getattr(task, "next_run_at", "") or ""),
        "last_run_at": str(getattr(task, "last_run_at", "") or ""),
        "connector_context": dict(getattr(task, "connector_context_json", {}) or {}),
        "payload": dict(getattr(task, "payload_json", {}) or {}),
        "execution_policy": dict(getattr(task, "execution_policy_json", {}) or {}),
        "retry_policy": dict(getattr(task, "retry_policy_json", {}) or {}),
        "metadata_json": dict(getattr(task, "metadata_json", {}) or {}),
        "created_at": str(getattr(task, "created_at", "") or ""),
        "updated_at": str(getattr(task, "updated_at", "") or ""),
    }


def run_to_dict(run) -> dict:
    return {
        "id": getattr(run, "id", ""),
        "scheduled_task_id": getattr(run, "scheduled_task_id", ""),
        "workspace_id": getattr(run, "workspace_id", ""),
        "actor_user_id": getattr(run, "actor_user_id", None),
        "membership_id": getattr(run, "membership_id", None),
        "run_key": getattr(run, "run_key", ""),
        "status": getattr(run, "status", ""),
        "planned_for": str(getattr(run, "planned_for", "") or ""),
        "started_at": str(getattr(run, "started_at", "") or ""),
        "finished_at": str(getattr(run, "finished_at", "") or ""),
        "error_message": getattr(run, "error_message", "") or "",
        "result_json": dict(getattr(run, "result_json", {}) or {}),
        "request_id": getattr(run, "request_id", "") or "",
        "idempotency_key": getattr(run, "idempotency_key", "") or "",
        "resume_token": getattr(run, "resume_token", "") or "",
        "attempt_number": int(getattr(run, "attempt_number", 1) or 1),
        "created_at": str(getattr(run, "created_at", "") or ""),
        "updated_at": str(getattr(run, "updated_at", "") or ""),
    }


def create_schedule(
    db,
    *,
    workspace_id: str,
    schedule: ScheduleSpec | dict,
    payload: ScheduledTaskPayload | dict,
    connector_context: ConnectorContext | dict | None = None,
    retry_policy: RetryPolicy | dict | None = None,
    execution_policy: ExecutionPolicy | dict | None = None,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
    metadata_json: dict | None = None,
):
    schedule_spec = ScheduleSpec.from_value(schedule)
    task_payload = ScheduledTaskPayload.from_value(payload)
    retry = RetryPolicy.from_value(retry_policy)
    execution = ExecutionPolicy.from_value(execution_policy)
    connector = normalize_connector_context(connector_context)

    errors = [*validate_schedule_spec(schedule_spec), *_validate_target(task_payload)]
    if errors:
        raise ValueError(" ".join(errors))

    next_run_at = compute_next_run_at(schedule_spec, after=utc_now())
    return repo.create_scheduled_task(
        db,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        created_by_user_id=actor_user_id,
        membership_id=membership_id,
        target_kind=task_payload.target_kind,
        target_name=task_payload.target_name,
        schedule_type=schedule_spec.schedule_type,
        timezone=schedule_spec.timezone,
        start_at=parse_datetime(schedule_spec.start_at, schedule_spec.timezone),
        end_at=parse_datetime(schedule_spec.end_at, schedule_spec.timezone),
        next_run_at=next_run_at,
        cron_expression=schedule_spec.cron_expression,
        interval_seconds=schedule_spec.interval_seconds,
        payload_json=task_payload.to_dict(),
        connector_context_json=connector.to_dict(),
        execution_policy_json=execution.to_dict(),
        retry_policy_json=retry.to_dict(),
        metadata_json=dict(metadata_json or {}),
    )


def list_schedules(db, workspace_id: str, *, status: str = "", limit: int = 50) -> list[dict]:
    return [task_to_dict(task) for task in repo.list_scheduled_tasks(db, workspace_id, status=status, limit=limit)]


def update_schedule(
    db,
    task_id: str,
    *,
    schedule: ScheduleSpec | dict | None = None,
    payload: ScheduledTaskPayload | dict | None = None,
    connector_context: ConnectorContext | dict | None = None,
    retry_policy: RetryPolicy | dict | None = None,
    execution_policy: ExecutionPolicy | dict | None = None,
    metadata_json: dict | None = None,
):
    task = repo.get_scheduled_task(db, task_id)
    if not task:
        return None
    current_schedule = _task_schedule_dict(task)
    schedule_spec = ScheduleSpec.from_value({**current_schedule, **_as_dict(schedule)})
    current_payload = dict(getattr(task, "payload_json", {}) or {})
    task_payload = ScheduledTaskPayload.from_value({**current_payload, **_as_dict(payload)})
    errors = [*validate_schedule_spec(schedule_spec), *_validate_target(task_payload)]
    if errors:
        raise ValueError(" ".join(errors))
    connector = normalize_connector_context(
        connector_context if connector_context is not None else dict(getattr(task, "connector_context_json", {}) or {})
    )
    retry = RetryPolicy.from_value(retry_policy if retry_policy is not None else dict(getattr(task, "retry_policy_json", {}) or {}))
    execution = ExecutionPolicy.from_value(
        execution_policy if execution_policy is not None else dict(getattr(task, "execution_policy_json", {}) or {})
    )
    next_run_at = compute_next_run_at(schedule_spec, after=utc_now()) if getattr(task, "status", "") == "active" else getattr(task, "next_run_at", None)
    return repo.update_scheduled_task(
        db,
        task_id,
        target_kind=task_payload.target_kind,
        target_name=task_payload.target_name,
        schedule_type=schedule_spec.schedule_type,
        timezone=schedule_spec.timezone,
        start_at=parse_datetime(schedule_spec.start_at, schedule_spec.timezone),
        end_at=parse_datetime(schedule_spec.end_at, schedule_spec.timezone),
        cron_expression=schedule_spec.cron_expression,
        interval_seconds=schedule_spec.interval_seconds,
        next_run_at=next_run_at,
        payload_json=task_payload.to_dict(),
        connector_context_json=connector.to_dict(),
        retry_policy_json=retry.to_dict(),
        execution_policy_json=execution.to_dict(),
        metadata_json=metadata_json if metadata_json is not None else dict(getattr(task, "metadata_json", {}) or {}),
    )


def pause_schedule(db, task_id: str):
    return repo.set_scheduled_task_status(db, task_id, "paused")


def cancel_schedule(db, task_id: str):
    return repo.update_scheduled_task(db, task_id, status="cancelled", next_run_at=None)


def resume_schedule(db, task_id: str):
    task = repo.get_scheduled_task(db, task_id)
    if not task:
        return None
    next_run_at = getattr(task, "next_run_at", None) or compute_next_run_at(_task_schedule_dict(task), after=utc_now())
    return repo.update_scheduled_task(db, task_id, status="active", next_run_at=next_run_at)


def list_runs(db, *, workspace_id: str = "", scheduled_task_id: str = "", limit: int = 50) -> list[dict]:
    return [
        run_to_dict(run)
        for run in repo.list_scheduled_task_runs(
            db,
            workspace_id=workspace_id,
            scheduled_task_id=scheduled_task_id,
            limit=limit,
        )
    ]


def enqueue_run_for_task(db, task, *, planned_for: datetime | None = None):
    planned = (planned_for or getattr(task, "next_run_at", None) or utc_now()).astimezone(UTC)
    payload = dict(getattr(task, "payload_json", {}) or {})
    run_key = build_run_key(getattr(task, "id", ""), planned, getattr(task, "target_kind", ""), getattr(task, "target_name", ""))
    idempotency_key = build_run_idempotency_key(getattr(task, "id", ""), planned, payload)
    run = repo.create_scheduled_task_run(
        db,
        scheduled_task_id=getattr(task, "id", ""),
        workspace_id=getattr(task, "workspace_id", ""),
        actor_user_id=getattr(task, "actor_user_id", None),
        membership_id=getattr(task, "membership_id", None),
        planned_for=planned,
        run_key=run_key,
        idempotency_key=idempotency_key,
        status="queued",
    )
    next_run_at = compute_next_run_at(_task_schedule_dict(task), after=utc_now(), previous_run_at=planned)
    status = "archived" if getattr(task, "schedule_type", "") == "once" else None
    repo.update_scheduled_task(
        db,
        getattr(task, "id", ""),
        status=status,
        next_run_at=next_run_at,
        last_run_at=planned,
    )
    return run


def enqueue_due_runs(db, *, due_at: datetime | None = None, limit: int = 20) -> list[dict]:
    now = due_at or utc_now()
    runs = []
    for task in repo.list_due_scheduled_tasks(db, now, limit=limit):
        runs.append(run_to_dict(enqueue_run_for_task(db, task)))
    return runs


def run_now(db, task_id: str):
    task = repo.get_scheduled_task(db, task_id)
    if not task:
        return None
    return enqueue_run_for_task(db, task, planned_for=utc_now())


def validate_task_connector(db, task) -> tuple[dict, str]:
    connector_context = dict(getattr(task, "connector_context_json", {}) or {})
    connector = normalize_connector_context(connector_context)
    if connector.is_auto():
        return connector.to_dict(), ""
    normalized, _status, error = validate_connector_context(
        connector,
        getattr(task, "workspace_id", ""),
        db,
        request_cache={},
        refresh=False,
    )
    return normalized.to_dict(), error


def complete_run_from_resume(db, scheduled_run_id: str | None, result: dict | None):
    """Update a scheduled run after an auth/approval resume completes."""
    if not scheduled_run_id:
        return None
    payload = dict(result or {})
    if payload.get("approval_required") or payload.get("pending_kind") == "approval":
        status = "approval_required"
        finished_at = None
    elif payload.get("mode") in {"connect_required", "auth_unavailable", "invalid_tool", "validation_error", "tool_error"} or payload.get("error"):
        status = "failed"
        finished_at = utc_now()
    else:
        status = "succeeded"
        finished_at = utc_now()
    return repo.update_scheduled_task_run(
        db,
        scheduled_run_id,
        status=status,
        result_json=payload,
        error_message=str(payload.get("error") or payload.get("output", "") if status == "failed" else ""),
        resume_token=str(payload.get("resume_token", "") or ""),
        idempotency_key=str(payload.get("idempotency_key", "") or ""),
        finished_at=finished_at,
    )
