"""
models/tool_call_logs.py  —  Audit log for every tool execution attempt.

Every call through tools/tool_executor.py creates exactly one row here,
regardless of outcome. This gives full observability into:
  - which tools agents are calling
  - success / failure rates
  - auth prompts surfaced to users
  - input/output payloads for debugging

Statuses:
  success           — tool executed and returned valid output
  failure           — tool executed but returned an error
  connect_required  — tool was not executed because auth is missing
  validation_error  — tool_name or agent not allowed
  timeout           — tool execution timed out
"""
from sqlalchemy import Boolean, Column, String, Text, DateTime, JSON, Float

from storage.db import Base, new_id
from utils.time_utils import utc_now


class ToolCallLogModel(Base):
    __tablename__ = "tool_call_logs"

    id              = Column(String,   primary_key=True, default=new_id)
    workspace_id    = Column(String,   nullable=False, index=True)
    agent_key       = Column(String,   default="")           # which agent requested the tool
    tool_name       = Column(String,   nullable=False)       # tool slug
    toolkit         = Column(String,   default="")           # parent toolkit
    status          = Column(String,   nullable=False)       # success | failure | connect_required | …
    idempotency_key = Column(String,   default="")
    pending_kind    = Column(String,   default="")
    approval_required = Column(Boolean, default=False)
    input_json      = Column(JSON,     default=dict)         # arguments sent to the tool
    output_json     = Column(JSON,     default=dict)         # raw response from Composio
    error_message   = Column(Text,     default="")           # human-readable error, if any
    duration_ms     = Column(Float,    default=0.0)          # execution wall-clock time
    created_at      = Column(DateTime, default=utc_now)

    def __repr__(self):
        return (
            f"<ToolCallLog ws={self.workspace_id} "
            f"tool={self.tool_name} status={self.status}>"
        )
