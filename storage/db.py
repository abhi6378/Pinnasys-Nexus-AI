"""
storage/db.py  —  SQLAlchemy ORM models + engine setup
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from utils.time_utils import utc_now

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("CRITICAL: DATABASE_URL not found in environment. PostgreSQL is required.")

if not DATABASE_URL.startswith("postgresql"):
    raise ValueError(f"CRITICAL: Invalid database protocol. Expected PostgreSQL, got: {DATABASE_URL.split(':', 1)[0]}")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

TZDateTime = DateTime(timezone=True)

IDEA_STATUSES = ("pending", "accepted", "rejected")
WORKFLOW_RUN_STATUSES = ("pending", "running", "paused", "completed", "failed", "cancelled")
WORKSPACE_MEMBERSHIP_ROLES = ("owner", "admin", "member", "viewer")
WORKSPACE_MEMBERSHIP_STATUSES = ("active", "invited", "suspended", "removed")
CONNECTOR_PREFERENCE_MODES = ("auto", "manual")
CONNECTOR_PREFERENCE_SCOPES = ("workspace", "user", "membership")
AUTH_SESSION_STATUSES = ("active", "revoked", "expired")
SCHEDULED_TASK_STATUSES = ("active", "paused", "cancelled", "archived")
SCHEDULED_RUN_STATUSES = ("queued", "running", "succeeded", "failed", "skipped", "cancelled", "approval_required")
SCHEDULE_TYPES = ("once", "interval", "cron")


def new_id():
    return str(uuid.uuid4())


def _status_check(column_name: str, values: tuple[str, ...], constraint_name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column_name} IN ({allowed})", name=constraint_name)


class WorkspaceModel(Base):
    __tablename__ = "workspaces"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(TZDateTime, default=utc_now)


class UserModel(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_id)
    email = Column(String, nullable=True, index=True)
    display_name = Column(String, default="")
    avatar_url = Column(String, default="")
    status = Column(String, nullable=False, default="active")
    metadata_json = Column(JSON, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        _status_check("status", ("active", "invited", "disabled"), "ck_users_status"),
        Index(
            "uq_users_email_nonempty",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL AND email <> ''"),
        ),
    )


class ExternalIdentityModel(Base):
    __tablename__ = "external_identities"

    id = Column(String, primary_key=True, default=new_id)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    provider_subject = Column(String, nullable=False)
    email = Column(String, default="")
    metadata_json = Column(JSON, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("uq_external_identities_provider_subject", "provider", "provider_subject", unique=True),
    )


class WorkspaceMembershipModel(Base):
    __tablename__ = "workspace_memberships"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False, default="member")
    status = Column(String, nullable=False, default="active")
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("uq_workspace_memberships_workspace_user", "workspace_id", "user_id", unique=True),
        _status_check("role", WORKSPACE_MEMBERSHIP_ROLES, "ck_workspace_memberships_role"),
        _status_check("status", WORKSPACE_MEMBERSHIP_STATUSES, "ck_workspace_memberships_status"),
    )


class AuthSessionModel(Base):
    __tablename__ = "auth_sessions"

    id = Column(String, primary_key=True, default=new_id)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_hash = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="active")
    metadata_json = Column(JSON, default=dict)
    expires_at = Column(TZDateTime, nullable=False, index=True)
    created_at = Column(TZDateTime, default=utc_now)
    last_seen_at = Column(TZDateTime, default=utc_now)
    revoked_at = Column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("uq_auth_sessions_session_hash", "session_hash", unique=True),
        Index("ix_auth_sessions_user_status_expires", "user_id", "status", "expires_at"),
        _status_check("status", AUTH_SESSION_STATUSES, "ck_auth_sessions_status"),
    )


class BrainProfileModel(Base):
    __tablename__ = "brain_profiles"

    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    company_name = Column(String, default="")
    brand_context = Column(Text, default="")
    tone = Column(String, default="")
    audience = Column(Text, default="")
    goals = Column(Text, default="")
    services = Column(Text, default="")
    pricing = Column(Text, default="")
    competitors = Column(Text, default="")
    support_style = Column(String, default="")
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)


class KnowledgeItemModel(Base):
    __tablename__ = "knowledge_items"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    type = Column(String, default="text")
    title = Column(String, default="")
    content = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    created_at = Column(TZDateTime, default=utc_now)


class QuizAnswerModel(Base):
    __tablename__ = "quiz_answers"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    category = Column(String, default="general")
    created_at = Column(TZDateTime, default=utc_now)


class ConversationModel(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    helper = Column(String, nullable=False)
    input = Column(Text, nullable=False)
    output = Column(Text, nullable=False)
    request_id = Column(String, default="", index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(TZDateTime, default=utc_now)

    __table_args__ = (
        Index("ix_conversations_workspace_created_at", "workspace_id", "created_at"),
    )


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_name = Column(String, nullable=False)
    steps = Column(JSON, default=list)
    final_output = Column(Text, default="")
    status = Column(String, nullable=False, default="completed")
    request_id = Column(String, default="", index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("ix_workflow_runs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_workflow_runs_workspace_status_updated", "workspace_id", "status", "updated_at"),
        _status_check("status", WORKFLOW_RUN_STATUSES, "ck_workflow_runs_status"),
    )


class IdeaModel(Base):
    __tablename__ = "ideas"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, default="")
    source_agent = Column(String, default="system")
    status = Column(String, default="pending")
    workflow_hint = Column(String, default="")
    created_at = Column(TZDateTime, default=utc_now)

    __table_args__ = (
        _status_check("status", IDEA_STATUSES, "ck_ideas_status"),
    )


class MemoryRecordModel(Base):
    __tablename__ = "memory_records"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    memory_type = Column(String, nullable=False, default="semantic_fact")
    title = Column(String, default="")
    content = Column(Text, default="")
    summary = Column(Text, default="")
    source_kind = Column(String, default="")
    source_reference_id = Column(String, default="")
    tags = Column(JSON, default=list)
    entity_tags = Column(JSON, default=list)
    tool_tags = Column(JSON, default=list)
    importance_score = Column(Float, default=0.5)
    confidence_score = Column(Float, default=0.5)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(TZDateTime, default=utc_now)
    pinned = Column(Boolean, default=False)
    canonical_key = Column(String, default="")
    superseded_by = Column(String, default="")
    metadata_json = Column(JSON, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("ix_memory_records_workspace_type_updated", "workspace_id", "memory_type", "updated_at"),
        Index(
            "uq_memory_records_active_canonical_key",
            "workspace_id",
            "canonical_key",
            unique=True,
            postgresql_where=text("canonical_key <> '' AND superseded_by = ''"),
        ),
    )


class WorkingMemoryStateModel(Base):
    __tablename__ = "working_memory_states"

    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    current_goal = Column(Text, default="")
    active_tasks = Column(JSON, default=list)
    open_questions = Column(JSON, default=list)
    current_draft_summary = Column(Text, default="")
    recent_tool_summary = Column(Text, default="")
    latest_workflow_summary = Column(Text, default="")
    project_focus = Column(Text, default="")
    state_json = Column(JSON, default=dict)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)


class MemoryEmbeddingModel(Base):
    __tablename__ = "memory_embeddings"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    memory_record_id = Column(String, ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name = Column(String, default="")
    content_hash = Column(String, default="")
    vector_json = Column(JSON, default=list)
    dimensions = Column(Integer, default=0)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("uq_memory_embeddings_memory_model", "memory_record_id", "model_name", unique=True),
        Index("ix_memory_embeddings_workspace_model_updated", "workspace_id", "model_name", "updated_at"),
    )


class WorkspaceConnectorPreferenceModel(Base):
    __tablename__ = "workspace_connector_preferences"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_type = Column(String, nullable=False, default="workspace")
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    selected_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    mode = Column(String, default="auto")
    selected_toolkit = Column(String, default="")
    selected_account_id = Column(String, default="")
    selected_account_alias = Column(String, default="")
    source = Column(String, default="persisted_default")
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index(
            "uq_workspace_connector_preferences_workspace_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("scope_type = 'workspace'"),
        ),
        Index(
            "uq_workspace_connector_preferences_user_scope",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where=text("scope_type = 'user' AND user_id IS NOT NULL"),
        ),
        Index(
            "uq_workspace_connector_preferences_membership_scope",
            "workspace_id",
            "membership_id",
            unique=True,
            postgresql_where=text("scope_type = 'membership' AND membership_id IS NOT NULL"),
        ),
        _status_check("mode", CONNECTOR_PREFERENCE_MODES, "ck_workspace_connector_preferences_mode"),
        _status_check("scope_type", CONNECTOR_PREFERENCE_SCOPES, "ck_workspace_connector_preferences_scope_type"),
    )


def register_model_modules() -> None:
    # Import models that live outside this module so they register on Base.metadata.
    from models.pending_tool_requests import PendingToolRequestModel  # noqa: F401
    from models.scheduled_tasks import ScheduledTaskModel, ScheduledTaskRunModel  # noqa: F401
    from models.tool_call_logs import ToolCallLogModel  # noqa: F401
    from models.tool_connections import ToolConnectionModel  # noqa: F401
    from models.tool_idempotency_records import ToolIdempotencyRecordModel  # noqa: F401


def _should_allow_schema_bootstrap(allow_bootstrap: bool | None = None) -> bool:
    if allow_bootstrap is not None:
        return bool(allow_bootstrap)
    return str(os.getenv("SINTRA_ALLOW_SCHEMA_BOOTSTRAP", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _database_has_any_tables() -> bool:
    inspector = inspect(engine)
    return bool(inspector.get_table_names())


def _load_alembic_config():
    config_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    if not config_path.exists():
        return None
    try:
        from alembic.config import Config
    except Exception:
        return None
    config = Config(str(config_path))
    script_location = Path(__file__).resolve().parent.parent / "alembic"
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def _get_alembic_head_revision() -> str:
    config = _load_alembic_config()
    if config is None:
        return ""
    try:
        from alembic.script import ScriptDirectory
    except Exception:
        return ""
    try:
        script = ScriptDirectory.from_config(config)
        return script.get_current_head() or ""
    except Exception:
        return ""


def _get_current_database_revision() -> str:
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return ""
    try:
        with engine.connect() as connection:
            return str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar() or "")
    except Exception:
        return ""


def init_db(allow_bootstrap: bool | None = None):
    register_model_modules()

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    has_tables = _database_has_any_tables()
    bootstrap_allowed = _should_allow_schema_bootstrap(allow_bootstrap)

    if not has_tables:
        if bootstrap_allowed:
            logger.warning(
                "Bootstrapping database schema with create_all() because SINTRA_ALLOW_SCHEMA_BOOTSTRAP is enabled."
            )
            Base.metadata.create_all(bind=engine)
            return
        raise RuntimeError(
            "Database schema is missing. Run 'alembic upgrade head' or set "
            "SINTRA_ALLOW_SCHEMA_BOOTSTRAP=1 for explicit local bootstrap."
        )

    head_revision = _get_alembic_head_revision()
    current_revision = _get_current_database_revision()
    if head_revision and current_revision and current_revision != head_revision:
        logger.warning(
            "Database revision is behind head. current=%s head=%s. Run 'alembic upgrade head'.",
            current_revision,
            head_revision,
        )
    elif head_revision and not current_revision:
        logger.warning(
            "Database schema is unversioned. Stamp the existing schema with "
            "'alembic stamp %s' before running upgrades.",
            head_revision,
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
