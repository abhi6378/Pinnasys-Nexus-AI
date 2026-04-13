"""
models/tool_connections.py  —  Tracks which Composio-managed tools a workspace
has connected (OAuth completed, API key stored, etc.).

This is the LOCAL record of what Composio already knows server-side, kept so
that the tool executor can short-circuit "is this connected?" checks without
an API round-trip every time.

Identity mapping:
  workspace_id  ≡  Composio user_id  (1:1 for now)
  user_id       =  workspace_id      (reserved for future multi-user workspaces)

Columns:
  id                   — PK, UUID
  workspace_id         — FK-like reference to workspaces.id (not enforced by FK
                         constraint to stay additive / migration-safe)
  user_id              — Composio user_id; equals workspace_id today
  tool_name            — Composio tool slug, e.g. "GMAIL_SEND_EMAIL"
  toolkit              — Composio toolkit / app name, e.g. "GMAIL"
  status               — connected | pending | revoked | error
  connected_account_id — Composio's opaque account ID once OAuth completes
  auth_mode            — oauth2 | api_key | jwt | none
  metadata_json        — Arbitrary JSON blob for extra data (scopes, labels, …)
  created_at           — Row creation timestamp
  updated_at           — Last status/metadata change
"""
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, JSON

from storage.db import Base, new_id


class ToolConnectionModel(Base):
    __tablename__ = "tool_connections"

    id                   = Column(String, primary_key=True, default=new_id)
    workspace_id         = Column(String, nullable=False, index=True)
    user_id              = Column(String, nullable=False, index=True)
    tool_name            = Column(String, nullable=False)           # e.g. GMAIL_SEND_EMAIL
    toolkit              = Column(String, nullable=False, index=True)  # e.g. GMAIL
    status               = Column(String, nullable=False, default="pending")
    connected_account_id = Column(String, default="")
    auth_mode            = Column(String, default="oauth2")         # oauth2 | api_key | jwt | none
    metadata_json        = Column(JSON,   default=dict)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return (
            f"<ToolConnection ws={self.workspace_id} "
            f"toolkit={self.toolkit} tool={self.tool_name} "
            f"status={self.status}>"
        )
