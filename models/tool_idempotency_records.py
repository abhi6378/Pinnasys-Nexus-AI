"""
models/tool_idempotency_records.py  —  Durable dedupe state for external writes.
"""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from storage.db import Base, TZDateTime, new_id
from utils.time_utils import utc_now


class ToolIdempotencyRecordModel(Base):
    __tablename__ = "tool_idempotency_records"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, index=True)
    input_hash = Column(String, default="")
    status = Column(String, nullable=False, default="pending")
    pending_request_id = Column(String, ForeignKey("pending_tool_requests.id", ondelete="SET NULL"), nullable=True)
    tool_call_log_id = Column(String, ForeignKey("tool_call_logs.id", ondelete="SET NULL"), nullable=True)
    output_json = Column(JSONB, default=dict)
    error_message = Column(Text, default="")
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)
    completed_at = Column(TZDateTime, nullable=True)

    __table_args__ = (
        Index("uq_tool_idempotency_workspace_tool_key", "workspace_id", "tool_name", "idempotency_key", unique=True),
        CheckConstraint(
            "status IN ('pending', 'pending_auth', 'pending_approval', 'in_progress', 'success', 'failure')",
            name="ck_tool_idempotency_records_status",
        ),
    )

    def __repr__(self):
        return (
            f"<ToolIdempotencyRecord ws={self.workspace_id} "
            f"tool={self.tool_name} status={self.status}>"
        )
