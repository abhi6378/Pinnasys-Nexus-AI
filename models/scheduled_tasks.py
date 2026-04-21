"""
models/scheduled_tasks.py — Durable automation definitions and run history.

Automations are workspace-owned and optionally actor/membership-scoped. They
store only safe execution metadata; connector secrets and OAuth material remain
outside the app database.
"""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from storage.db import Base, TZDateTime, new_id
from utils.time_utils import utc_now


SCHEDULED_TASK_STATUSES = ("active", "paused", "cancelled", "archived")
SCHEDULED_RUN_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
    "approval_required",
)
SCHEDULE_TYPES = ("once", "interval", "cron")
AUTOMATION_TARGET_KINDS = ("workflow", "agent", "direct_action")


class ScheduledTaskModel(Base):
    __tablename__ = "scheduled_tasks"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    task_kind = Column(String, nullable=False, default="automation")
    target_kind = Column(String, nullable=False, default="workflow")
    target_name = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="active")
    schedule_type = Column(String, nullable=False, default="once")
    cron_expression = Column(String, default="")
    interval_seconds = Column(Integer, default=0)
    timezone = Column(String, nullable=False, default="UTC")
    start_at = Column(TZDateTime, nullable=True)
    end_at = Column(TZDateTime, nullable=True)
    next_run_at = Column(TZDateTime, nullable=True, index=True)
    last_run_at = Column(TZDateTime, nullable=True)
    connector_context_json = Column(JSONB, default=dict)
    payload_json = Column(JSONB, default=dict)
    execution_policy_json = Column(JSONB, default=dict)
    retry_policy_json = Column(JSONB, default=dict)
    metadata_json = Column(JSONB, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("ix_scheduled_tasks_due", "status", "next_run_at"),
        Index("ix_scheduled_tasks_workspace_status_next", "workspace_id", "status", "next_run_at"),
        Index("ix_scheduled_tasks_workspace_actor", "workspace_id", "actor_user_id"),
        CheckConstraint(
            "status IN ('active', 'paused', 'cancelled', 'archived')",
            name="ck_scheduled_tasks_status",
        ),
        CheckConstraint(
            "schedule_type IN ('once', 'interval', 'cron')",
            name="ck_scheduled_tasks_schedule_type",
        ),
        CheckConstraint(
            "target_kind IN ('workflow', 'agent', 'direct_action')",
            name="ck_scheduled_tasks_target_kind",
        ),
    )


class ScheduledTaskRunModel(Base):
    __tablename__ = "scheduled_task_runs"

    id = Column(String, primary_key=True, default=new_id)
    scheduled_task_id = Column(String, ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    run_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    planned_for = Column(TZDateTime, nullable=False)
    started_at = Column(TZDateTime, nullable=True)
    finished_at = Column(TZDateTime, nullable=True)
    error_message = Column(Text, default="")
    result_json = Column(JSONB, default=dict)
    request_id = Column(String, default="", index=True)
    idempotency_key = Column(String, default="")
    resume_token = Column(String, default="", index=True)
    attempt_number = Column(Integer, default=1)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("uq_scheduled_task_runs_task_planned", "scheduled_task_id", "planned_for", unique=True),
        Index("uq_scheduled_task_runs_run_key", "run_key", unique=True),
        Index("ix_scheduled_task_runs_status_created", "status", "created_at"),
        Index("ix_scheduled_task_runs_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_scheduled_task_runs_task_created", "scheduled_task_id", "created_at"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled', 'approval_required')",
            name="ck_scheduled_task_runs_status",
        ),
    )
