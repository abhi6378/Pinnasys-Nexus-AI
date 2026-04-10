"""
workspace/manager.py  —  Workspace lifecycle management
"""
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from storage import repositories as repo
from storage.db import WorkspaceModel, BrainProfileModel


@dataclass
class WorkspaceContext:
    id: str
    name: str
    brain: dict = field(default_factory=dict)
    knowledge_count: int = 0
    conversation_count: int = 0
    idea_count: int = 0


def create_workspace(name: str, db: Session) -> WorkspaceModel:
    return repo.create_workspace(db, name)


def get_workspace_context(workspace_id: str, db: Session) -> WorkspaceContext | None:
    ws = repo.get_workspace(db, workspace_id)
    if not ws:
        return None

    brain = repo.get_brain(db, workspace_id)
    brain_dict = {}
    if brain:
        brain_dict = {
            "company_name":  brain.company_name,
            "brand_context": brain.brand_context,
            "tone":          brain.tone,
            "audience":      brain.audience,
            "goals":         brain.goals,
            "services":      brain.services,
        }

    knowledge = repo.list_all_knowledge(db, workspace_id)
    conversations = repo.get_conversations(db, workspace_id)
    ideas = repo.get_ideas(db, workspace_id, status="pending")

    return WorkspaceContext(
        id=ws.id,
        name=ws.name,
        brain=brain_dict,
        knowledge_count=len(knowledge),
        conversation_count=len(conversations),
        idea_count=len(ideas),
    )


def list_workspaces(db: Session) -> list:
    workspaces = repo.list_workspaces(db)
    return [{"id": ws.id, "name": ws.name, "created_at": str(ws.created_at)}
            for ws in workspaces]
