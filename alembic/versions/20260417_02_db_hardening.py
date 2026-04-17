"""database hardening control-plane pass

Revision ID: 20260417_02
Revises: 20260417_01
Create Date: 2026-04-17 00:10:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260417_02"
down_revision = "20260417_01"
branch_labels = None
depends_on = None


def _safe_create_index(name: str, table: str, columns: list[str], *, unique: bool = False, where: str | None = None) -> None:
    if where:
        op.create_index(name, table, columns, unique=unique, postgresql_where=sa.text(where))
    else:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    # Expand tables first.
    op.add_column("workspaces", sa.Column("owner_user_id", sa.String(), nullable=True))
    op.add_column("conversations", sa.Column("request_id", sa.String(), nullable=False, server_default=""))
    op.add_column("conversations", sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("workflow_runs", sa.Column("status", sa.String(), nullable=False, server_default="completed"))
    op.add_column("workflow_runs", sa.Column("request_id", sa.String(), nullable=False, server_default=""))
    op.add_column("workflow_runs", sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("workflow_runs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pending_tool_requests", sa.Column("pending_kind", sa.String(), nullable=False, server_default="auth"))
    op.add_column("pending_tool_requests", sa.Column("idempotency_key", sa.String(), nullable=False, server_default=""))
    op.add_column("pending_tool_requests", sa.Column("approval_requirement_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("pending_tool_requests", sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("pending_tool_requests", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pending_tool_requests", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tool_call_logs", sa.Column("idempotency_key", sa.String(), nullable=False, server_default=""))
    op.add_column("tool_call_logs", sa.Column("pending_kind", sa.String(), nullable=False, server_default=""))
    op.add_column("tool_call_logs", sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tool_connections", sa.Column("account_label", sa.String(), nullable=False, server_default=""))
    op.add_column("tool_connections", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tool_connections", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tool_connections", sa.Column("last_seen_remote_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tool_connections", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tool_connections", sa.Column("status_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("tool_connections", sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True, server_default=""),
        sa.Column("avatar_url", sa.String(), nullable=True, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, server_default=""),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Normalize timestamps to timestamptz, treating existing naive values as UTC.
    for table_name, columns in {
        "workspaces": ["created_at"],
        "brain_profiles": ["updated_at"],
        "knowledge_items": ["created_at"],
        "quiz_answers": ["created_at"],
        "conversations": ["created_at"],
        "workflow_runs": ["created_at", "updated_at"],
        "ideas": ["created_at"],
        "memory_records": ["last_accessed_at", "created_at", "updated_at"],
        "working_memory_states": ["updated_at"],
        "memory_embeddings": ["created_at", "updated_at"],
        "workspace_connector_preferences": ["created_at", "updated_at"],
        "tool_connections": ["created_at", "updated_at", "last_verified_at", "last_seen_remote_at", "revoked_at", "status_updated_at"],
        "pending_tool_requests": ["created_at", "updated_at", "approved_at", "expires_at"],
        "tool_call_logs": ["created_at"],
        "tool_idempotency_records": ["created_at", "updated_at", "completed_at"],
        "users": ["created_at", "updated_at"],
        "external_identities": ["created_at", "updated_at"],
        "workspace_memberships": ["created_at", "updated_at"],
    }.items():
        for column_name in columns:
            op.execute(
                sa.text(
                    f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                    f"TYPE TIMESTAMPTZ USING {column_name} AT TIME ZONE 'UTC'"
                )
            )

    # Backfill new workflow/request metadata.
    op.execute(sa.text("UPDATE workflow_runs SET updated_at = COALESCE(updated_at, created_at), status = COALESCE(NULLIF(status, ''), 'completed')"))
    op.execute(sa.text("UPDATE ideas SET status = COALESCE(NULLIF(status, ''), 'pending')"))
    op.execute(sa.text("UPDATE workspace_connector_preferences SET mode = COALESCE(NULLIF(mode, ''), 'auto')"))
    op.execute(sa.text("UPDATE tool_connections SET status = COALESCE(NULLIF(status, ''), 'pending'), auth_mode = COALESCE(NULLIF(auth_mode, ''), 'oauth2')"))
    op.execute(sa.text("UPDATE tool_connections SET status_updated_at = COALESCE(status_updated_at, updated_at, created_at)"))
    op.execute(sa.text("UPDATE pending_tool_requests SET status = COALESCE(NULLIF(status, ''), 'pending'), pending_kind = COALESCE(NULLIF(pending_kind, ''), 'auth')"))
    op.execute(sa.text("UPDATE pending_tool_requests SET expires_at = COALESCE(expires_at, created_at + INTERVAL '72 hours')"))
    op.execute(sa.text("UPDATE tool_call_logs SET status = COALESCE(NULLIF(status, ''), 'failure'), pending_kind = COALESCE(pending_kind, '')"))
    op.execute(sa.text("UPDATE tool_idempotency_records SET status = COALESCE(NULLIF(status, ''), 'pending')"))
    op.execute(sa.text("UPDATE tool_idempotency_records SET pending_request_id = NULL WHERE pending_request_id = ''"))
    op.execute(sa.text("UPDATE tool_idempotency_records SET tool_call_log_id = NULL WHERE tool_call_log_id = ''"))

    # Create backup tables for safe dedupe/rollback before enforcing uniqueness.
    op.execute(sa.text("CREATE TABLE IF NOT EXISTS tool_connections_dedupe_backup AS SELECT * FROM tool_connections WHERE 1=0"))
    op.execute(sa.text("CREATE TABLE IF NOT EXISTS memory_record_dedupe_backup AS SELECT * FROM memory_records WHERE 1=0"))

    # Dedupe connector rows by runtime identity.
    op.execute(
        sa.text(
            """
            INSERT INTO tool_connections_dedupe_backup
            SELECT * FROM tool_connections
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id, toolkit, COALESCE(connected_account_id, '')
                               ORDER BY COALESCE(last_verified_at, updated_at, created_at) DESC, created_at DESC, id DESC
                           ) AS row_rank
                    FROM tool_connections
                ) ranked
                WHERE ranked.row_rank > 1
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM tool_connections
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id, toolkit, COALESCE(connected_account_id, '')
                               ORDER BY COALESCE(last_verified_at, updated_at, created_at) DESC, created_at DESC, id DESC
                           ) AS row_rank
                    FROM tool_connections
                ) ranked
                WHERE ranked.row_rank > 1
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH chosen_defaults AS (
                SELECT id
                FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id, toolkit
                               ORDER BY CASE WHEN is_default THEN 0 ELSE 1 END,
                                        COALESCE(last_verified_at, updated_at, created_at) DESC,
                                        created_at DESC
                           ) AS row_rank
                    FROM tool_connections
                    WHERE status = 'connected' AND revoked_at IS NULL
                ) ranked
                WHERE row_rank = 1
            )
            UPDATE tool_connections
            SET is_default = CASE WHEN id IN (SELECT id FROM chosen_defaults) THEN TRUE ELSE FALSE END
            WHERE status = 'connected'
            """
        )
    )

    # Dedupe active canonical memories.
    op.execute(
        sa.text(
            """
            INSERT INTO memory_record_dedupe_backup
            SELECT * FROM memory_records
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id, canonical_key
                               ORDER BY updated_at DESC, created_at DESC, id DESC
                           ) AS row_rank
                    FROM memory_records
                    WHERE canonical_key <> '' AND superseded_by = ''
                ) ranked
                WHERE ranked.row_rank > 1
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM memory_records
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY workspace_id, canonical_key
                               ORDER BY updated_at DESC, created_at DESC, id DESC
                           ) AS row_rank
                    FROM memory_records
                    WHERE canonical_key <> '' AND superseded_by = ''
                ) ranked
                WHERE ranked.row_rank > 1
            )
            """
        )
    )

    # Add FK/constraint/index guarantees.
    op.create_foreign_key("fk_workspaces_owner_user_id", "workspaces", "users", ["owner_user_id"], ["id"])
    op.create_foreign_key("fk_memory_embeddings_memory_record", "memory_embeddings", "memory_records", ["memory_record_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_tool_idempotency_pending_request", "tool_idempotency_records", "pending_tool_requests", ["pending_request_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_tool_idempotency_tool_call_log", "tool_idempotency_records", "tool_call_logs", ["tool_call_log_id"], ["id"], ondelete="SET NULL")

    _safe_create_index("ix_users_email", "users", ["email"])
    _safe_create_index("uq_external_identities_provider_subject", "external_identities", ["provider", "provider_subject"], unique=True)
    _safe_create_index("ix_external_identities_user_id", "external_identities", ["user_id"])
    _safe_create_index("uq_workspace_memberships_workspace_user", "workspace_memberships", ["workspace_id", "user_id"], unique=True)
    _safe_create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    _safe_create_index("ix_conversations_workspace_created_at", "conversations", ["workspace_id", "created_at"])
    _safe_create_index("ix_workflow_runs_workspace_created_at", "workflow_runs", ["workspace_id", "created_at"])
    _safe_create_index("ix_workflow_runs_workspace_status_updated", "workflow_runs", ["workspace_id", "status", "updated_at"])
    _safe_create_index("ix_memory_records_workspace_type_updated", "memory_records", ["workspace_id", "memory_type", "updated_at"])
    _safe_create_index(
        "uq_memory_records_active_canonical_key",
        "memory_records",
        ["workspace_id", "canonical_key"],
        unique=True,
        where="canonical_key <> '' AND superseded_by = ''",
    )
    _safe_create_index("uq_memory_embeddings_memory_model", "memory_embeddings", ["memory_record_id", "model_name"], unique=True)
    _safe_create_index("ix_memory_embeddings_workspace_model_updated", "memory_embeddings", ["workspace_id", "model_name", "updated_at"])
    _safe_create_index("ix_tool_connections_workspace_toolkit_status_default_updated", "tool_connections", ["workspace_id", "toolkit", "status", "is_default", "updated_at"])
    _safe_create_index("uq_tool_connections_workspace_toolkit_account", "tool_connections", ["workspace_id", "toolkit", "connected_account_id"], unique=True)
    _safe_create_index("ix_tool_connections_workspace_toolkit_account", "tool_connections", ["workspace_id", "toolkit", "connected_account_id"])
    _safe_create_index(
        "uq_tool_connections_single_default_active",
        "tool_connections",
        ["workspace_id", "toolkit"],
        unique=True,
        where="is_default = TRUE AND status = 'connected' AND revoked_at IS NULL",
    )
    _safe_create_index("ix_pending_tool_requests_workspace_status_updated", "pending_tool_requests", ["workspace_id", "status", "updated_at"])
    _safe_create_index("ix_pending_tool_requests_workspace_kind_status_updated", "pending_tool_requests", ["workspace_id", "pending_kind", "status", "updated_at"])
    _safe_create_index("ix_pending_tool_requests_expires_at", "pending_tool_requests", ["expires_at"])
    _safe_create_index("ix_tool_call_logs_workspace_status_created", "tool_call_logs", ["workspace_id", "status", "created_at"])
    _safe_create_index("ix_tool_call_logs_workspace_tool_created", "tool_call_logs", ["workspace_id", "tool_name", "created_at"])
    _safe_create_index("uq_tool_idempotency_workspace_tool_key", "tool_idempotency_records", ["workspace_id", "tool_name", "idempotency_key"], unique=True)

    op.create_check_constraint("ck_ideas_status", "ideas", "status IN ('pending', 'accepted', 'rejected')")
    op.create_check_constraint("ck_workflow_runs_status", "workflow_runs", "status IN ('pending', 'running', 'paused', 'completed', 'failed', 'cancelled')")
    op.create_check_constraint("ck_workspace_connector_preferences_mode", "workspace_connector_preferences", "mode IN ('auto', 'manual')")
    op.create_check_constraint("ck_users_status", "users", "status IN ('active', 'invited', 'disabled')")
    op.create_check_constraint("ck_workspace_memberships_role", "workspace_memberships", "role IN ('owner', 'admin', 'member', 'viewer')")
    op.create_check_constraint("ck_workspace_memberships_status", "workspace_memberships", "status IN ('active', 'invited', 'suspended', 'removed')")
    op.create_check_constraint("ck_tool_connections_status", "tool_connections", "status IN ('connected', 'pending', 'revoked', 'error', 'not_found')")
    op.create_check_constraint("ck_tool_connections_auth_mode", "tool_connections", "auth_mode IN ('oauth2', 'api_key', 'jwt', 'none')")
    op.create_check_constraint("ck_pending_tool_requests_status", "pending_tool_requests", "status IN ('pending', 'resumed', 'completed', 'expired', 'cancelled')")
    op.create_check_constraint("ck_pending_tool_requests_pending_kind", "pending_tool_requests", "pending_kind IN ('auth', 'approval')")
    op.create_check_constraint("ck_tool_call_logs_status", "tool_call_logs", "status IN ('success', 'failure', 'connect_required', 'auth_unavailable', 'validation_error', 'timeout', 'invalid_tool')")
    op.create_check_constraint("ck_tool_call_logs_pending_kind", "tool_call_logs", "pending_kind IN ('', 'auth', 'approval')")
    op.create_check_constraint("ck_tool_idempotency_records_status", "tool_idempotency_records", "status IN ('pending', 'pending_auth', 'pending_approval', 'in_progress', 'success', 'failure')")

    # Convert JSON columns introduced in baseline to JSONB for control-plane tables.
    for table_name, column_name in [
        ("conversations", "metadata_json"),
        ("workflow_runs", "metadata_json"),
        ("pending_tool_requests", "context_json"),
        ("pending_tool_requests", "approval_requirement_json"),
        ("tool_call_logs", "input_json"),
        ("tool_call_logs", "output_json"),
        ("tool_connections", "metadata_json"),
        ("tool_idempotency_records", "output_json"),
    ]:
        op.execute(
            sa.text(
                f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE JSONB USING {column_name}::jsonb"
            )
        )

    # Drop server defaults that only exist for migration/backfill safety.
    op.alter_column("conversations", "request_id", server_default=None)
    op.alter_column("conversations", "metadata_json", server_default=None)
    op.alter_column("workflow_runs", "status", server_default=None)
    op.alter_column("workflow_runs", "request_id", server_default=None)
    op.alter_column("workflow_runs", "metadata_json", server_default=None)
    op.alter_column("pending_tool_requests", "pending_kind", server_default=None)
    op.alter_column("pending_tool_requests", "idempotency_key", server_default=None)
    op.alter_column("pending_tool_requests", "approval_requirement_json", server_default=None)
    op.alter_column("pending_tool_requests", "approved", server_default=None)
    op.alter_column("tool_call_logs", "idempotency_key", server_default=None)
    op.alter_column("tool_call_logs", "pending_kind", server_default=None)
    op.alter_column("tool_call_logs", "approval_required", server_default=None)
    op.alter_column("tool_connections", "account_label", server_default=None)
    op.alter_column("tool_connections", "is_default", server_default=None)
    op.alter_column("tool_connections", "status_reason", server_default=None)
    op.alter_column("users", "display_name", server_default=None)
    op.alter_column("users", "avatar_url", server_default=None)
    op.alter_column("users", "status", server_default=None)
    op.alter_column("users", "metadata_json", server_default=None)
    op.alter_column("external_identities", "email", server_default=None)
    op.alter_column("external_identities", "metadata_json", server_default=None)
    op.alter_column("workspace_memberships", "role", server_default=None)
    op.alter_column("workspace_memberships", "status", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_tool_idempotency_records_status", "tool_idempotency_records", type_="check")
    op.drop_constraint("ck_tool_call_logs_pending_kind", "tool_call_logs", type_="check")
    op.drop_constraint("ck_tool_call_logs_status", "tool_call_logs", type_="check")
    op.drop_constraint("ck_pending_tool_requests_pending_kind", "pending_tool_requests", type_="check")
    op.drop_constraint("ck_pending_tool_requests_status", "pending_tool_requests", type_="check")
    op.drop_constraint("ck_tool_connections_auth_mode", "tool_connections", type_="check")
    op.drop_constraint("ck_tool_connections_status", "tool_connections", type_="check")
    op.drop_constraint("ck_workspace_memberships_status", "workspace_memberships", type_="check")
    op.drop_constraint("ck_workspace_memberships_role", "workspace_memberships", type_="check")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_constraint("ck_workspace_connector_preferences_mode", "workspace_connector_preferences", type_="check")
    op.drop_constraint("ck_workflow_runs_status", "workflow_runs", type_="check")
    op.drop_constraint("ck_ideas_status", "ideas", type_="check")

    for index_name, table_name in [
        ("uq_tool_idempotency_workspace_tool_key", "tool_idempotency_records"),
        ("ix_tool_call_logs_workspace_tool_created", "tool_call_logs"),
        ("ix_tool_call_logs_workspace_status_created", "tool_call_logs"),
        ("ix_pending_tool_requests_expires_at", "pending_tool_requests"),
        ("ix_pending_tool_requests_workspace_kind_status_updated", "pending_tool_requests"),
        ("ix_pending_tool_requests_workspace_status_updated", "pending_tool_requests"),
        ("uq_tool_connections_single_default_active", "tool_connections"),
        ("ix_tool_connections_workspace_toolkit_account", "tool_connections"),
        ("uq_tool_connections_workspace_toolkit_account", "tool_connections"),
        ("ix_tool_connections_workspace_toolkit_status_default_updated", "tool_connections"),
        ("ix_memory_embeddings_workspace_model_updated", "memory_embeddings"),
        ("uq_memory_embeddings_memory_model", "memory_embeddings"),
        ("uq_memory_records_active_canonical_key", "memory_records"),
        ("ix_memory_records_workspace_type_updated", "memory_records"),
        ("ix_workflow_runs_workspace_status_updated", "workflow_runs"),
        ("ix_workflow_runs_workspace_created_at", "workflow_runs"),
        ("ix_conversations_workspace_created_at", "conversations"),
        ("uq_workspace_memberships_workspace_user", "workspace_memberships"),
        ("ix_workspace_memberships_user_id", "workspace_memberships"),
        ("uq_external_identities_provider_subject", "external_identities"),
        ("ix_external_identities_user_id", "external_identities"),
        ("ix_users_email", "users"),
    ]:
        op.drop_index(index_name, table_name=table_name)

    op.drop_constraint("fk_tool_idempotency_tool_call_log", "tool_idempotency_records", type_="foreignkey")
    op.drop_constraint("fk_tool_idempotency_pending_request", "tool_idempotency_records", type_="foreignkey")
    op.drop_constraint("fk_memory_embeddings_memory_record", "memory_embeddings", type_="foreignkey")
    op.drop_constraint("fk_workspaces_owner_user_id", "workspaces", type_="foreignkey")

    op.drop_table("workspace_memberships")
    op.drop_table("external_identities")
    op.drop_table("users")

    for table_name, column_name in [
        ("tool_connections", "status_updated_at"),
        ("tool_connections", "status_reason"),
        ("tool_connections", "revoked_at"),
        ("tool_connections", "last_seen_remote_at"),
        ("tool_connections", "last_verified_at"),
        ("tool_connections", "is_default"),
        ("tool_connections", "account_label"),
        ("tool_call_logs", "approval_required"),
        ("tool_call_logs", "pending_kind"),
        ("tool_call_logs", "idempotency_key"),
        ("pending_tool_requests", "expires_at"),
        ("pending_tool_requests", "approved_at"),
        ("pending_tool_requests", "approved"),
        ("pending_tool_requests", "approval_requirement_json"),
        ("pending_tool_requests", "idempotency_key"),
        ("pending_tool_requests", "pending_kind"),
        ("workflow_runs", "updated_at"),
        ("workflow_runs", "metadata_json"),
        ("workflow_runs", "request_id"),
        ("workflow_runs", "status"),
        ("conversations", "metadata_json"),
        ("conversations", "request_id"),
        ("workspaces", "owner_user_id"),
    ]:
        op.drop_column(table_name, column_name)
