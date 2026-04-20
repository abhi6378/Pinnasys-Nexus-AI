"""ownership and scope hardening

Revision ID: 20260417_03
Revises: 20260417_02
Create Date: 2026-04-17 00:30:00
"""
from alembic import op


revision = "20260417_03"
down_revision = "20260417_02"
branch_labels = None
depends_on = None


WORKSPACE_FKS = {
    "brain_profiles": "workspace_id",
    "knowledge_items": "workspace_id",
    "quiz_answers": "workspace_id",
    "conversations": "workspace_id",
    "workflow_runs": "workspace_id",
    "ideas": "workspace_id",
    "memory_records": "workspace_id",
    "working_memory_states": "workspace_id",
    "memory_embeddings": "workspace_id",
    "workspace_connector_preferences": "workspace_id",
    "tool_connections": "workspace_id",
    "pending_tool_requests": "workspace_id",
    "tool_call_logs": "workspace_id",
    "tool_idempotency_records": "workspace_id",
}


def _constraint_exists(name: str) -> str:
    return f"SELECT 1 FROM pg_constraint WHERE conname = '{name}'"


def _add_fk_not_valid(name: str, table: str, column: str, target: str, on_delete: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS ({_constraint_exists(name)}) THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {name}
                FOREIGN KEY ({column}) REFERENCES {target}
                ON DELETE {on_delete}
                NOT VALID;
            END IF;
        END $$;
        """
    )


def _drop_constraint(name: str, table: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def _create_index_if_missing(name: str, table: str, columns: str) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")


def upgrade() -> None:
    # Expand connector preference rows so today's workspace default can later
    # become a workspace/user/membership hierarchy without another rewrite.
    op.execute(
        "ALTER TABLE workspace_connector_preferences "
        "ADD COLUMN IF NOT EXISTS scope_type VARCHAR NOT NULL DEFAULT 'workspace'"
    )
    op.execute("ALTER TABLE workspace_connector_preferences ADD COLUMN IF NOT EXISTS user_id VARCHAR")
    op.execute("ALTER TABLE workspace_connector_preferences ADD COLUMN IF NOT EXISTS membership_id VARCHAR")
    op.execute("ALTER TABLE workspace_connector_preferences ADD COLUMN IF NOT EXISTS selected_by_user_id VARCHAR")

    op.execute("ALTER TABLE tool_connections ALTER COLUMN user_id DROP NOT NULL")
    op.execute(
        """
        UPDATE tool_connections tc
        SET user_id = NULL
        WHERE user_id IS NOT NULL
          AND user_id <> ''
          AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = tc.user_id)
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_workspace_connector_preferences_scope_type') THEN
                ALTER TABLE workspace_connector_preferences
                ADD CONSTRAINT ck_workspace_connector_preferences_scope_type
                CHECK (scope_type IN ('workspace', 'user', 'membership'));
            END IF;
        END $$;
        """
    )

    _create_index_if_missing("ix_workspace_connector_preferences_user_id", "workspace_connector_preferences", "user_id")
    _create_index_if_missing("ix_workspace_connector_preferences_membership_id", "workspace_connector_preferences", "membership_id")
    _create_index_if_missing("ix_workspace_connector_preferences_selected_by_user_id", "workspace_connector_preferences", "selected_by_user_id")

    for table, column in WORKSPACE_FKS.items():
        _add_fk_not_valid(f"fk_{table}_workspace", table, column, "workspaces(id)", "CASCADE")

    _add_fk_not_valid("fk_tool_connections_user", "tool_connections", "user_id", "users(id)", "SET NULL")
    _add_fk_not_valid("fk_workspace_connector_preferences_user", "workspace_connector_preferences", "user_id", "users(id)", "SET NULL")
    _add_fk_not_valid(
        "fk_workspace_connector_preferences_membership",
        "workspace_connector_preferences",
        "membership_id",
        "workspace_memberships(id)",
        "SET NULL",
    )
    _add_fk_not_valid(
        "fk_workspace_connector_preferences_selected_by_user",
        "workspace_connector_preferences",
        "selected_by_user_id",
        "users(id)",
        "SET NULL",
    )


def downgrade() -> None:
    _drop_constraint("fk_workspace_connector_preferences_selected_by_user", "workspace_connector_preferences")
    _drop_constraint("fk_workspace_connector_preferences_membership", "workspace_connector_preferences")
    _drop_constraint("fk_workspace_connector_preferences_user", "workspace_connector_preferences")
    _drop_constraint("fk_tool_connections_user", "tool_connections")

    for table in reversed(list(WORKSPACE_FKS.keys())):
        _drop_constraint(f"fk_{table}_workspace", table)

    op.execute("DROP INDEX IF EXISTS ix_workspace_connector_preferences_selected_by_user_id")
    op.execute("DROP INDEX IF EXISTS ix_workspace_connector_preferences_membership_id")
    op.execute("DROP INDEX IF EXISTS ix_workspace_connector_preferences_user_id")
    op.execute(
        "ALTER TABLE workspace_connector_preferences "
        "DROP CONSTRAINT IF EXISTS ck_workspace_connector_preferences_scope_type"
    )
    op.execute("ALTER TABLE workspace_connector_preferences DROP COLUMN IF EXISTS selected_by_user_id")
    op.execute("ALTER TABLE workspace_connector_preferences DROP COLUMN IF EXISTS membership_id")
    op.execute("ALTER TABLE workspace_connector_preferences DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE workspace_connector_preferences DROP COLUMN IF EXISTS scope_type")

    op.execute("UPDATE tool_connections SET user_id = workspace_id WHERE user_id IS NULL OR user_id = ''")
    op.execute("ALTER TABLE tool_connections ALTER COLUMN user_id SET NOT NULL")
