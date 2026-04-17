"""
models/pending_tool_requests.py  —  Saves the original user request when a tool
call cannot proceed because authentication is missing.

Flow:
  1. User says "send an email to X"
  2. Executor detects email tool needs OAuth
  3. Executor creates a PendingToolRequest row with a resume_token
  4. Chat returns a Connect Link to the user
  5. User completes OAuth → callback hits the app
  6. App looks up the resume_token, re-runs the request, marks row "completed"

Statuses:
  pending    — waiting for user to complete auth
  resumed    — auth completed, request was re-submitted
  completed  — tool executed successfully after resume
  expired    — timed out / user never completed auth
  cancelled  — user explicitly cancelled
"""
from sqlalchemy import Boolean, Column, String, Text, DateTime, JSON

from storage.db import Base, new_id
from utils.time_utils import utc_now


class PendingToolRequestModel(Base):
    __tablename__ = "pending_tool_requests"

    id               = Column(String,   primary_key=True, default=new_id)
    workspace_id     = Column(String,   nullable=False, index=True)
    conversation_id  = Column(String,   default="")          # links back to conversations.id
    agent_key        = Column(String,   default="")          # which agent was handling
    original_input   = Column(Text,     nullable=False)      # the user message that triggered this
    requested_tool   = Column(String,   nullable=False)      # tool slug that needs auth
    requested_toolkit = Column(String,  default="")          # parent toolkit, e.g. "GMAIL"
    resume_token     = Column(String,   nullable=False, unique=True, index=True)
    status           = Column(String,   nullable=False, default="pending")
    pending_kind     = Column(String,   nullable=False, default="auth")  # auth | approval
    idempotency_key  = Column(String,   default="")
    approval_requirement_json = Column(JSON, default=dict)
    approved         = Column(Boolean,  default=False)
    approved_at      = Column(DateTime, nullable=True)
    context_json     = Column(JSON,     default=dict)        # brain_context snapshot, etc.
    created_at       = Column(DateTime, default=utc_now)
    updated_at       = Column(DateTime, default=utc_now, onupdate=utc_now)

    def __repr__(self):
        return (
            f"<PendingToolRequest ws={self.workspace_id} "
            f"tool={self.requested_tool} status={self.status} "
            f"token={self.resume_token[:8]}…>"
        )
