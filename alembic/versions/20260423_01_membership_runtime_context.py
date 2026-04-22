"""membership-aware runtime context

Revision ID: 20260423_01
Revises: 20260422_01
Create Date: 2026-04-23 10:00:00
"""
from alembic import op


revision = "20260423_01"
down_revision = "20260422_01"
branch_labels = None
depends_on = None


TABLES = (
    "conversations",
    "workflow_runs",
    "pending_tool_requests",
    "tool_call_logs",
    "tool_idempotency_records",
)


def _add_membership_fk(table: str) -> None:
    constraint_name = f"fk_{table}_membership"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}') THEN
                ALTER TABLE {table}
                ADD CONSTRAINT {constraint_name}
                FOREIGN KEY (membership_id) REFERENCES workspace_memberships(id)
                ON DELETE SET NULL
                NOT VALID;
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS membership_id VARCHAR")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_membership_id ON {table} (membership_id)")
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_workspace_membership ON {table} (workspace_id, membership_id)")
        _add_membership_fk(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS fk_{table}_membership")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_workspace_membership")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_membership_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS membership_id")
