"""
storage/db.py  —  SQLAlchemy ORM models + engine setup
"""
import os
import uuid

from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, JSON, Float, Integer, Boolean, text
)
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
from utils.time_utils import utc_now

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("CRITICAL: DATABASE_URL not found in environment. PostgreSQL is required.")

if not DATABASE_URL.startswith("postgresql"):
    raise ValueError(f"CRITICAL: Invalid database protocol. Expected PostgreSQL, got: {DATABASE_URL.split(':', 1)[0]}")

# PostgreSQL doesn't use check_same_thread
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def new_id():
    return str(uuid.uuid4())


# ── Tables ───────────────────────────────────────────────────────────────────

class WorkspaceModel(Base):
    __tablename__ = "workspaces"
    id         = Column(String, primary_key=True, default=new_id)
    name       = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)


class BrainProfileModel(Base):
    __tablename__ = "brain_profiles"
    workspace_id  = Column(String, primary_key=True)
    company_name  = Column(String,  default="")
    brand_context = Column(Text,    default="")
    tone          = Column(String,  default="")
    audience      = Column(Text,    default="")
    goals         = Column(Text,    default="")
    services      = Column(Text,    default="")
    pricing       = Column(Text,    default="")
    competitors   = Column(Text,    default="")
    support_style = Column(String,  default="")
    updated_at    = Column(DateTime, default=utc_now, onupdate=utc_now)


class KnowledgeItemModel(Base):
    __tablename__ = "knowledge_items"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    type         = Column(String, default="text")   # text | faq | link | file
    title        = Column(String, default="")
    content      = Column(Text,   nullable=False)
    tags         = Column(JSON,   default=list)
    created_at   = Column(DateTime, default=utc_now)


class QuizAnswerModel(Base):
    __tablename__ = "quiz_answers"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    question     = Column(Text,   nullable=False)
    answer       = Column(Text,   nullable=False)
    category     = Column(String, default="general")
    created_at   = Column(DateTime, default=utc_now)


class ConversationModel(Base):
    __tablename__ = "conversations"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    helper       = Column(String, nullable=False)
    input        = Column(Text,   nullable=False)
    output       = Column(Text,   nullable=False)
    created_at   = Column(DateTime, default=utc_now)


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    id            = Column(String, primary_key=True, default=new_id)
    workspace_id  = Column(String, nullable=False)
    workflow_name = Column(String, nullable=False)
    steps         = Column(JSON,   default=list)
    final_output  = Column(Text,   default="")
    created_at    = Column(DateTime, default=utc_now)


class IdeaModel(Base):
    __tablename__ = "ideas"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    title        = Column(String, nullable=False)
    description  = Column(Text,   default="")
    source_agent = Column(String, default="system")
    status       = Column(String, default="pending")   # pending | accepted | rejected
    workflow_hint = Column(String, default="")
    created_at   = Column(DateTime, default=utc_now)


class MemoryRecordModel(Base):
    __tablename__ = "memory_records"
    id                  = Column(String, primary_key=True, default=new_id)
    workspace_id        = Column(String, nullable=False)
    memory_type         = Column(String, nullable=False, default="semantic_fact")
    title               = Column(String, default="")
    content             = Column(Text, default="")
    summary             = Column(Text, default="")
    source_kind         = Column(String, default="")
    source_reference_id = Column(String, default="")
    tags                = Column(JSON, default=list)
    entity_tags         = Column(JSON, default=list)
    tool_tags           = Column(JSON, default=list)
    importance_score    = Column(Float, default=0.5)
    confidence_score    = Column(Float, default=0.5)
    access_count        = Column(Integer, default=0)
    last_accessed_at    = Column(DateTime, default=utc_now)
    pinned              = Column(Boolean, default=False)
    canonical_key       = Column(String, default="")
    superseded_by       = Column(String, default="")
    metadata_json       = Column(JSON, default=dict)
    created_at          = Column(DateTime, default=utc_now)
    updated_at          = Column(DateTime, default=utc_now, onupdate=utc_now)


class WorkingMemoryStateModel(Base):
    __tablename__ = "working_memory_states"
    workspace_id            = Column(String, primary_key=True)
    current_goal            = Column(Text, default="")
    active_tasks            = Column(JSON, default=list)
    open_questions          = Column(JSON, default=list)
    current_draft_summary   = Column(Text, default="")
    recent_tool_summary     = Column(Text, default="")
    latest_workflow_summary = Column(Text, default="")
    project_focus           = Column(Text, default="")
    state_json              = Column(JSON, default=dict)
    updated_at              = Column(DateTime, default=utc_now, onupdate=utc_now)


class MemoryEmbeddingModel(Base):
    __tablename__ = "memory_embeddings"
    id               = Column(String, primary_key=True, default=new_id)
    workspace_id     = Column(String, nullable=False)
    memory_record_id = Column(String, nullable=False)
    model_name       = Column(String, default="")
    content_hash     = Column(String, default="")
    vector_json      = Column(JSON, default=list)
    dimensions       = Column(Integer, default=0)
    created_at       = Column(DateTime, default=utc_now)
    updated_at       = Column(DateTime, default=utc_now, onupdate=utc_now)


class WorkspaceConnectorPreferenceModel(Base):
    __tablename__ = "workspace_connector_preferences"
    workspace_id           = Column(String, primary_key=True)
    mode                   = Column(String, default="auto")
    selected_toolkit       = Column(String, default="")
    selected_account_id    = Column(String, default="")
    selected_account_alias = Column(String, default="")
    source                 = Column(String, default="persisted_default")
    created_at             = Column(DateTime, default=utc_now)
    updated_at             = Column(DateTime, default=utc_now, onupdate=utc_now)


def _ensure_additive_connector_columns() -> None:
    statements = [
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS account_label VARCHAR DEFAULT ''",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMP NULL",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS last_seen_remote_at TIMESTAMP NULL",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP NULL",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS status_reason TEXT DEFAULT ''",
        "ALTER TABLE tool_connections ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE pending_tool_requests ADD COLUMN IF NOT EXISTS pending_kind VARCHAR DEFAULT 'auth'",
        "ALTER TABLE pending_tool_requests ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR DEFAULT ''",
        "ALTER TABLE pending_tool_requests ADD COLUMN IF NOT EXISTS approval_requirement_json JSONB DEFAULT '{}'::jsonb",
        "ALTER TABLE pending_tool_requests ADD COLUMN IF NOT EXISTS approved BOOLEAN DEFAULT FALSE",
        "ALTER TABLE pending_tool_requests ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP NULL",
        "ALTER TABLE tool_call_logs ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR DEFAULT ''",
        "ALTER TABLE tool_call_logs ADD COLUMN IF NOT EXISTS pending_kind VARCHAR DEFAULT ''",
        "ALTER TABLE tool_call_logs ADD COLUMN IF NOT EXISTS approval_required BOOLEAN DEFAULT FALSE",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))



def init_db():
    # Import tool-layer models inside init_db() so they register on
    # Base.metadata before create_all(), but AFTER all modules have
    # finished loading. This avoids the circular import:
    #   tool_executor → models → storage/db → models (boom)
    from models.tool_connections import ToolConnectionModel          # noqa: F401
    from models.pending_tool_requests import PendingToolRequestModel  # noqa: F401
    from models.tool_call_logs import ToolCallLogModel                # noqa: F401
    from models.tool_idempotency_records import ToolIdempotencyRecordModel  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_additive_connector_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
