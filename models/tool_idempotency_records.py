"""
models/tool_idempotency_records.py  —  Durable dedupe state for external writes.

The executor uses this table to prevent duplicate high-risk external writes
from being executed more than once across retries, reconnect flows, and
accidental duplicate submissions.
"""
from sqlalchemy import Column, String, Text, DateTime, JSON

from storage.db import Base, new_id
from utils.time_utils import utc_now


class ToolIdempotencyRecordModel(Base):
    __tablename__ = "tool_idempotency_records"

    id               = Column(String, primary_key=True, default=new_id)
    workspace_id     = Column(String, nullable=False, index=True)
    tool_name        = Column(String, nullable=False, index=True)
    idempotency_key  = Column(String, nullable=False, index=True)
    input_hash       = Column(String, default="")
    status           = Column(String, nullable=False, default="pending")  # pending_auth | pending_approval | in_progress | success | failure
    pending_request_id = Column(String, default="")
    tool_call_log_id = Column(String, default="")
    output_json      = Column(JSON, default=dict)
    error_message    = Column(Text, default="")
    created_at       = Column(DateTime, default=utc_now)
    updated_at       = Column(DateTime, default=utc_now, onupdate=utc_now)
    completed_at     = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<ToolIdempotencyRecord ws={self.workspace_id} "
            f"tool={self.tool_name} status={self.status}>"
        )
