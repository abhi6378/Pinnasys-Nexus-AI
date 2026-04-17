"""baseline legacy schema

Revision ID: 20260417_01
Revises:
Create Date: 2026-04-17 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "20260417_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "brain_profiles",
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("brand_context", sa.Text(), nullable=True),
        sa.Column("tone", sa.String(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("goals", sa.Text(), nullable=True),
        sa.Column("services", sa.Text(), nullable=True),
        sa.Column("pricing", sa.Text(), nullable=True),
        sa.Column("competitors", sa.Text(), nullable=True),
        sa.Column("support_style", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "quiz_answers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("helper", sa.String(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("final_output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ideas",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_agent", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("workflow_hint", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("memory_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=True),
        sa.Column("source_reference_id", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("entity_tags", sa.JSON(), nullable=True),
        sa.Column("tool_tags", sa.JSON(), nullable=True),
        sa.Column("importance_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=True),
        sa.Column("canonical_key", sa.String(), nullable=True),
        sa.Column("superseded_by", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "working_memory_states",
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("current_goal", sa.Text(), nullable=True),
        sa.Column("active_tasks", sa.JSON(), nullable=True),
        sa.Column("open_questions", sa.JSON(), nullable=True),
        sa.Column("current_draft_summary", sa.Text(), nullable=True),
        sa.Column("recent_tool_summary", sa.Text(), nullable=True),
        sa.Column("latest_workflow_summary", sa.Text(), nullable=True),
        sa.Column("project_focus", sa.Text(), nullable=True),
        sa.Column("state_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("memory_record_id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("vector_json", sa.JSON(), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_connector_preferences",
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("selected_toolkit", sa.String(), nullable=True),
        sa.Column("selected_account_id", sa.String(), nullable=True),
        sa.Column("selected_account_alias", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_table(
        "tool_connections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("toolkit", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("connected_account_id", sa.String(), nullable=True),
        sa.Column("auth_mode", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pending_tool_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("agent_key", sa.String(), nullable=True),
        sa.Column("original_input", sa.Text(), nullable=False),
        sa.Column("requested_tool", sa.String(), nullable=False),
        sa.Column("requested_toolkit", sa.String(), nullable=True),
        sa.Column("resume_token", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_tool_requests_resume_token", "pending_tool_requests", ["resume_token"], unique=True)
    op.create_table(
        "tool_call_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("agent_key", sa.String(), nullable=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("toolkit", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tool_idempotency_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("input_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pending_request_id", sa.String(), nullable=True),
        sa.Column("tool_call_log_id", sa.String(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    for table_name in [
        "tool_idempotency_records",
        "tool_call_logs",
        "pending_tool_requests",
        "tool_connections",
        "workspace_connector_preferences",
        "memory_embeddings",
        "working_memory_states",
        "memory_records",
        "ideas",
        "workflow_runs",
        "conversations",
        "quiz_answers",
        "knowledge_items",
        "brain_profiles",
        "workspaces",
    ]:
        op.drop_table(table_name)
