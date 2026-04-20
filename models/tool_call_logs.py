"""
models/tool_call_logs.py  —  Audit log for every tool execution attempt.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from storage.db import Base, TZDateTime, new_id
from utils.time_utils import utc_now


class ToolCallLogModel(Base):
    __tablename__ = "tool_call_logs"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_key = Column(String, default="")
    tool_name = Column(String, nullable=False)
    toolkit = Column(String, default="")
    status = Column(String, nullable=False)
    idempotency_key = Column(String, default="")
    pending_kind = Column(String, default="")
    approval_required = Column(Boolean, default=False)
    input_json = Column(JSONB, default=dict)
    output_json = Column(JSONB, default=dict)
    error_message = Column(Text, default="")
    duration_ms = Column(Float, default=0.0)
    created_at = Column(TZDateTime, default=utc_now)

    __table_args__ = (
        Index("ix_tool_call_logs_workspace_status_created", "workspace_id", "status", "created_at"),
        Index("ix_tool_call_logs_workspace_tool_created", "workspace_id", "tool_name", "created_at"),
        CheckConstraint(
            "status IN ('success', 'failure', 'connect_required', 'auth_unavailable', "
            "'validation_error', 'timeout', 'invalid_tool')",
            name="ck_tool_call_logs_status",
        ),
        CheckConstraint(
            "pending_kind IN ('', 'auth', 'approval')",
            name="ck_tool_call_logs_pending_kind",
        ),
    )

    def __repr__(self):
        return (
            f"<ToolCallLog ws={self.workspace_id} "
            f"tool={self.tool_name} status={self.status}>"
        )
