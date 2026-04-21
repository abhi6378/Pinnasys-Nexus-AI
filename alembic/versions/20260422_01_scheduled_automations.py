"""durable scheduled automations

Revision ID: 20260422_01
Revises: 20260417_04
Create Date: 2026-04-22 00:15:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260422_01"
down_revision = "20260417_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("membership_id", sa.String(), nullable=True),
        sa.Column("task_kind", sa.String(), nullable=False, server_default="automation"),
        sa.Column("target_kind", sa.String(), nullable=False, server_default="workflow"),
        sa.Column("target_name", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("schedule_type", sa.String(), nullable=False, server_default="once"),
        sa.Column("cron_expression", sa.String(), nullable=False, server_default=""),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connector_context_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("execution_policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("retry_policy_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["membership_id"], ["workspace_memberships.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('active', 'paused', 'cancelled', 'archived')", name="ck_scheduled_tasks_status"),
        sa.CheckConstraint("schedule_type IN ('once', 'interval', 'cron')", name="ck_scheduled_tasks_schedule_type"),
        sa.CheckConstraint("target_kind IN ('workflow', 'agent', 'direct_action')", name="ck_scheduled_tasks_target_kind"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_tasks_workspace_id", "scheduled_tasks", ["workspace_id"])
    op.create_index("ix_scheduled_tasks_actor_user_id", "scheduled_tasks", ["actor_user_id"])
    op.create_index("ix_scheduled_tasks_membership_id", "scheduled_tasks", ["membership_id"])
    op.create_index("ix_scheduled_tasks_next_run_at", "scheduled_tasks", ["next_run_at"])
    op.create_index("ix_scheduled_tasks_due", "scheduled_tasks", ["status", "next_run_at"])
    op.create_index("ix_scheduled_tasks_workspace_status_next", "scheduled_tasks", ["workspace_id", "status", "next_run_at"])
    op.create_index("ix_scheduled_tasks_workspace_actor", "scheduled_tasks", ["workspace_id", "actor_user_id"])

    op.create_table(
        "scheduled_task_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scheduled_task_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("membership_id", sa.String(), nullable=True),
        sa.Column("run_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("planned_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("request_id", sa.String(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(), nullable=False, server_default=""),
        sa.Column("resume_token", sa.String(), nullable=False, server_default=""),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scheduled_task_id"], ["scheduled_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["membership_id"], ["workspace_memberships.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled', 'approval_required')",
            name="ck_scheduled_task_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_task_runs_scheduled_task_id", "scheduled_task_runs", ["scheduled_task_id"])
    op.create_index("ix_scheduled_task_runs_workspace_id", "scheduled_task_runs", ["workspace_id"])
    op.create_index("ix_scheduled_task_runs_actor_user_id", "scheduled_task_runs", ["actor_user_id"])
    op.create_index("ix_scheduled_task_runs_membership_id", "scheduled_task_runs", ["membership_id"])
    op.create_index("ix_scheduled_task_runs_request_id", "scheduled_task_runs", ["request_id"])
    op.create_index("ix_scheduled_task_runs_resume_token", "scheduled_task_runs", ["resume_token"])
    op.create_index("uq_scheduled_task_runs_task_planned", "scheduled_task_runs", ["scheduled_task_id", "planned_for"], unique=True)
    op.create_index("uq_scheduled_task_runs_run_key", "scheduled_task_runs", ["run_key"], unique=True)
    op.create_index("ix_scheduled_task_runs_status_created", "scheduled_task_runs", ["status", "created_at"])
    op.create_index("ix_scheduled_task_runs_workspace_status_created", "scheduled_task_runs", ["workspace_id", "status", "created_at"])
    op.create_index("ix_scheduled_task_runs_task_created", "scheduled_task_runs", ["scheduled_task_id", "created_at"])

    for table, columns in {
        "scheduled_tasks": [
            "task_kind",
            "target_kind",
            "target_name",
            "status",
            "schedule_type",
            "cron_expression",
            "interval_seconds",
            "timezone",
            "connector_context_json",
            "payload_json",
            "execution_policy_json",
            "retry_policy_json",
            "metadata_json",
        ],
        "scheduled_task_runs": [
            "status",
            "error_message",
            "result_json",
            "request_id",
            "idempotency_key",
            "resume_token",
            "attempt_number",
        ],
    }.items():
        for column in columns:
            op.alter_column(table, column, server_default=None)


def downgrade() -> None:
    for index_name in (
        "ix_scheduled_task_runs_task_created",
        "ix_scheduled_task_runs_workspace_status_created",
        "ix_scheduled_task_runs_status_created",
        "uq_scheduled_task_runs_run_key",
        "uq_scheduled_task_runs_task_planned",
        "ix_scheduled_task_runs_resume_token",
        "ix_scheduled_task_runs_request_id",
        "ix_scheduled_task_runs_membership_id",
        "ix_scheduled_task_runs_actor_user_id",
        "ix_scheduled_task_runs_workspace_id",
        "ix_scheduled_task_runs_scheduled_task_id",
    ):
        op.drop_index(index_name, table_name="scheduled_task_runs")
    op.drop_table("scheduled_task_runs")

    for index_name in (
        "ix_scheduled_tasks_workspace_actor",
        "ix_scheduled_tasks_workspace_status_next",
        "ix_scheduled_tasks_due",
        "ix_scheduled_tasks_next_run_at",
        "ix_scheduled_tasks_membership_id",
        "ix_scheduled_tasks_actor_user_id",
        "ix_scheduled_tasks_workspace_id",
    ):
        op.drop_index(index_name, table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
