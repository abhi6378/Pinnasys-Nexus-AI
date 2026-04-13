"""
storage/db.py  —  SQLAlchemy ORM models + engine setup
"""
import os
import uuid
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, String, Text, DateTime, JSON
)
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

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
    created_at = Column(DateTime, default=datetime.utcnow)


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
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeItemModel(Base):
    __tablename__ = "knowledge_items"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    type         = Column(String, default="text")   # text | faq | link | file
    title        = Column(String, default="")
    content      = Column(Text,   nullable=False)
    tags         = Column(JSON,   default=list)
    created_at   = Column(DateTime, default=datetime.utcnow)


class QuizAnswerModel(Base):
    __tablename__ = "quiz_answers"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    question     = Column(Text,   nullable=False)
    answer       = Column(Text,   nullable=False)
    category     = Column(String, default="general")
    created_at   = Column(DateTime, default=datetime.utcnow)


class ConversationModel(Base):
    __tablename__ = "conversations"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    helper       = Column(String, nullable=False)
    input        = Column(Text,   nullable=False)
    output       = Column(Text,   nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    id            = Column(String, primary_key=True, default=new_id)
    workspace_id  = Column(String, nullable=False)
    workflow_name = Column(String, nullable=False)
    steps         = Column(JSON,   default=list)
    final_output  = Column(Text,   default="")
    created_at    = Column(DateTime, default=datetime.utcnow)


class IdeaModel(Base):
    __tablename__ = "ideas"
    id           = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False)
    title        = Column(String, nullable=False)
    description  = Column(Text,   default="")
    source_agent = Column(String, default="system")
    status       = Column(String, default="pending")   # pending | accepted | rejected
    workflow_hint = Column(String, default="")
    created_at   = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
