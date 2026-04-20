"""google auth runtime and scoped ownership

Revision ID: 20260417_04
Revises: 20260417_03
Create Date: 2026-04-17 00:45:00
"""
from alembic import op


revision = "20260417_04"
down_revision = "20260417_03"
branch_labels = None
depends_on = None


def _add_fk_not_valid(name: str, table: str, column: str, target: str, on_delete: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {name}
                FOREIGN KEY ({column}) REFERENCES {target}
                ON DELETE {on_delete}
                NOT VALID;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_hash VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'active',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ,
            last_seen_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            CONSTRAINT ck_auth_sessions_status CHECK (status IN ('active', 'revoked', 'expired'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_sessions_session_hash ON auth_sessions (session_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_status_expires ON auth_sessions (user_id, status, expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at ON auth_sessions (expires_at)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_nonempty "
        "ON users (email) WHERE email IS NOT NULL AND email <> ''"
    )

    for table in ("conversations", "workflow_runs", "pending_tool_requests", "tool_call_logs", "tool_idempotency_records"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS actor_user_id VARCHAR")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_actor_user_id ON {table} (actor_user_id)")
        _add_fk_not_valid(f"fk_{table}_actor_user", table, "actor_user_id", "users(id)", "SET NULL")

    op.execute("ALTER TABLE workspace_connector_preferences ADD COLUMN IF NOT EXISTS id VARCHAR")
    op.execute(
        """
        UPDATE workspace_connector_preferences
        SET id = 'pref_' || md5(random()::text || clock_timestamp()::text || workspace_id)
        WHERE id IS NULL OR id = ''
        """
    )
    op.execute("ALTER TABLE workspace_connector_preferences ALTER COLUMN id SET NOT NULL")
    op.execute("ALTER TABLE workspace_connector_preferences DROP CONSTRAINT IF EXISTS workspace_connector_preferences_pkey")
    op.execute("ALTER TABLE workspace_connector_preferences ADD CONSTRAINT workspace_connector_preferences_pkey PRIMARY KEY (id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_connector_preferences_workspace_default "
        "ON workspace_connector_preferences (workspace_id) WHERE scope_type = 'workspace'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_connector_preferences_user_scope "
        "ON workspace_connector_preferences (workspace_id, user_id) "
        "WHERE scope_type = 'user' AND user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_connector_preferences_membership_scope "
        "ON workspace_connector_preferences (workspace_id, membership_id) "
        "WHERE scope_type = 'membership' AND membership_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_workspace_connector_preferences_membership_scope")
    op.execute("DROP INDEX IF EXISTS uq_workspace_connector_preferences_user_scope")
    op.execute("DROP INDEX IF EXISTS uq_workspace_connector_preferences_workspace_default")
    op.execute(
        "DELETE FROM workspace_connector_preferences "
        "WHERE scope_type IN ('user', 'membership')"
    )
    op.execute("ALTER TABLE workspace_connector_preferences DROP CONSTRAINT IF EXISTS workspace_connector_preferences_pkey")
    op.execute("ALTER TABLE workspace_connector_preferences ADD CONSTRAINT workspace_connector_preferences_pkey PRIMARY KEY (workspace_id)")
    op.execute("ALTER TABLE workspace_connector_preferences DROP COLUMN IF EXISTS id")

    for table in reversed(("conversations", "workflow_runs", "pending_tool_requests", "tool_call_logs", "tool_idempotency_records")):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_actor_user")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_actor_user_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS actor_user_id")

    op.execute("DROP INDEX IF EXISTS uq_users_email_nonempty")
    op.execute("DROP TABLE IF EXISTS auth_sessions")
