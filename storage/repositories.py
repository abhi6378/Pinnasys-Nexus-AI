"""
storage/repositories.py  —  CRUD helpers for every table
"""
import uuid
import logging
from typing import Optional

from sqlalchemy.orm import Session

from storage.db import (
    WorkspaceModel, BrainProfileModel, KnowledgeItemModel,
    QuizAnswerModel, ConversationModel, WorkflowRunModel, IdeaModel,
    MemoryRecordModel, WorkingMemoryStateModel, MemoryEmbeddingModel,
    WorkspaceConnectorPreferenceModel,
)
from utils.logging_utils import log_event, log_exception
from utils.time_utils import utc_now


logger = logging.getLogger(__name__)


def _id():
    return str(uuid.uuid4())


def _clean_text(value: str | None, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _merge_unique_strings(existing: list | None, incoming: list | None, *, limit: int = 12) -> list[str]:
    merged: list[str] = []
    for item in [*(existing or []), *(incoming or [])]:
        text = str(item or "").strip()
        if not text or text in merged:
            continue
        merged.append(text)
        if len(merged) >= limit:
            break
    return merged


# ── Workspace ─────────────────────────────────────────────────────────────────

def create_workspace(db: Session, name: str) -> WorkspaceModel:
    ws = WorkspaceModel(id=_id(), name=name, created_at=utc_now())
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
        brain.updated_at = utc_now()
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
            tags=tags or [], created_at=utc_now()
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


# ── Working Memory ────────────────────────────────────────────────────────────

def get_working_memory(db: Session, workspace_id: str) -> Optional[WorkingMemoryStateModel]:
    return db.query(WorkingMemoryStateModel).filter(
        WorkingMemoryStateModel.workspace_id == workspace_id
    ).first()


def upsert_working_memory(
    db: Session,
    workspace_id: str,
    *,
    current_goal: str | None = None,
    active_tasks: list[str] | None = None,
    open_questions: list[str] | None = None,
    current_draft_summary: str | None = None,
    recent_tool_summary: str | None = None,
    latest_workflow_summary: str | None = None,
    project_focus: str | None = None,
    state_json: dict | None = None,
) -> WorkingMemoryStateModel:
    try:
        state = get_working_memory(db, workspace_id)
        if not state:
            state = WorkingMemoryStateModel(workspace_id=workspace_id)
            db.add(state)

        if current_goal:
            state.current_goal = _clean_text(current_goal, 400)
        if active_tasks is not None:
            state.active_tasks = _merge_unique_strings(getattr(state, "active_tasks", []), active_tasks, limit=8)
        if open_questions is not None:
            state.open_questions = _merge_unique_strings(getattr(state, "open_questions", []), open_questions, limit=8)
        if current_draft_summary:
            state.current_draft_summary = _clean_text(current_draft_summary, 600)
        if recent_tool_summary:
            state.recent_tool_summary = _clean_text(recent_tool_summary, 600)
        if latest_workflow_summary:
            state.latest_workflow_summary = _clean_text(latest_workflow_summary, 600)
        if project_focus:
            state.project_focus = _clean_text(project_focus, 400)
        merged_state_json = dict(getattr(state, "state_json", {}) or {})
        merged_state_json.update(dict(state_json or {}))
        state.state_json = merged_state_json
        state.updated_at = utc_now()
        db.commit()
        db.refresh(state)
        return state
    except Exception as exc:
        log_exception(
            logger,
            "storage.working_memory_upsert_failed",
            exc,
            workspace_id=workspace_id,
        )
        raise


# ── Long-Term Memory ──────────────────────────────────────────────────────────

def get_memory_record_by_canonical_key(
    db: Session,
    workspace_id: str,
    canonical_key: str,
) -> Optional[MemoryRecordModel]:
    if not canonical_key:
        return None
    return (
        db.query(MemoryRecordModel)
        .filter(
            MemoryRecordModel.workspace_id == workspace_id,
            MemoryRecordModel.canonical_key == canonical_key,
            MemoryRecordModel.superseded_by == "",
        )
        .first()
    )


def add_memory_record(
    db: Session,
    workspace_id: str,
    *,
    memory_type: str,
    title: str = "",
    content: str = "",
    summary: str = "",
    source_kind: str = "",
    source_reference_id: str = "",
    tags: list[str] | None = None,
    entity_tags: list[str] | None = None,
    tool_tags: list[str] | None = None,
    importance_score: float = 0.5,
    confidence_score: float = 0.5,
    pinned: bool = False,
    canonical_key: str = "",
    metadata_json: dict | None = None,
) -> MemoryRecordModel:
    try:
        row = MemoryRecordModel(
            id=_id(),
            workspace_id=workspace_id,
            memory_type=memory_type,
            title=_clean_text(title, 240),
            content=_clean_text(content, 3000),
            summary=_clean_text(summary, 1000),
            source_kind=source_kind,
            source_reference_id=source_reference_id,
            tags=_merge_unique_strings([], tags, limit=12),
            entity_tags=_merge_unique_strings([], entity_tags, limit=12),
            tool_tags=_merge_unique_strings([], tool_tags, limit=12),
            importance_score=float(importance_score or 0.0),
            confidence_score=float(confidence_score or 0.0),
            access_count=0,
            last_accessed_at=utc_now(),
            pinned=bool(pinned),
            canonical_key=canonical_key or "",
            superseded_by="",
            metadata_json=dict(metadata_json or {}),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        log_exception(
            logger,
            "storage.memory_record_add_failed",
            exc,
            workspace_id=workspace_id,
            memory_type=memory_type,
        )
        raise


def upsert_memory_record(
    db: Session,
    workspace_id: str,
    *,
    memory_type: str,
    title: str = "",
    content: str = "",
    summary: str = "",
    source_kind: str = "",
    source_reference_id: str = "",
    tags: list[str] | None = None,
    entity_tags: list[str] | None = None,
    tool_tags: list[str] | None = None,
    importance_score: float = 0.5,
    confidence_score: float = 0.5,
    pinned: bool = False,
    canonical_key: str = "",
    metadata_json: dict | None = None,
) -> MemoryRecordModel:
    try:
        existing = get_memory_record_by_canonical_key(db, workspace_id, canonical_key) if canonical_key else None
        if not existing:
            return add_memory_record(
                db,
                workspace_id,
                memory_type=memory_type,
                title=title,
                content=content,
                summary=summary,
                source_kind=source_kind,
                source_reference_id=source_reference_id,
                tags=tags,
                entity_tags=entity_tags,
                tool_tags=tool_tags,
                importance_score=importance_score,
                confidence_score=confidence_score,
                pinned=pinned,
                canonical_key=canonical_key,
                metadata_json=metadata_json,
            )

        if title:
            existing.title = _clean_text(title, 240)
        if content:
            existing.content = _clean_text(content, 3000)
        if summary:
            existing.summary = _clean_text(summary, 1000)
        if source_kind:
            existing.source_kind = source_kind
        if source_reference_id:
            existing.source_reference_id = source_reference_id
        existing.tags = _merge_unique_strings(existing.tags, tags, limit=12)
        existing.entity_tags = _merge_unique_strings(existing.entity_tags, entity_tags, limit=12)
        existing.tool_tags = _merge_unique_strings(existing.tool_tags, tool_tags, limit=12)
        existing.importance_score = max(float(getattr(existing, "importance_score", 0.0) or 0.0), float(importance_score or 0.0))
        existing.confidence_score = max(float(getattr(existing, "confidence_score", 0.0) or 0.0), float(confidence_score or 0.0))
        existing.pinned = bool(getattr(existing, "pinned", False) or pinned)
        merged_meta = dict(getattr(existing, "metadata_json", {}) or {})
        merged_meta.update(dict(metadata_json or {}))
        existing.metadata_json = merged_meta
        existing.updated_at = utc_now()
        db.commit()
        db.refresh(existing)
        return existing
    except Exception as exc:
        log_exception(
            logger,
            "storage.memory_record_upsert_failed",
            exc,
            workspace_id=workspace_id,
            memory_type=memory_type,
            canonical_key=canonical_key,
        )
        raise


def list_memory_records(
    db: Session,
    workspace_id: str,
    *,
    limit: int = 50,
    memory_types: list[str] | None = None,
    pinned_only: bool = False,
    include_superseded: bool = False,
) -> list[MemoryRecordModel]:
    query = db.query(MemoryRecordModel).filter(MemoryRecordModel.workspace_id == workspace_id)
    if memory_types:
        query = query.filter(MemoryRecordModel.memory_type.in_(list(memory_types)))
    if pinned_only:
        query = query.filter(MemoryRecordModel.pinned == True)  # noqa: E712
    if not include_superseded:
        query = query.filter(MemoryRecordModel.superseded_by == "")
    return query.order_by(MemoryRecordModel.updated_at.desc()).limit(limit).all()


def get_memory_records_by_ids(
    db: Session,
    workspace_id: str,
    memory_ids: list[str],
) -> list[MemoryRecordModel]:
    if not memory_ids:
        return []
    records = (
        db.query(MemoryRecordModel)
        .filter(
            MemoryRecordModel.workspace_id == workspace_id,
            MemoryRecordModel.id.in_(memory_ids),
        )
        .all()
    )
    record_map = {record.id: record for record in records}
    return [record_map[memory_id] for memory_id in memory_ids if memory_id in record_map]


def search_memory_records(
    db: Session,
    workspace_id: str,
    query: str = "",
    *,
    limit: int = 20,
    memory_types: list[str] | None = None,
) -> list[MemoryRecordModel]:
    items = list_memory_records(
        db,
        workspace_id,
        limit=200,
        memory_types=memory_types,
        pinned_only=False,
        include_superseded=False,
    )
    if not query:
        return items[:limit]
    lowered = query.lower()
    scored: list[tuple[float, MemoryRecordModel]] = []
    for item in items:
        score = 0.0
        if lowered in str(getattr(item, "title", "") or "").lower():
            score += 2.0
        if lowered in str(getattr(item, "summary", "") or "").lower():
            score += 2.0
        if lowered in str(getattr(item, "content", "") or "").lower():
            score += 1.5
        if any(lowered in str(tag).lower() for tag in (getattr(item, "tags", []) or [])):
            score += 1.0
        if any(lowered in str(tag).lower() for tag in (getattr(item, "entity_tags", []) or [])):
            score += 0.8
        if any(lowered in str(tag).lower() for tag in (getattr(item, "tool_tags", []) or [])):
            score += 0.6
        if score > 0:
            scored.append((score + float(getattr(item, "importance_score", 0.0) or 0.0), item))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


def touch_memory_record(db: Session, memory_id: str) -> Optional[MemoryRecordModel]:
    record = db.query(MemoryRecordModel).filter(MemoryRecordModel.id == memory_id).first()
    if not record:
        return None
    record.access_count = int(getattr(record, "access_count", 0) or 0) + 1
    record.last_accessed_at = utc_now()
    record.updated_at = utc_now()
    db.commit()
    db.refresh(record)
    return record


# ── Memory Embeddings ─────────────────────────────────────────────────────────

def get_memory_embedding(
    db: Session,
    memory_record_id: str,
    model_name: str = "",
) -> Optional[MemoryEmbeddingModel]:
    query = db.query(MemoryEmbeddingModel).filter(
        MemoryEmbeddingModel.memory_record_id == memory_record_id
    )
    if model_name:
        query = query.filter(MemoryEmbeddingModel.model_name == model_name)
    return query.first()


def upsert_memory_embedding(
    db: Session,
    workspace_id: str,
    memory_record_id: str,
    *,
    model_name: str,
    content_hash: str,
    vector_json: list[float],
) -> MemoryEmbeddingModel:
    try:
        row = get_memory_embedding(db, memory_record_id, model_name=model_name)
        if not row:
            row = MemoryEmbeddingModel(
                id=_id(),
                workspace_id=workspace_id,
                memory_record_id=memory_record_id,
                model_name=model_name,
                content_hash=content_hash,
                vector_json=list(vector_json or []),
                dimensions=len(vector_json or []),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
        else:
            row.content_hash = content_hash
            row.vector_json = list(vector_json or [])
            row.dimensions = len(vector_json or [])
            row.updated_at = utc_now()
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        log_exception(
            logger,
            "storage.memory_embedding_upsert_failed",
            exc,
            workspace_id=workspace_id,
            memory_record_id=memory_record_id,
            model_name=model_name,
        )
        raise


def list_memory_embeddings(
    db: Session,
    workspace_id: str,
    *,
    model_name: str = "",
    limit: int = 200,
) -> list[MemoryEmbeddingModel]:
    query = db.query(MemoryEmbeddingModel).filter(
        MemoryEmbeddingModel.workspace_id == workspace_id
    )
    if model_name:
        query = query.filter(MemoryEmbeddingModel.model_name == model_name)
    return query.order_by(MemoryEmbeddingModel.updated_at.desc()).limit(limit).all()


# ── Quiz Answers ──────────────────────────────────────────────────────────────

def save_quiz_answer(db: Session, workspace_id: str,
                     question: str, answer: str, category: str) -> QuizAnswerModel:
    qa = QuizAnswerModel(
        id=_id(), workspace_id=workspace_id,
        question=question, answer=answer, category=category,
        created_at=utc_now()
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
            created_at=utc_now()
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
            final_output=final_output, created_at=utc_now()
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
        workflow_hint=workflow_hint, created_at=utc_now()
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


# ── Tool Connections ──────────────────────────────────────────────────────────

def list_tool_connections(
    db: Session,
    workspace_id: str,
    *,
    toolkit: str = "",
    status: str = "",
):
    from models.tool_connections import ToolConnectionModel

    query = db.query(ToolConnectionModel).filter(
        ToolConnectionModel.workspace_id == workspace_id
    )
    if toolkit:
        query = query.filter(ToolConnectionModel.toolkit == toolkit.upper())
    if status:
        query = query.filter(ToolConnectionModel.status == status)
    return query.order_by(ToolConnectionModel.is_default.desc(), ToolConnectionModel.updated_at.desc()).all()


def upsert_tool_connection(
    db: Session,
    workspace_id: str,
    *,
    toolkit: str,
    connected_account_id: str = "",
    tool_name: str = "*",
    status: str = "connected",
    auth_mode: str = "oauth2",
    account_label: str = "",
    is_default: bool = False,
    last_verified_at = None,
    last_seen_remote_at = None,
    revoked_at = None,
    status_reason: str = "",
    status_updated_at = None,
    metadata_json: dict | None = None,
):
    from models.tool_connections import ToolConnectionModel

    try:
        query = db.query(ToolConnectionModel).filter(
            ToolConnectionModel.workspace_id == workspace_id,
            ToolConnectionModel.toolkit == toolkit.upper(),
        )
        if connected_account_id:
            query = query.filter(ToolConnectionModel.connected_account_id == connected_account_id)
        else:
            query = query.filter(ToolConnectionModel.connected_account_id == "")
        row = query.first()
        if not row:
            row = ToolConnectionModel(
                id=_id(),
                workspace_id=workspace_id,
                user_id=workspace_id,
                tool_name=tool_name or "*",
                toolkit=toolkit.upper(),
                status=status,
                connected_account_id=connected_account_id or "",
                account_label=account_label or "",
                is_default=bool(is_default),
                auth_mode=auth_mode,
                metadata_json=dict(metadata_json or {}),
                last_verified_at=last_verified_at,
                last_seen_remote_at=last_seen_remote_at,
                revoked_at=revoked_at,
                status_reason=status_reason or "",
                status_updated_at=status_updated_at or utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
        else:
            previous_status = str(getattr(row, "status", "") or "")
            row.tool_name = tool_name or row.tool_name
            row.status = status
            row.auth_mode = auth_mode or row.auth_mode
            row.connected_account_id = connected_account_id or row.connected_account_id
            row.account_label = account_label or row.account_label
            row.is_default = bool(is_default or getattr(row, "is_default", False))
            if last_verified_at is not None:
                row.last_verified_at = last_verified_at
            if last_seen_remote_at is not None:
                row.last_seen_remote_at = last_seen_remote_at
            if revoked_at is not None:
                row.revoked_at = revoked_at
            if status_reason:
                row.status_reason = status_reason
            merged_meta = dict(getattr(row, "metadata_json", {}) or {})
            merged_meta.update(dict(metadata_json or {}))
            row.metadata_json = merged_meta
            if previous_status != status or status_updated_at is not None:
                row.status_updated_at = status_updated_at or utc_now()
            row.updated_at = utc_now()
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        log_exception(
            logger,
            "storage.tool_connection_upsert_failed",
            exc,
            workspace_id=workspace_id,
            toolkit=toolkit,
            connected_account_id=connected_account_id,
        )
        raise


def set_tool_connection_status(
    db: Session,
    workspace_id: str,
    *,
    toolkit: str,
    connected_account_id: str,
    status: str,
    is_default: bool | None = None,
    revoked_at = None,
    status_reason: str = "",
    last_seen_remote_at = None,
):
    from models.tool_connections import ToolConnectionModel

    row = (
        db.query(ToolConnectionModel)
        .filter(
            ToolConnectionModel.workspace_id == workspace_id,
            ToolConnectionModel.toolkit == toolkit.upper(),
            ToolConnectionModel.connected_account_id == (connected_account_id or ""),
        )
        .first()
    )
    if not row:
        return None
    row.status = status
    if is_default is not None:
        row.is_default = bool(is_default)
    if revoked_at is not None:
        row.revoked_at = revoked_at
    if last_seen_remote_at is not None:
        row.last_seen_remote_at = last_seen_remote_at
    if status_reason:
        row.status_reason = status_reason
    row.status_updated_at = utc_now()
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def get_workspace_connector_preference(
    db: Session,
    workspace_id: str,
) -> Optional[WorkspaceConnectorPreferenceModel]:
    return db.query(WorkspaceConnectorPreferenceModel).filter(
        WorkspaceConnectorPreferenceModel.workspace_id == workspace_id
    ).first()


def upsert_workspace_connector_preference(
    db: Session,
    workspace_id: str,
    *,
    mode: str = "auto",
    selected_toolkit: str = "",
    selected_account_id: str = "",
    selected_account_alias: str = "",
    source: str = "persisted_default",
) -> WorkspaceConnectorPreferenceModel:
    try:
        row = get_workspace_connector_preference(db, workspace_id)
        if not row:
            row = WorkspaceConnectorPreferenceModel(
                workspace_id=workspace_id,
                mode=mode,
                selected_toolkit=selected_toolkit,
                selected_account_id=selected_account_id,
                selected_account_alias=selected_account_alias,
                source=source,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
        else:
            row.mode = mode
            row.selected_toolkit = selected_toolkit
            row.selected_account_id = selected_account_id
            row.selected_account_alias = selected_account_alias
            row.source = source or row.source
            row.updated_at = utc_now()
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        log_exception(
            logger,
            "storage.connector_preference_upsert_failed",
            exc,
            workspace_id=workspace_id,
        )
        raise
