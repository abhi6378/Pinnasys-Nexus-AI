from __future__ import annotations

import argparse
import logging
import os
import time
import uuid
from datetime import timedelta

from models.contracts import RetryPolicy, ScheduledTaskPayload
from orchestrator.handler import handle_request
from storage import repositories as repo
from storage.db import SessionLocal, init_db
from utils.logging_utils import configure_logging, log_event
from utils.perf import elapsed_ms, perf_counter
from utils.time_utils import utc_now

from automation import service


logger = logging.getLogger(__name__)


def _build_resume_state(task, run) -> dict:
    return {
        "request_id": getattr(run, "request_id", "") or str(uuid.uuid4()),
        "scheduled_task_id": getattr(task, "id", ""),
        "scheduled_run_id": getattr(run, "id", ""),
        "automation_run_id": getattr(run, "id", ""),
        "idempotency_key": getattr(run, "idempotency_key", "") or "",
        "actor_user_id": getattr(run, "actor_user_id", None) or getattr(task, "actor_user_id", None),
        "membership_id": getattr(run, "membership_id", None) or getattr(task, "membership_id", None),
        "connector_context": dict(getattr(task, "connector_context_json", {}) or {}),
        "automation": True,
    }


def execute_run(db, run_id: str) -> dict:
    started = perf_counter()
    claimed = repo.claim_scheduled_task_run(db, run_id)
    if not claimed or getattr(claimed, "status", "") != "running":
        status = getattr(claimed, "status", "not_found") if claimed else "not_found"
        log_event(logger, logging.INFO, "automation.worker.run", run_id=run_id, status=status, duration_ms=elapsed_ms(started))
        return {"status": status}

    task = repo.get_scheduled_task(db, getattr(claimed, "scheduled_task_id", ""))
    if not task:
        repo.update_scheduled_task_run(
            db,
            run_id,
            status="failed",
            error_message="Scheduled task definition was not found.",
            finished_at=utc_now(),
        )
        result = {"status": "failed", "error_message": "Scheduled task definition was not found."}
        log_event(logger, logging.INFO, "automation.worker.run", run_id=run_id, status="failed", duration_ms=elapsed_ms(started))
        return result

    connector_started = perf_counter()
    connector_context, connector_error = service.validate_task_connector(db, task)
    log_event(
        logger,
        logging.INFO,
        "automation.worker.connector_validate",
        run_id=run_id,
        task_id=getattr(task, "id", ""),
        has_error=bool(connector_error),
        duration_ms=elapsed_ms(connector_started),
    )
    if connector_error:
        repo.update_scheduled_task_run(
            db,
            run_id,
            status="failed",
            error_message=connector_error,
            result_json={"connector_context": connector_context},
            finished_at=utc_now(),
        )
        result = {"status": "failed", "error_message": connector_error}
        log_event(logger, logging.INFO, "automation.worker.run", run_id=run_id, status="failed", duration_ms=elapsed_ms(started))
        return result

    payload = ScheduledTaskPayload.from_value(dict(getattr(task, "payload_json", {}) or {}))
    resume_state = _build_resume_state(task, claimed)
    resume_state["connector_context"] = connector_context
    request_id = resume_state["request_id"]
    repo.update_scheduled_task_run(db, run_id, request_id=request_id)

    force_workflow = payload.force_workflow or (payload.target_name if payload.target_kind == "workflow" else "")
    force_agent = payload.force_agent or (payload.target_name if payload.target_kind == "agent" else "")
    if payload.target_kind == "direct_action" and not force_agent:
        force_agent = payload.target_name or "assistant"

    execution_started = perf_counter()
    result = handle_request(
        payload.user_input,
        getattr(task, "workspace_id", ""),
        db,
        force_agent=force_agent or None,
        force_workflow=force_workflow or None,
        resume_state=resume_state,
        connector_context=connector_context,
        actor_user_id=getattr(claimed, "actor_user_id", None) or getattr(task, "actor_user_id", None),
        membership_id=getattr(claimed, "membership_id", None) or getattr(task, "membership_id", None),
    )
    log_event(
        logger,
        logging.INFO,
        "automation.worker.execute_runtime",
        run_id=run_id,
        task_id=getattr(task, "id", ""),
        duration_ms=elapsed_ms(execution_started),
    )
    mode = str(result.get("mode", "") or "")
    errored = bool(result.get("error", False))
    if result.get("approval_required") or result.get("pending_kind") == "approval":
        status = "approval_required"
    elif mode in {"connect_required", "auth_unavailable", "invalid_tool", "validation_error", "tool_error"} or errored:
        status = "failed"
    else:
        status = "succeeded"

    repo.update_scheduled_task_run(
        db,
        run_id,
        status=status,
        result_json=result,
        error_message=str((result.get("error") or result.get("output", "")) if status == "failed" else ""),
        resume_token=str(result.get("resume_token", "") or ""),
        idempotency_key=str(result.get("idempotency_key", "") or getattr(claimed, "idempotency_key", "") or ""),
        finished_at=utc_now() if status != "approval_required" else None,
    )
    if status == "failed":
        retry = RetryPolicy.from_value(dict(getattr(task, "retry_policy_json", {}) or {}))
        attempt_number = int(getattr(claimed, "attempt_number", 1) or 1)
        if attempt_number < retry.max_attempts:
            planned_for = utc_now() + timedelta(seconds=retry.backoff_seconds)
            repo.create_scheduled_task_run(
                db,
                scheduled_task_id=getattr(task, "id", ""),
                workspace_id=getattr(task, "workspace_id", ""),
                actor_user_id=getattr(claimed, "actor_user_id", None) or getattr(task, "actor_user_id", None),
                membership_id=getattr(claimed, "membership_id", None) or getattr(task, "membership_id", None),
                planned_for=planned_for,
                run_key=f"{getattr(claimed, 'run_key', '')}:retry:{attempt_number + 1}",
                idempotency_key=getattr(claimed, "idempotency_key", "") or "",
                attempt_number=attempt_number + 1,
                status="queued",
            )
    log_event(
        logger,
        logging.INFO,
        "automation.worker.run",
        run_id=run_id,
        task_id=getattr(task, "id", ""),
        status=status,
        duration_ms=elapsed_ms(started),
    )
    return {"status": status, "result": result}


def execute_queued_runs(*, batch_size: int = 10) -> list[dict]:
    started = perf_counter()
    db = SessionLocal()
    try:
        runs = repo.list_queued_scheduled_task_runs(db, due_at=utc_now(), limit=batch_size)
        results = []
        for run in runs:
            results.append(execute_run(db, getattr(run, "id", "")))
        log_event(
            logger,
            logging.INFO,
            "automation.worker.batch",
            run_count=len(results),
            batch_size=batch_size,
            duration_ms=elapsed_ms(started),
        )
        return results
    finally:
        db.close()


def worker_loop(*, poll_seconds: int = 10, batch_size: int = 10, with_scheduler: bool = False) -> None:
    from automation.scheduler import enqueue_due_once

    while True:
        if with_scheduler:
            enqueue_due_once(batch_size=batch_size)
        results = execute_queued_runs(batch_size=batch_size)
        log_event(logger, logging.INFO, "automation.worker.tick", run_count=len(results))
        time.sleep(max(1, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sintra automation worker.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--with-scheduler", action="store_true", help="Also enqueue due runs before each worker tick.")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("SINTRA_WORKER_POLL_SECONDS", "10") or "10"))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SINTRA_WORKER_BATCH_SIZE", "10") or "10"))
    args = parser.parse_args()
    configure_logging()
    init_db()
    if args.once:
        if args.with_scheduler:
            from automation.scheduler import enqueue_due_once

            enqueue_due_once(batch_size=args.batch_size)
        execute_queued_runs(batch_size=args.batch_size)
        return
    worker_loop(poll_seconds=args.poll_seconds, batch_size=args.batch_size, with_scheduler=args.with_scheduler)


if __name__ == "__main__":
    main()
