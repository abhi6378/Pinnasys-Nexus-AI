"""
models/pending_tool_requests.py  —  Durable pending auth/approval requests.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from storage.db import Base, TZDateTime, new_id
from utils.time_utils import utc_now


class PendingToolRequestModel(Base):
    __tablename__ = "pending_tool_requests"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id = Column(String, ForeignKey("workspace_memberships.id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id = Column(String, default="")
    agent_key = Column(String, default="")
    original_input = Column(Text, nullable=False)
    requested_tool = Column(String, nullable=False)
    requested_toolkit = Column(String, default="")
    resume_token = Column(String, nullable=False, unique=True, index=True)
    status = Column(String, nullable=False, default="pending")
    pending_kind = Column(String, nullable=False, default="auth")
    idempotency_key = Column(String, default="")
    approval_requirement_json = Column(JSONB, default=dict)
    approved = Column(Boolean, default=False)
    approved_at = Column(TZDateTime, nullable=True)
    expires_at = Column(TZDateTime, nullable=True, index=True)
    context_json = Column(JSONB, default=dict)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("ix_pending_tool_requests_workspace_status_updated", "workspace_id", "status", "updated_at"),
        Index("ix_pending_tool_requests_workspace_kind_status_updated", "workspace_id", "pending_kind", "status", "updated_at"),
        CheckConstraint(
            "status IN ('pending', 'resumed', 'completed', 'expired', 'cancelled')",
            name="ck_pending_tool_requests_status",
        ),
        CheckConstraint(
            "pending_kind IN ('auth', 'approval')",
            name="ck_pending_tool_requests_pending_kind",
        ),
    )

    def __repr__(self):
        return (
            f"<PendingToolRequest ws={self.workspace_id} "
            f"tool={self.requested_tool} status={self.status} "
            f"token={self.resume_token[:8]}…>"
        )
