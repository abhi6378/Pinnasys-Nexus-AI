# Durable Scheduling And Automation

Nexus Ai automations are database-backed schedules executed by separate scheduler
and worker processes. Streamlit and FastAPI can create and inspect schedules, but
they are not the authority for timekeeping.

## Runtime Shape

- `scheduled_tasks` stores the automation definition: owner workspace, optional
  actor/membership, schedule spec, workflow/agent target, connector context,
  retry policy, execution policy, and next run time.
- `scheduled_task_runs` stores each planned execution with a unique run key,
  idempotency key, status, result metadata, and approval/auth resume token when
  a run pauses.
- `python -m automation.scheduler` scans active due tasks and creates queued run
  records. It is safe to run more than one process because run keys are unique.
- `python -m automation.worker` claims queued runs with conditional updates and
  executes them through `orchestrator.handler.handle_request()`.
- `python -m automation.worker --with-scheduler` is a local development helper
  only. Production should run scheduler and worker as separate services.

## Schedule Semantics

Supported schedule types are `once`, `interval`, and `cron`.

- `once` requires `start_at`.
- `interval` requires `interval_seconds > 0`.
- `cron` requires `cron_expression` and the `croniter` package.
- Datetimes are timezone-aware and stored in UTC. Schedule interpretation uses
  the task `timezone` field.

## Execution Targets

The first-class target is `workflow`. The worker calls:

```python
handle_request(..., force_workflow=workflow_key)
```

`agent` targets call the same runtime with `force_agent`. `direct_action` is
schema-ready and still routes through agent/tool planning instead of bypassing
broker safety.

## Connectors, Approval, And Idempotency

Scheduled tasks store normalized connector context, not secrets. The worker
validates connector/account state before execution and records stale/missing
connector failures in run history.

Every run gets a deterministic idempotency key from task id, planned time, and
payload. Retries of the same planned run reuse the same key, while recurring
runs get distinct keys.

Risky writes continue through the existing approval gate. If a scheduled run
requires approval, the run becomes `approval_required` and stores the resume
token. Approval resume updates the scheduled run when execution completes.

## API And UI

FastAPI routes live under:

```text
/workspace/{workspace_id}/automations
```

They support create, list, get, update, pause, resume, cancel, run-now, and run
history. Streamlit has a small Automations page for workflow schedules and basic
run inspection.

## Deployment

1. Run Alembic migrations.
2. Deploy web/API code.
3. Start `python -m automation.scheduler`.
4. Start `python -m automation.worker`.

Existing chat, workflows, memory, connectors, approvals, and idempotency keep
working even if scheduler/worker processes are not running.

## Deferred

P1 items include a polished natural-language scheduling flow, richer campaign
builders, advanced retry/backoff policies, event-driven triggers, and admin
observability dashboards.
