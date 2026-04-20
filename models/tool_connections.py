"""
models/tool_connections.py  —  Tracks Composio-managed connector/account state.

The app DB stores only safe connector metadata and runtime state. OAuth tokens,
API keys, and other secret material must remain outside this table.
"""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, JSON, String, text

from storage.db import Base, TZDateTime, new_id
from utils.time_utils import utc_now

TOOL_CONNECTION_STATUSES = ("connected", "pending", "revoked", "error", "not_found")
TOOL_CONNECTION_AUTH_MODES = ("oauth2", "api_key", "jwt", "none")


class ToolConnectionModel(Base):
    __tablename__ = "tool_connections"

    id = Column(String, primary_key=True, default=new_id)
    workspace_id = Column(String, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tool_name = Column(String, nullable=False)
    toolkit = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")
    connected_account_id = Column(String, default="")
    account_label = Column(String, default="")
    is_default = Column(Boolean, default=False)
    auth_mode = Column(String, default="oauth2")
    metadata_json = Column(JSON, default=dict)
    last_verified_at = Column(TZDateTime, nullable=True)
    last_seen_remote_at = Column(TZDateTime, nullable=True)
    revoked_at = Column(TZDateTime, nullable=True)
    status_reason = Column(String, default="")
    status_updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)
    created_at = Column(TZDateTime, default=utc_now)
    updated_at = Column(TZDateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index(
            "uq_tool_connections_workspace_toolkit_account",
            "workspace_id",
            "toolkit",
            "connected_account_id",
            unique=True,
        ),
        Index(
            "uq_tool_connections_single_default_active",
            "workspace_id",
            "toolkit",
            unique=True,
            postgresql_where=text("is_default = TRUE AND status = 'connected' AND revoked_at IS NULL"),
        ),
        Index(
            "ix_tool_connections_workspace_toolkit_status_default_updated",
            "workspace_id",
            "toolkit",
            "status",
            "is_default",
            "updated_at",
        ),
        Index(
            "ix_tool_connections_workspace_toolkit_account",
            "workspace_id",
            "toolkit",
            "connected_account_id",
        ),
        CheckConstraint(
            "status IN ('connected', 'pending', 'revoked', 'error', 'not_found')",
            name="ck_tool_connections_status",
        ),
        CheckConstraint(
            "auth_mode IN ('oauth2', 'api_key', 'jwt', 'none')",
            name="ck_tool_connections_auth_mode",
        ),
    )

    def __repr__(self):
        return (
            f"<ToolConnection ws={self.workspace_id} "
            f"toolkit={self.toolkit} tool={self.tool_name} "
            f"status={self.status}>"
        )
