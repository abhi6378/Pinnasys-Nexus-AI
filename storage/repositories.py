"""
storage/repositories.py  —  CRUD helpers for every table
"""
import uuid
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from storage.db import (
    WorkspaceModel, BrainProfileModel, KnowledgeItemModel,
    QuizAnswerModel, ConversationModel, WorkflowRunModel, IdeaModel
)
from utils.logging_utils import log_event, log_exception


logger = logging.getLogger(__name__)


def _id():
    return str(uuid.uuid4())


# ── Workspace ─────────────────────────────────────────────────────────────────

def create_workspace(db: Session, name: str) -> WorkspaceModel:
    ws = WorkspaceModel(id=_id(), name=name, created_at=datetime.utcnow())
    db.add(ws)
    # seed empty brain profile
    bp = BrainProfileModel(workspace_id=ws.id)
    db.add(bp)
    db.commit()
    db.refresh(ws)
    return ws


def get_workspace(db: Session, workspace_id: str) -> Optional[WorkspaceModel]:
    return db.query(WorkspaceModel).filter(WorkspaceModel.id == workspace_id).first()


def list_workspaces(db: Session):
    return db.query(WorkspaceModel).order_by(WorkspaceModel.created_at).all()


# ── Brain Profile ─────────────────────────────────────────────────────────────

def get_brain(db: Session, workspace_id: str) -> Optional[BrainProfileModel]:
    return db.query(BrainProfileModel).filter(
        BrainProfileModel.workspace_id == workspace_id
    ).first()


def update_brain(db: Session, workspace_id: str, updates: dict) -> BrainProfileModel:
    try:
        brain = get_brain(db, workspace_id)
        if not brain:
            brain = BrainProfileModel(workspace_id=workspace_id)
            db.add(brain)
        for k, v in updates.items():
            if hasattr(brain, k) and v:
                setattr(brain, k, v)
        brain.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(brain)
        log_event(
            logger,
            logging.INFO,
            "storage.brain.updated",
            workspace_id=workspace_id,
            field_count=len([k for k, v in updates.items() if v]),
        )
        return brain
    except Exception as exc:
        log_exception(
            logger,
            "storage.brain.update_failed",
            exc,
            workspace_id=workspace_id,
        )
        raise


# ── Knowledge ─────────────────────────────────────────────────────────────────

def add_knowledge(db: Session, workspace_id: str, type_: str,
                  title: str, content: str, tags: list = None) -> KnowledgeItemModel:
    try:
        item = KnowledgeItemModel(
            id=_id(), workspace_id=workspace_id,
            type=type_, title=title, content=content,
            tags=tags or [], created_at=datetime.utcnow()
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        log_event(
            logger,
            logging.INFO,
            "storage.knowledge.added",
            workspace_id=workspace_id,
            knowledge_type=type_,
        )
        return item
    except Exception as exc:
        log_exception(
            logger,
            "storage.knowledge_add_failed",
            exc,
            workspace_id=workspace_id,
            knowledge_type=type_,
        )
        raise


def get_knowledge(db: Session, workspace_id: str, query: str = "", limit: int = 10):
    try:
        items = db.query(KnowledgeItemModel).filter(
            KnowledgeItemModel.workspace_id == workspace_id
        ).order_by(KnowledgeItemModel.created_at.desc()).all()

        if not query:
            result = items[:limit]
            log_event(
                logger,
                logging.INFO,
                "storage.knowledge.fetched",
                workspace_id=workspace_id,
                query_present=False,
                result_count=len(result),
            )
            return result

        q = query.lower()
        scored = []
        for item in items:
            score = 0
            if q in item.content.lower():
                score += 2
            if q in item.title.lower():
                score += 1
            if any(q in tag.lower() for tag in (item.tags or [])):
                score += 1
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda x: -x[0])
        result = [i for _, i in scored[:limit]]
        log_event(
            logger,
            logging.INFO,
            "storage.knowledge.fetched",
            workspace_id=workspace_id,
            query_present=True,
            result_count=len(result),
        )
        return result
    except Exception as exc:
        log_exception(
            logger,
            "storage.knowledge_fetch_failed",
            exc,
            workspace_id=workspace_id,
        )
        raise


def list_all_knowledge(db: Session, workspace_id: str):
    return db.query(KnowledgeItemModel).filter(
        KnowledgeItemModel.workspace_id == workspace_id
    ).order_by(KnowledgeItemModel.created_at.desc()).all()


def delete_knowledge(db: Session, item_id: str):
    item = db.query(KnowledgeItemModel).filter(KnowledgeItemModel.id == item_id).first()
    if item:
        db.delete(item)
        db.commit()


# ── Quiz Answers ──────────────────────────────────────────────────────────────

def save_quiz_answer(db: Session, workspace_id: str,
                     question: str, answer: str, category: str) -> QuizAnswerModel:
    qa = QuizAnswerModel(
        id=_id(), workspace_id=workspace_id,
        question=question, answer=answer, category=category,
        created_at=datetime.utcnow()
    )
    db.add(qa)
    db.commit()
    return qa


def get_quiz_answers(db: Session, workspace_id: str):
    return db.query(QuizAnswerModel).filter(
        QuizAnswerModel.workspace_id == workspace_id
    ).all()


# ── Conversations ─────────────────────────────────────────────────────────────

def save_conversation(db: Session, workspace_id: str,
                      helper: str, input_: str, output: str) -> ConversationModel:
    try:
        conv = ConversationModel(
            id=_id(), workspace_id=workspace_id,
            helper=helper, input=input_, output=output,
            created_at=datetime.utcnow()
        )
        db.add(conv)
        db.commit()
        log_event(
            logger,
            logging.INFO,
            "storage.conversation.saved",
            workspace_id=workspace_id,
            agent_name=helper,
        )
        return conv
    except Exception as exc:
        log_exception(
            logger,
            "storage.conversation_save_failed",
            exc,
            workspace_id=workspace_id,
            agent_name=helper,
        )
        raise


def get_conversations(db: Session, workspace_id: str, limit: int = 20):
    try:
        result = db.query(ConversationModel).filter(
            ConversationModel.workspace_id == workspace_id
        ).order_by(ConversationModel.created_at.desc()).limit(limit).all()
        log_event(
            logger,
            logging.INFO,
            "storage.conversation.fetched",
            workspace_id=workspace_id,
            result_count=len(result),
            limit=limit,
        )
        return result
    except Exception as exc:
        log_exception(
            logger,
            "storage.conversation_fetch_failed",
            exc,
            workspace_id=workspace_id,
            limit=limit,
        )
        raise


# ── Workflow Runs ─────────────────────────────────────────────────────────────

def save_workflow_run(db: Session, workspace_id: str, workflow_name: str,
                      steps: list, final_output: str) -> WorkflowRunModel:
    try:
        run = WorkflowRunModel(
            id=_id(), workspace_id=workspace_id,
            workflow_name=workflow_name, steps=steps,
            final_output=final_output, created_at=datetime.utcnow()
        )
        db.add(run)
        db.commit()
        log_event(
            logger,
            logging.INFO,
            "storage.workflow.saved",
            workspace_id=workspace_id,
            workflow_name=workflow_name,
            step_count=len(steps or []),
        )
        return run
    except Exception as exc:
        log_exception(
            logger,
            "storage.workflow_save_failed",
            exc,
            workspace_id=workspace_id,
            workflow_name=workflow_name,
        )
        raise


def get_workflow_runs(db: Session, workspace_id: str, limit: int = 10):
    return db.query(WorkflowRunModel).filter(
        WorkflowRunModel.workspace_id == workspace_id
    ).order_by(WorkflowRunModel.created_at.desc()).limit(limit).all()


# ── Ideas Inbox ───────────────────────────────────────────────────────────────

def push_idea(db: Session, workspace_id: str, title: str,
              description: str, source_agent: str,
              workflow_hint: str = "") -> IdeaModel:
    idea = IdeaModel(
        id=_id(), workspace_id=workspace_id,
        title=title, description=description,
        source_agent=source_agent, status="pending",
        workflow_hint=workflow_hint, created_at=datetime.utcnow()
    )
    db.add(idea)
    db.commit()
    return idea


def get_ideas(db: Session, workspace_id: str, status: str = None):
    q = db.query(IdeaModel).filter(IdeaModel.workspace_id == workspace_id)
    if status:
        q = q.filter(IdeaModel.status == status)
    return q.order_by(IdeaModel.created_at.desc()).all()


def update_idea_status(db: Session, idea_id: str, status: str):
    idea = db.query(IdeaModel).filter(IdeaModel.id == idea_id).first()
    if idea:
        idea.status = status
        db.commit()
    return idea


def list_pending_tool_requests(db: Session, workspace_id: str, limit: int = 5):
    """
    Return the newest pending or resumed tool requests for a workspace.

    This powers UI refresh/resume without requiring a schema change in the
    conversation table.
    """
    from models.pending_tool_requests import PendingToolRequestModel

    return (
        db.query(PendingToolRequestModel)
        .filter(
            PendingToolRequestModel.workspace_id == workspace_id,
            PendingToolRequestModel.status.in_(["pending", "resumed"]),
        )
        .order_by(PendingToolRequestModel.updated_at.desc())
        .limit(limit)
        .all()
    )
