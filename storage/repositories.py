"""
storage/repositories.py  —  CRUD helpers for every table
"""
import logging
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy.orm import Session

from storage.db import (
    WorkspaceModel, BrainProfileModel, KnowledgeItemModel,
    QuizAnswerModel, ConversationModel, WorkflowRunModel, IdeaModel,
    MemoryRecordModel, WorkingMemoryStateModel, MemoryEmbeddingModel,
    WorkspaceConnectorPreferenceModel, UserModel, ExternalIdentityModel,
    WorkspaceMembershipModel, AuthSessionModel,
)
from utils.logging_utils import log_event, log_exception
from utils.time_utils import utc_now


logger = logging.getLogger(__name__)
PENDING_REQUEST_TTL_HOURS = int(os.getenv("SINTRA_PENDING_REQUEST_TTL_HOURS", "72") or "72")
_TABLE_COLUMN_CACHE: dict[tuple[int, str], set[str] | None] = {}
_REFLECTED_TABLE_CACHE: dict[tuple[int, str], Any] = {}


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


def _is_integrity_error(exc: Exception) -> bool:
    try:
        from sqlalchemy.exc import IntegrityError
    except Exception:
        return False
    return isinstance(exc, IntegrityError)


def _is_missing_column_error(exc: Exception) -> bool:
    message = str(getattr(exc, "orig", exc) or "")
    lowered = message.lower()
    return (
        "undefinedcolumn" in lowered
        or ("column" in lowered and "does not exist" in lowered)
    )


def _pending_request_expiry():
    return utc_now() + timedelta(hours=PENDING_REQUEST_TTL_HOURS)


def _safe_rollback(db: Session) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:
            pass


def _get_bind(db: Session):
    getter = getattr(db, "get_bind", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    return getattr(db, "bind", None)


def _get_table_columns(db: Session, table_name: str) -> set[str] | None:
    bind = _get_bind(db)
    if bind is None:
        return None
    engine = getattr(bind, "engine", bind)
    cache_key = (id(engine), table_name)
    if cache_key in _TABLE_COLUMN_CACHE:
        return _TABLE_COLUMN_CACHE[cache_key]
    try:
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(bind)
        columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    except Exception:
        columns = None
    _TABLE_COLUMN_CACHE[cache_key] = columns
    return columns


def _table_has_column(db: Session, table_name: str, column_name: str) -> bool:
    columns = _get_table_columns(db, table_name)
    if columns is None:
        return True
    return column_name in columns


def _get_reflected_table(db: Session, table_name: str):
    bind = _get_bind(db)
    if bind is None:
        return None
    engine = getattr(bind, "engine", bind)
    cache_key = (id(engine), table_name)
    if cache_key in _REFLECTED_TABLE_CACHE:
        return _REFLECTED_TABLE_CACHE[cache_key]
    try:
        from sqlalchemy import MetaData, Table

        table = Table(table_name, MetaData(), autoload_with=bind)
    except Exception:
        table = None
    _REFLECTED_TABLE_CACHE[cache_key] = table
    return table


def _model_instance(model_cls, **values):
    try:
        return model_cls(**values)
    except Exception:
        return SimpleNamespace(**values)


def _workspace_from_mapping(values: dict[str, Any]):
    return _model_instance(
        WorkspaceModel,
        id=values.get("id", ""),
        name=values.get("name", ""),
        owner_user_id=values.get("owner_user_id"),
        created_at=values.get("created_at"),
    )


def _user_from_mapping(values: dict[str, Any]):
    return _model_instance(
        UserModel,
        id=values.get("id", ""),
        email=values.get("email", "") or "",
        display_name=values.get("display_name", "") or "",
        avatar_url=values.get("avatar_url", "") or "",
        status=values.get("status", "active") or "active",
        metadata_json=dict(values.get("metadata_json", {}) or {}),
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


def _workspace_membership_from_mapping(values: dict[str, Any]):
    return _model_instance(
        WorkspaceMembershipModel,
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        user_id=values.get("user_id", ""),
        role=values.get("role", "member") or "member",
        status=values.get("status", "active") or "active",
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


def _conversation_from_mapping(values: dict[str, Any]):
    return _model_instance(
        ConversationModel,
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        helper=values.get("helper", ""),
        input=values.get("input", ""),
        output=values.get("output", ""),
        request_id=values.get("request_id", "") or "",
        actor_user_id=values.get("actor_user_id"),
        membership_id=values.get("membership_id"),
        metadata_json=dict(values.get("metadata_json", {}) or {}),
        created_at=values.get("created_at"),
    )


def _workflow_run_from_mapping(values: dict[str, Any]):
    return _model_instance(
        WorkflowRunModel,
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        workflow_name=values.get("workflow_name", ""),
        steps=list(values.get("steps", []) or []),
        final_output=values.get("final_output", "") or "",
        status=values.get("status", "completed") or "completed",
        request_id=values.get("request_id", "") or "",
        actor_user_id=values.get("actor_user_id"),
        membership_id=values.get("membership_id"),
        metadata_json=dict(values.get("metadata_json", {}) or {}),
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at") or values.get("created_at"),
    )


def _tool_connection_from_mapping(values: dict[str, Any]):
    raw_metadata = values.get("metadata_json", {}) or {}
    metadata_json = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    account_label = values.get("account_label", "") or metadata_json.get("account_label", "") or metadata_json.get("account_alias", "") or ""
    return _model_instance(
        _get_tool_connection_model_cls(),
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        user_id=values.get("user_id", "") or None,
        tool_name=values.get("tool_name", "*") or "*",
        toolkit=values.get("toolkit", "") or "",
        status=values.get("status", "pending") or "pending",
        connected_account_id=values.get("connected_account_id", "") or "",
        account_label=account_label,
        is_default=bool(values.get("is_default", False)),
        auth_mode=values.get("auth_mode", "oauth2") or "oauth2",
        metadata_json=metadata_json,
        last_verified_at=values.get("last_verified_at") or values.get("updated_at"),
        last_seen_remote_at=values.get("last_seen_remote_at"),
        revoked_at=values.get("revoked_at"),
        status_reason=values.get("status_reason", "") or "",
        status_updated_at=values.get("status_updated_at") or values.get("updated_at"),
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


def _connector_preference_from_mapping(values: dict[str, Any]):
    return _model_instance(
        WorkspaceConnectorPreferenceModel,
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        scope_type=values.get("scope_type", "workspace") or "workspace",
        user_id=values.get("user_id"),
        membership_id=values.get("membership_id"),
        selected_by_user_id=values.get("selected_by_user_id"),
        mode=values.get("mode", "auto") or "auto",
        selected_toolkit=values.get("selected_toolkit", "") or "",
        selected_account_id=values.get("selected_account_id", "") or "",
        selected_account_alias=values.get("selected_account_alias", "") or "",
        source=values.get("source", "persisted_default") or "persisted_default",
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


def _pending_tool_request_from_mapping(values: dict[str, Any]):
    context_json = dict(values.get("context_json", {}) or {})
    return _model_instance(
        _get_pending_tool_request_model_cls(),
        id=values.get("id", ""),
        workspace_id=values.get("workspace_id", ""),
        actor_user_id=values.get("actor_user_id"),
        membership_id=values.get("membership_id"),
        conversation_id=values.get("conversation_id", "") or "",
        agent_key=values.get("agent_key", "") or "",
        original_input=values.get("original_input", "") or "",
        requested_tool=values.get("requested_tool", "") or "",
        requested_toolkit=values.get("requested_toolkit", "") or "",
        resume_token=values.get("resume_token", "") or "",
        status=values.get("status", "pending") or "pending",
        pending_kind=values.get("pending_kind", context_json.get("pending_kind", "auth")) or "auth",
        idempotency_key=values.get("idempotency_key", context_json.get("idempotency_key", "")) or "",
        approval_requirement_json=dict(
            values.get("approval_requirement_json", context_json.get("approval_requirement_json", {})) or {}
        ),
        approved=bool(values.get("approved", context_json.get("approved", False))),
        approved_at=values.get("approved_at"),
        expires_at=values.get("expires_at"),
        context_json=context_json,
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


def _get_pending_tool_request_model_cls():
    model = globals().get("_pending_tool_request_model")
    if model is not None:
        return model
    from models.pending_tool_requests import PendingToolRequestModel
    return PendingToolRequestModel


def _get_tool_connection_model_cls():
    model = globals().get("_tool_connection_model")
    if model is not None:
        return model
    from models.tool_connections import ToolConnectionModel
    return ToolConnectionModel


def _get_tool_idempotency_model_cls():
    model = globals().get("_tool_idempotency_model")
    if model is not None:
        return model
    from models.tool_idempotency_records import ToolIdempotencyRecordModel
    return ToolIdempotencyRecordModel


def _get_scheduled_task_model_cls():
    model = globals().get("_scheduled_task_model")
    if model is not None:
        return model
    from models.scheduled_tasks import ScheduledTaskModel
    return ScheduledTaskModel


def _get_scheduled_task_run_model_cls():
    model = globals().get("_scheduled_task_run_model")
    if model is not None:
        return model
    from models.scheduled_tasks import ScheduledTaskRunModel
    return ScheduledTaskRunModel


# ── Users / Auth / Memberships ───────────────────────────────────────────────

def get_user(db: Session, user_id: str) -> Optional[UserModel]:
    if not user_id:
        return None
    return db.query(UserModel).filter(UserModel.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[UserModel]:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    return db.query(UserModel).filter(UserModel.email == normalized).first()


def upsert_user(
    db: Session,
    *,
    user_id: str = "",
    email: str = "",
    display_name: str = "",
    avatar_url: str = "",
    metadata_json: dict | None = None,
) -> UserModel:
    normalized_email = str(email or "").strip().lower()
    row = get_user(db, user_id) if user_id else None
    if not row and normalized_email:
        row = get_user_by_email(db, normalized_email)
    if not row:
        row = UserModel(
            id=user_id or _id(),
            email=normalized_email,
            display_name=display_name or "",
            avatar_url=avatar_url or "",
            status="active",
            metadata_json=dict(metadata_json or {}),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        if normalized_email:
            row.email = normalized_email
        if display_name:
            row.display_name = display_name
        if avatar_url:
            row.avatar_url = avatar_url
        merged_metadata = dict(getattr(row, "metadata_json", {}) or {})
        merged_metadata.update(dict(metadata_json or {}))
        row.metadata_json = merged_metadata
        row.status = getattr(row, "status", "") or "active"
        row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def get_external_identity(db: Session, provider: str, provider_subject: str) -> Optional[ExternalIdentityModel]:
    return db.query(ExternalIdentityModel).filter(
        ExternalIdentityModel.provider == provider,
        ExternalIdentityModel.provider_subject == provider_subject,
    ).first()


def upsert_external_identity(
    db: Session,
    *,
    user_id: str,
    provider: str,
    provider_subject: str,
    email: str = "",
    metadata_json: dict | None = None,
) -> ExternalIdentityModel:
    row = get_external_identity(db, provider, provider_subject)
    if not row:
        row = ExternalIdentityModel(
            id=_id(),
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            email=str(email or "").strip().lower(),
            metadata_json=dict(metadata_json or {}),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        row.user_id = user_id
        if email:
            row.email = str(email or "").strip().lower()
        merged_metadata = dict(getattr(row, "metadata_json", {}) or {})
        merged_metadata.update(dict(metadata_json or {}))
        row.metadata_json = merged_metadata
        row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def upsert_workspace_membership(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    role: str = "member",
    status: str = "active",
) -> WorkspaceMembershipModel:
    row = db.query(WorkspaceMembershipModel).filter(
        WorkspaceMembershipModel.workspace_id == workspace_id,
        WorkspaceMembershipModel.user_id == user_id,
    ).first()
    if not row:
        row = WorkspaceMembershipModel(
            id=_id(),
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
            status=status,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        row.role = role or row.role
        row.status = status or row.status
        row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def get_workspace_membership(db: Session, workspace_id: str, user_id: str):
    if not workspace_id or not user_id:
        return None
    return db.query(WorkspaceMembershipModel).filter(
        WorkspaceMembershipModel.workspace_id == workspace_id,
        WorkspaceMembershipModel.user_id == user_id,
        WorkspaceMembershipModel.status == "active",
    ).first()


def list_user_memberships(db: Session, user_id: str):
    if not user_id:
        return []
    return db.query(WorkspaceMembershipModel).filter(
        WorkspaceMembershipModel.user_id == user_id,
        WorkspaceMembershipModel.status == "active",
    ).order_by(WorkspaceMembershipModel.created_at.asc()).all()


def create_auth_session(
    db: Session,
    *,
    user_id: str,
    session_hash: str,
    expires_at,
    metadata_json: dict | None = None,
) -> AuthSessionModel:
    row = AuthSessionModel(
        id=_id(),
        user_id=user_id,
        session_hash=session_hash,
        status="active",
        metadata_json=dict(metadata_json or {}),
        expires_at=expires_at,
        created_at=utc_now(),
        last_seen_at=utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_active_auth_session_by_hash(db: Session, session_hash: str) -> Optional[AuthSessionModel]:
    if not session_hash:
        return None
    row = db.query(AuthSessionModel).filter(
        AuthSessionModel.session_hash == session_hash,
        AuthSessionModel.status == "active",
    ).first()
    if not row:
        return None
    if getattr(row, "expires_at", None) and row.expires_at <= utc_now():
        row.status = "expired"
        row.last_seen_at = utc_now()
        db.commit()
        return None
    row.last_seen_at = utc_now()
    db.commit()
    return row


def revoke_auth_session(db: Session, session_hash: str) -> bool:
    row = get_active_auth_session_by_hash(db, session_hash)
    if not row:
        return False
    row.status = "revoked"
    row.revoked_at = utc_now()
    row.last_seen_at = utc_now()
    db.commit()
    return True


# ── Workspace ─────────────────────────────────────────────────────────────────

def create_workspace(db: Session, name: str, *, owner_user_id: str | None = None) -> WorkspaceModel:
    ws_id = _id()
    created_at = utc_now()
    try:
        ws = WorkspaceModel(id=ws_id, name=name, owner_user_id=owner_user_id, created_at=created_at)
        db.add(ws)
        # seed empty brain profile
        bp = BrainProfileModel(workspace_id=ws.id)
        db.add(bp)
        db.commit()
        if owner_user_id:
            upsert_workspace_membership(
                db,
                workspace_id=ws.id,
                user_id=owner_user_id,
                role="owner",
                status="active",
            )
            db.refresh(ws)
        db.refresh(ws)
        return ws
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        db.rollback()
        table = _get_reflected_table(db, "workspaces")
        if table is None:
            raise
        insert_values = {"id": ws_id, "name": name}
        if "created_at" in table.c:
            insert_values["created_at"] = created_at
        db.execute(table.insert().values(**insert_values))
        bp = BrainProfileModel(workspace_id=ws_id)
        db.add(bp)
        db.commit()
        return _workspace_from_mapping(
            {
                "id": ws_id,
                "name": name,
                "created_at": created_at,
                "owner_user_id": None,
            }
        )


def get_workspace(db: Session, workspace_id: str) -> Optional[WorkspaceModel]:
    try:
        return db.query(WorkspaceModel).filter(WorkspaceModel.id == workspace_id).first()
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "workspaces")
        if table is None:
            raise
        row = (
            db.execute(
                table.select()
                .with_only_columns(*[
                    table.c.id,
                    table.c.name,
                    table.c.created_at,
                ])
                .where(table.c.id == workspace_id)
            )
            .mappings()
            .first()
        )
        return _workspace_from_mapping(dict(row)) if row else None


def list_workspaces(db: Session):
    try:
        return db.query(WorkspaceModel).order_by(WorkspaceModel.created_at).all()
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "workspaces")
        if table is None:
            raise
        rows = (
            db.execute(
                table.select()
                .with_only_columns(*[
                    table.c.id,
                    table.c.name,
                    table.c.created_at,
                ])
                .order_by(table.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_workspace_from_mapping(dict(row)) for row in rows]


def list_workspaces_for_user(db: Session, user_id: str):
    if not user_id:
        return []
    return (
        db.query(WorkspaceModel)
        .join(WorkspaceMembershipModel, WorkspaceMembershipModel.workspace_id == WorkspaceModel.id)
        .filter(
            WorkspaceMembershipModel.user_id == user_id,
            WorkspaceMembershipModel.status == "active",
        )
        .order_by(WorkspaceModel.created_at)
        .all()
    )


def ensure_default_workspace_for_user(db: Session, user: UserModel) -> WorkspaceModel:
    memberships = list_user_memberships(db, user.id)
    if memberships:
        workspace = get_workspace(db, memberships[0].workspace_id)
        if workspace:
            return workspace
    display_name = str(getattr(user, "display_name", "") or "").strip()
    email = str(getattr(user, "email", "") or "").strip()
    workspace_name = f"{display_name or email or 'My'} Workspace"
    return create_workspace(db, workspace_name, owner_user_id=user.id)


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
            try:
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
            except Exception as exc:
                if not canonical_key or not _is_integrity_error(exc):
                    raise
                try:
                    db.rollback()
                except Exception:
                    pass
                existing = get_memory_record_by_canonical_key(db, workspace_id, canonical_key)
                if not existing:
                    raise

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
        if _is_integrity_error(exc):
            try:
                db.rollback()
            except Exception:
                pass
            row = get_memory_embedding(db, memory_record_id, model_name=model_name)
            if row:
                row.content_hash = content_hash
                row.vector_json = list(vector_json or [])
                row.dimensions = len(vector_json or [])
                row.updated_at = utc_now()
                db.commit()
                db.refresh(row)
                return row
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

def save_conversation(
    db: Session,
    workspace_id: str,
    helper: str,
    input_: str,
    output: str,
    *,
    request_id: str = "",
    metadata_json: dict | None = None,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
) -> ConversationModel:
    try:
        conv = ConversationModel(
            id=_id(), workspace_id=workspace_id,
            helper=helper, input=input_, output=output,
            request_id=request_id or "",
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            metadata_json=dict(metadata_json or {}),
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
        if _is_missing_column_error(exc):
            _safe_rollback(db)
            table = _get_reflected_table(db, "conversations")
            if table is None:
                raise
            conv_id = _id()
            created_at = utc_now()
            values = {
                "id": conv_id,
                "workspace_id": workspace_id,
                "helper": helper,
                "input": input_,
                "output": output,
                "created_at": created_at,
            }
            if "request_id" in table.c:
                values["request_id"] = request_id or ""
            if "actor_user_id" in table.c:
                values["actor_user_id"] = actor_user_id
            if "membership_id" in table.c:
                values["membership_id"] = membership_id
            if "metadata_json" in table.c:
                values["metadata_json"] = dict(metadata_json or {})
            db.execute(table.insert().values(**values))
            db.commit()
            return _conversation_from_mapping(
                {
                    **values,
                    "request_id": request_id or "",
                    "membership_id": membership_id,
                    "metadata_json": dict(metadata_json or {}),
                }
            )
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
        if _is_missing_column_error(exc):
            _safe_rollback(db)
            table = _get_reflected_table(db, "conversations")
            if table is None:
                raise
            column_names = ["id", "workspace_id", "helper", "input", "output", "created_at"]
            if "request_id" in table.c:
                column_names.append("request_id")
            if "actor_user_id" in table.c:
                column_names.append("actor_user_id")
            if "membership_id" in table.c:
                column_names.append("membership_id")
            if "metadata_json" in table.c:
                column_names.append("metadata_json")
            rows = (
                db.execute(
                    table.select()
                    .with_only_columns(*[table.c[name] for name in column_names])
                    .where(table.c.workspace_id == workspace_id)
                    .order_by(table.c.created_at.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return [_conversation_from_mapping(dict(row)) for row in rows]
        log_exception(
            logger,
            "storage.conversation_fetch_failed",
            exc,
            workspace_id=workspace_id,
            limit=limit,
        )
        raise


# ── Workflow Runs ─────────────────────────────────────────────────────────────

def save_workflow_run(
    db: Session,
    workspace_id: str,
    workflow_name: str,
    steps: list,
    final_output: str,
    *,
    status: str = "completed",
    request_id: str = "",
    metadata_json: dict | None = None,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
) -> WorkflowRunModel:
    try:
        run = WorkflowRunModel(
            id=_id(), workspace_id=workspace_id,
            workflow_name=workflow_name, steps=steps,
            final_output=final_output,
            status=status or "completed",
            request_id=request_id or "",
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            metadata_json=dict(metadata_json or {}),
            created_at=utc_now(),
            updated_at=utc_now(),
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
        if _is_missing_column_error(exc):
            _safe_rollback(db)
            table = _get_reflected_table(db, "workflow_runs")
            if table is None:
                raise
            run_id = _id()
            created_at = utc_now()
            updated_at = utc_now()
            values = {
                "id": run_id,
                "workspace_id": workspace_id,
                "workflow_name": workflow_name,
                "steps": steps,
                "final_output": final_output,
                "created_at": created_at,
            }
            if "status" in table.c:
                values["status"] = status or "completed"
            if "request_id" in table.c:
                values["request_id"] = request_id or ""
            if "actor_user_id" in table.c:
                values["actor_user_id"] = actor_user_id
            if "membership_id" in table.c:
                values["membership_id"] = membership_id
            if "metadata_json" in table.c:
                values["metadata_json"] = dict(metadata_json or {})
            if "updated_at" in table.c:
                values["updated_at"] = updated_at
            db.execute(table.insert().values(**values))
            db.commit()
            return _workflow_run_from_mapping(
                {
                    **values,
                    "status": status or "completed",
                    "request_id": request_id or "",
                    "membership_id": membership_id,
                    "metadata_json": dict(metadata_json or {}),
                    "updated_at": updated_at,
                }
            )
        log_exception(
            logger,
            "storage.workflow_save_failed",
            exc,
            workspace_id=workspace_id,
            workflow_name=workflow_name,
        )
        raise


def get_workflow_runs(db: Session, workspace_id: str, limit: int = 10):
    try:
        return db.query(WorkflowRunModel).filter(
            WorkflowRunModel.workspace_id == workspace_id
        ).order_by(WorkflowRunModel.created_at.desc()).limit(limit).all()
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "workflow_runs")
        if table is None:
            raise
        column_names = ["id", "workspace_id", "workflow_name", "steps", "final_output", "created_at"]
        for name in ("status", "request_id", "actor_user_id", "membership_id", "metadata_json", "updated_at"):
            if name in table.c:
                column_names.append(name)
        rows = (
            db.execute(
                table.select()
                .with_only_columns(*[table.c[name] for name in column_names])
                .where(table.c.workspace_id == workspace_id)
                .order_by(table.c.created_at.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_workflow_run_from_mapping(dict(row)) for row in rows]


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
    PendingToolRequestModel = _get_pending_tool_request_model_cls()

    try:
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
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        rows = (
            db.execute(
                table.select()
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.status.in_(["pending", "resumed"]),
                )
                .order_by(table.c.updated_at.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_pending_tool_request_from_mapping(dict(row)) for row in rows]


# ── Tool Connections ──────────────────────────────────────────────────────────

def list_tool_connections(
    db: Session,
    workspace_id: str,
    *,
    toolkit: str = "",
    status: str = "",
):
    ToolConnectionModel = _get_tool_connection_model_cls()

    try:
        query = db.query(ToolConnectionModel).filter(
            ToolConnectionModel.workspace_id == workspace_id
        )
        if toolkit:
            query = query.filter(ToolConnectionModel.toolkit == toolkit.upper())
        if status:
            query = query.filter(ToolConnectionModel.status == status)
        return query.order_by(ToolConnectionModel.is_default.desc(), ToolConnectionModel.updated_at.desc()).all()
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "tool_connections")
        if table is None:
            raise
        stmt = table.select().where(table.c.workspace_id == workspace_id)
        if toolkit:
            stmt = stmt.where(table.c.toolkit == toolkit.upper())
        if status:
            stmt = stmt.where(table.c.status == status)
        order_columns = []
        if "is_default" in table.c:
            order_columns.append(table.c.is_default.desc())
        if "updated_at" in table.c:
            order_columns.append(table.c.updated_at.desc())
        if order_columns:
            stmt = stmt.order_by(*order_columns)
        rows = db.execute(stmt).mappings().all()
        return [_tool_connection_from_mapping(dict(row)) for row in rows]


def get_tool_connection(
    db: Session,
    workspace_id: str,
    *,
    toolkit: str,
    connected_account_id: str = "",
    status: str = "",
):
    ToolConnectionModel = _get_tool_connection_model_cls()

    try:
        query = db.query(ToolConnectionModel).filter(
            ToolConnectionModel.workspace_id == workspace_id,
            ToolConnectionModel.toolkit == toolkit.upper(),
        )
        if status:
            query = query.filter(ToolConnectionModel.status == status)
        query = query.filter(ToolConnectionModel.connected_account_id == (connected_account_id or ""))
        return query.first()
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "tool_connections")
        if table is None:
            raise
        stmt = table.select().where(
            table.c.workspace_id == workspace_id,
            table.c.toolkit == toolkit.upper(),
            table.c.connected_account_id == (connected_account_id or ""),
        )
        if status:
            stmt = stmt.where(table.c.status == status)
        row = db.execute(stmt).mappings().first()
        return _tool_connection_from_mapping(dict(row)) if row else None


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
    ToolConnectionModel = _get_tool_connection_model_cls()

    try:
        normalized_toolkit = toolkit.upper()
        normalized_account_id = connected_account_id or ""
        row = get_tool_connection(
            db,
            workspace_id,
            toolkit=normalized_toolkit,
            connected_account_id=normalized_account_id,
        )
        existing_metadata = dict(getattr(row, "metadata_json", {}) or {}) if row else {}
        merged_metadata = dict(existing_metadata)
        merged_metadata.update(dict(metadata_json or {}))
        if not row:
            row = ToolConnectionModel(
                id=_id(),
                workspace_id=workspace_id,
                user_id=None,
                tool_name=tool_name or "*",
                toolkit=normalized_toolkit,
                status=status,
                connected_account_id=normalized_account_id,
                account_label=account_label or "",
                is_default=bool(is_default),
                auth_mode=auth_mode,
                metadata_json=merged_metadata,
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
            row.connected_account_id = normalized_account_id or row.connected_account_id
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
            row.metadata_json = merged_metadata
            if previous_status != status or status_updated_at is not None:
                row.status_updated_at = status_updated_at or utc_now()
            row.updated_at = utc_now()
        if row.is_default:
            for sibling in db.query(ToolConnectionModel).filter(
                ToolConnectionModel.workspace_id == workspace_id,
                ToolConnectionModel.toolkit == normalized_toolkit,
            ).all():
                if getattr(sibling, "connected_account_id", "") != normalized_account_id:
                    sibling.is_default = False
                    sibling.updated_at = utc_now()
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        if _is_missing_column_error(exc):
            _safe_rollback(db)
            table = _get_reflected_table(db, "tool_connections")
            if table is None:
                raise
            normalized_toolkit = toolkit.upper()
            normalized_account_id = connected_account_id or ""
            existing_row = db.execute(
                table.select().where(
                    table.c.workspace_id == workspace_id,
                    table.c.toolkit == normalized_toolkit,
                    table.c.connected_account_id == normalized_account_id,
                )
            ).mappings().first()
            base_metadata = dict((existing_row or {}).get("metadata_json", {}) or {})
            merged_metadata = dict(base_metadata)
            merged_metadata.update(dict(metadata_json or {}))
            if account_label:
                merged_metadata.setdefault("account_label", account_label)
            if is_default:
                merged_metadata["requested_is_default"] = True
            if status_reason:
                merged_metadata["status_reason"] = status_reason
            if last_verified_at is not None:
                merged_metadata["last_verified_at"] = str(last_verified_at)
            if last_seen_remote_at is not None:
                merged_metadata["last_seen_remote_at"] = str(last_seen_remote_at)
            values = {
                "workspace_id": workspace_id,
                "user_id": "",
                "tool_name": tool_name or "*",
                "toolkit": normalized_toolkit,
                "status": status,
                "connected_account_id": normalized_account_id,
                "auth_mode": auth_mode,
                "metadata_json": merged_metadata,
            }
            if "account_label" in table.c:
                values["account_label"] = account_label or base_metadata.get("account_label", "")
            if "is_default" in table.c:
                values["is_default"] = bool(is_default)
            if "last_verified_at" in table.c and last_verified_at is not None:
                values["last_verified_at"] = last_verified_at
            if "last_seen_remote_at" in table.c and last_seen_remote_at is not None:
                values["last_seen_remote_at"] = last_seen_remote_at
            if "revoked_at" in table.c:
                values["revoked_at"] = revoked_at
            if "status_reason" in table.c:
                values["status_reason"] = status_reason or ""
            if "status_updated_at" in table.c:
                values["status_updated_at"] = status_updated_at or utc_now()
            if "updated_at" in table.c:
                values["updated_at"] = utc_now()
            if existing_row:
                db.execute(
                    table.update()
                    .where(table.c.id == existing_row["id"])
                    .values(**{key: value for key, value in values.items() if key in table.c})
                )
            else:
                insert_values = {"id": _id(), "created_at": utc_now(), **values}
                db.execute(
                    table.insert().values(
                        **{key: value for key, value in insert_values.items() if key in table.c}
                    )
                )
            if is_default and "is_default" in table.c:
                sibling_update_values = {"is_default": False}
                if "updated_at" in table.c:
                    sibling_update_values["updated_at"] = utc_now()
                db.execute(
                    table.update()
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.toolkit == normalized_toolkit,
                        table.c.connected_account_id != normalized_account_id,
                    )
                    .values(**sibling_update_values)
                )
            db.commit()
            refreshed = db.execute(
                table.select().where(
                    table.c.workspace_id == workspace_id,
                    table.c.toolkit == normalized_toolkit,
                    table.c.connected_account_id == normalized_account_id,
                )
            ).mappings().first()
            return _tool_connection_from_mapping(dict(refreshed)) if refreshed else None
        if _is_integrity_error(exc):
            try:
                db.rollback()
            except Exception:
                pass
            row = get_tool_connection(
                db,
                workspace_id,
                toolkit=toolkit,
                connected_account_id=connected_account_id,
            )
            if row:
                if account_label:
                    row.account_label = account_label
                if metadata_json:
                    merged = dict(getattr(row, "metadata_json", {}) or {})
                    merged.update(dict(metadata_json or {}))
                    row.metadata_json = merged
                if last_verified_at is not None:
                    row.last_verified_at = last_verified_at
                if last_seen_remote_at is not None:
                    row.last_seen_remote_at = last_seen_remote_at
                if revoked_at is not None:
                    row.revoked_at = revoked_at
                row.status = status
                row.status_reason = status_reason or row.status_reason
                row.updated_at = utc_now()
                row.status_updated_at = status_updated_at or utc_now()
                db.commit()
                db.refresh(row)
                return row
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
    ToolConnectionModel = _get_tool_connection_model_cls()

    try:
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
        elif status != "connected":
            row.is_default = False
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
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "tool_connections")
        if table is None:
            raise
        existing_row = db.execute(
            table.select().where(
                table.c.workspace_id == workspace_id,
                table.c.toolkit == toolkit.upper(),
                table.c.connected_account_id == (connected_account_id or ""),
            )
        ).mappings().first()
        if not existing_row:
            return None
        merged_metadata = dict(existing_row.get("metadata_json", {}) or {})
        if status_reason:
            merged_metadata["status_reason"] = status_reason
        if revoked_at is not None:
            merged_metadata["revoked_at"] = str(revoked_at)
        if last_seen_remote_at is not None:
            merged_metadata["last_seen_remote_at"] = str(last_seen_remote_at)
        update_values = {
            "status": status,
            "metadata_json": merged_metadata,
        }
        if "is_default" in table.c:
            update_values["is_default"] = bool(is_default) if is_default is not None else status == "connected" and bool(existing_row.get("is_default", False))
            if status != "connected" and is_default is None:
                update_values["is_default"] = False
        if "revoked_at" in table.c and revoked_at is not None:
            update_values["revoked_at"] = revoked_at
        if "last_seen_remote_at" in table.c and last_seen_remote_at is not None:
            update_values["last_seen_remote_at"] = last_seen_remote_at
        if "status_reason" in table.c and status_reason:
            update_values["status_reason"] = status_reason
        if "status_updated_at" in table.c:
            update_values["status_updated_at"] = utc_now()
        if "updated_at" in table.c:
            update_values["updated_at"] = utc_now()
        db.execute(
            table.update()
            .where(table.c.id == existing_row["id"])
            .values(**update_values)
        )
        db.commit()
        refreshed = db.execute(
            table.select().where(table.c.id == existing_row["id"])
        ).mappings().first()
        return _tool_connection_from_mapping(dict(refreshed)) if refreshed else None


def _get_workspace_connector_preference_by_scope(
    db: Session,
    workspace_id: str,
    *,
    scope_type: str,
    user_id: str | None = None,
    membership_id: str | None = None,
) -> Optional[WorkspaceConnectorPreferenceModel]:
    base = db.query(WorkspaceConnectorPreferenceModel).filter(
        WorkspaceConnectorPreferenceModel.workspace_id == workspace_id,
        WorkspaceConnectorPreferenceModel.scope_type == scope_type,
    )
    if scope_type == "membership":
        if not membership_id:
            return None
        return base.filter(WorkspaceConnectorPreferenceModel.membership_id == membership_id).first()
    if scope_type == "user":
        if not user_id:
            return None
        return base.filter(WorkspaceConnectorPreferenceModel.user_id == user_id).first()
    return base.first()


def get_workspace_connector_preference(
    db: Session,
    workspace_id: str,
    *,
    user_id: str | None = None,
    membership_id: str | None = None,
) -> Optional[WorkspaceConnectorPreferenceModel]:
    try:
        if membership_id:
            row = _get_workspace_connector_preference_by_scope(
                db,
                workspace_id,
                scope_type="membership",
                membership_id=membership_id,
            )
            if row:
                return row
        if user_id:
            row = _get_workspace_connector_preference_by_scope(
                db,
                workspace_id,
                scope_type="user",
                user_id=user_id,
            )
            if row:
                return row
        return _get_workspace_connector_preference_by_scope(db, workspace_id, scope_type="workspace")
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "workspace_connector_preferences")
        if table is None:
            raise
        row = (
            db.execute(
                table.select().where(table.c.workspace_id == workspace_id)
            )
            .mappings()
            .first()
        )
        return _connector_preference_from_mapping(dict(row)) if row else None


def resolve_workspace_connector_preference(
    db: Session,
    workspace_id: str,
    *,
    user_id: str | None = None,
    membership_id: str | None = None,
) -> dict[str, Any]:
    """Resolve connector preference by membership, user, workspace, then Auto."""
    try:
        if membership_id:
            row = _get_workspace_connector_preference_by_scope(
                db,
                workspace_id,
                scope_type="membership",
                membership_id=membership_id,
            )
            if row:
                return {"row": row, "winning_scope": "membership"}
        if user_id:
            row = _get_workspace_connector_preference_by_scope(
                db,
                workspace_id,
                scope_type="user",
                user_id=user_id,
            )
            if row:
                return {"row": row, "winning_scope": "user"}
        row = _get_workspace_connector_preference_by_scope(db, workspace_id, scope_type="workspace")
        if row:
            return {"row": row, "winning_scope": "workspace"}
        return {"row": None, "winning_scope": "auto"}
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        row = get_workspace_connector_preference(db, workspace_id)
        return {"row": row, "winning_scope": "workspace" if row else "auto"}


def upsert_workspace_connector_preference(
    db: Session,
    workspace_id: str,
    *,
    mode: str = "auto",
    selected_toolkit: str = "",
    selected_account_id: str = "",
    selected_account_alias: str = "",
    source: str = "persisted_default",
    selected_by_user_id: str | None = None,
    scope_type: str = "workspace",
    user_id: str | None = None,
    membership_id: str | None = None,
) -> WorkspaceConnectorPreferenceModel:
    try:
        normalized_scope = scope_type if scope_type in {"workspace", "user", "membership"} else "workspace"
        row = _get_workspace_connector_preference_by_scope(
            db,
            workspace_id,
            scope_type=normalized_scope,
            user_id=user_id if normalized_scope == "user" else None,
            membership_id=membership_id if normalized_scope == "membership" else None,
        )
        if not row:
            row = WorkspaceConnectorPreferenceModel(
                id=_id(),
                workspace_id=workspace_id,
                scope_type=normalized_scope,
                user_id=user_id if normalized_scope == "user" else None,
                membership_id=membership_id if normalized_scope == "membership" else None,
                selected_by_user_id=selected_by_user_id,
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
            row.scope_type = normalized_scope
            row.user_id = user_id if normalized_scope == "user" else None
            row.membership_id = membership_id if normalized_scope == "membership" else None
            row.mode = mode
            row.selected_toolkit = selected_toolkit
            row.selected_account_id = selected_account_id
            row.selected_account_alias = selected_account_alias
            row.source = source or row.source
            if selected_by_user_id is not None:
                row.selected_by_user_id = selected_by_user_id
            row.updated_at = utc_now()
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        if _is_missing_column_error(exc):
            _safe_rollback(db)
            table = _get_reflected_table(db, "workspace_connector_preferences")
            if table is None:
                raise
            normalized_scope = scope_type if scope_type in {"workspace", "user", "membership"} else "workspace"
            if "scope_type" not in table.c and normalized_scope != "workspace":
                raise RuntimeError("Scoped connector preferences require migrated scope columns.")
            now = utc_now()
            where_clause = [table.c.workspace_id == workspace_id]
            if "scope_type" in table.c:
                where_clause.append(table.c.scope_type == normalized_scope)
                if normalized_scope == "user":
                    where_clause.append(table.c.user_id == user_id)
                elif normalized_scope == "membership":
                    where_clause.append(table.c.membership_id == membership_id)
            existing = db.execute(
                table.select().where(*where_clause)
            ).mappings().first()
            values = {
                "id": _id(),
                "workspace_id": workspace_id,
                "mode": mode,
                "selected_toolkit": selected_toolkit,
                "selected_account_id": selected_account_id,
                "selected_account_alias": selected_account_alias,
                "source": source or "persisted_default",
                "updated_at": now,
            }
            if "scope_type" in table.c:
                values["scope_type"] = normalized_scope
            if "user_id" in table.c:
                values["user_id"] = user_id if normalized_scope == "user" else None
            if "membership_id" in table.c:
                values["membership_id"] = membership_id if normalized_scope == "membership" else None
            if "selected_by_user_id" in table.c:
                values["selected_by_user_id"] = selected_by_user_id
            if "created_at" in table.c and not existing:
                values["created_at"] = now
            if existing:
                db.execute(
                    table.update()
                    .where(*where_clause)
                    .values(**{key: value for key, value in values.items() if key in table.c})
                )
            else:
                db.execute(
                    table.insert().values(
                        **{key: value for key, value in values.items() if key in table.c}
                    )
                )
            db.commit()
            refreshed = db.execute(
                table.select().where(*where_clause)
            ).mappings().first()
            return _connector_preference_from_mapping(dict(refreshed)) if refreshed else None
        log_exception(
            logger,
            "storage.connector_preference_upsert_failed",
            exc,
            workspace_id=workspace_id,
        )
        raise


# ── Pending Requests / Approval / Idempotency ────────────────────────────────

def get_pending_tool_request_by_resume_token(db: Session, resume_token: str):
    PendingToolRequestModel = _get_pending_tool_request_model_cls()

    try:
        return (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.resume_token == resume_token)
            .first()
        )
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        row = (
            db.execute(
                table.select().where(table.c.resume_token == resume_token)
            )
            .mappings()
            .first()
        )
        return _pending_tool_request_from_mapping(dict(row)) if row else None


def get_pending_tool_request_by_id(db: Session, request_id: str):
    PendingToolRequestModel = _get_pending_tool_request_model_cls()

    try:
        return (
            db.query(PendingToolRequestModel)
            .filter(PendingToolRequestModel.id == request_id)
            .first()
        )
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        row = (
            db.execute(
                table.select().where(table.c.id == request_id)
            )
            .mappings()
            .first()
        )
        return _pending_tool_request_from_mapping(dict(row)) if row else None


def save_pending_tool_request(
    db: Session,
    workspace_id: str,
    *,
    agent_key: str,
    original_input: str,
    requested_tool: str,
    requested_toolkit: str,
    resume_token: str,
    conversation_id: str = "",
    context_json: dict | None = None,
    pending_kind: str = "auth",
    idempotency_key: str = "",
    approval_requirement_json: dict | None = None,
    approved: bool = False,
    expires_at=None,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
):
    PendingToolRequestModel = _get_pending_tool_request_model_cls()

    try:
        row = (
            db.query(PendingToolRequestModel)
            .filter(
                PendingToolRequestModel.workspace_id == workspace_id,
                PendingToolRequestModel.agent_key == agent_key,
                PendingToolRequestModel.original_input == original_input,
                PendingToolRequestModel.requested_tool == requested_tool,
                PendingToolRequestModel.requested_toolkit == requested_toolkit,
                PendingToolRequestModel.pending_kind == pending_kind,
                PendingToolRequestModel.status.in_(["pending", "resumed"]),
            )
            .first()
        )
        if not row:
            row = PendingToolRequestModel(
                id=_id(),
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                membership_id=membership_id,
                conversation_id=conversation_id,
                agent_key=agent_key,
                original_input=original_input,
                requested_tool=requested_tool,
                requested_toolkit=requested_toolkit,
                resume_token=resume_token,
                status="pending",
                pending_kind=pending_kind,
                idempotency_key=idempotency_key or "",
                approval_requirement_json=dict(approval_requirement_json or {}),
                approved=bool(approved),
                approved_at=utc_now() if approved else None,
                expires_at=expires_at or _pending_request_expiry(),
                context_json=dict(context_json or {}),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(row)
        else:
            row.resume_token = resume_token
            if actor_user_id is not None:
                row.actor_user_id = actor_user_id
            if membership_id is not None:
                row.membership_id = membership_id
            row.conversation_id = conversation_id or row.conversation_id
            row.context_json = dict(context_json or {})
            row.pending_kind = pending_kind
            row.idempotency_key = idempotency_key or getattr(row, "idempotency_key", "")
            row.approval_requirement_json = dict(approval_requirement_json or getattr(row, "approval_requirement_json", {}) or {})
            row.approved = bool(approved)
            row.approved_at = utc_now() if approved else getattr(row, "approved_at", None)
            row.expires_at = expires_at or getattr(row, "expires_at", None) or _pending_request_expiry()
            row.updated_at = utc_now()
        db.commit()
        return row
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        existing_row = (
            db.execute(
                table.select().where(
                    table.c.workspace_id == workspace_id,
                    table.c.agent_key == agent_key,
                    table.c.original_input == original_input,
                    table.c.requested_tool == requested_tool,
                    table.c.requested_toolkit == requested_toolkit,
                    table.c.status.in_(["pending", "resumed"]),
                )
            )
            .mappings()
            .first()
        )
        legacy_context = dict(context_json or {})
        legacy_context.setdefault("pending_kind", pending_kind)
        legacy_context.setdefault("idempotency_key", idempotency_key or "")
        legacy_context.setdefault("approval_requirement_json", dict(approval_requirement_json or {}))
        legacy_context.setdefault("approved", bool(approved))
        values = {
            "conversation_id": conversation_id,
            "agent_key": agent_key,
            "original_input": original_input,
            "requested_tool": requested_tool,
            "requested_toolkit": requested_toolkit,
            "resume_token": resume_token,
            "status": "pending",
            "context_json": legacy_context,
            "updated_at": utc_now(),
        }
        if "actor_user_id" in table.c:
            values["actor_user_id"] = actor_user_id
        if "membership_id" in table.c:
            values["membership_id"] = membership_id
        if existing_row:
            db.execute(
                table.update()
                .where(table.c.id == existing_row["id"])
                .values(**{key: value for key, value in values.items() if key in table.c})
            )
            row_id = existing_row["id"]
            created_at = existing_row.get("created_at")
        else:
            row_id = _id()
            created_at = utc_now()
            insert_values = {
                "id": row_id,
                "workspace_id": workspace_id,
                "created_at": created_at,
                **values,
            }
            db.execute(
                table.insert().values(
                    **{key: value for key, value in insert_values.items() if key in table.c}
                )
            )
        db.commit()
        return _pending_tool_request_from_mapping(
            {
                "id": row_id,
                "workspace_id": workspace_id,
                "actor_user_id": actor_user_id,
                "membership_id": membership_id,
                "conversation_id": conversation_id,
                "agent_key": agent_key,
                "original_input": original_input,
                "requested_tool": requested_tool,
                "requested_toolkit": requested_toolkit,
                "resume_token": resume_token,
                "status": "pending",
                "pending_kind": pending_kind,
                "idempotency_key": idempotency_key or "",
                "approval_requirement_json": dict(approval_requirement_json or {}),
                "approved": bool(approved),
                "approved_at": utc_now() if approved else None,
                "expires_at": expires_at or _pending_request_expiry(),
                "context_json": legacy_context,
                "created_at": created_at,
                "updated_at": values["updated_at"],
            }
        )


def transition_pending_tool_request(
    db: Session,
    resume_token: str,
    *,
    to_status: str,
    allowed_statuses: tuple[str, ...] = ("pending", "resumed"),
    require_approved: bool = False,
    context_updates: dict | None = None,
):
    row = get_pending_tool_request_by_resume_token(db, resume_token)
    if not row:
        return None
    if allowed_statuses and getattr(row, "status", "") not in allowed_statuses:
        return row
    if require_approved and not getattr(row, "approved", False):
        return row
    try:
        row.status = to_status
        if context_updates:
            merged = dict(getattr(row, "context_json", {}) or {})
            merged.update(dict(context_updates or {}))
            row.context_json = merged
        row.updated_at = utc_now()
        db.commit()
        return row
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        update_values = {"status": to_status, "updated_at": utc_now()}
        if context_updates:
            merged = dict(getattr(row, "context_json", {}) or {})
            merged.update(dict(context_updates or {}))
            update_values["context_json"] = merged
        db.execute(
            table.update()
            .where(table.c.resume_token == resume_token)
            .values(**{key: value for key, value in update_values.items() if key in table.c})
        )
        db.commit()
        return _pending_tool_request_from_mapping({**row.__dict__, **update_values})


def approve_pending_tool_request(db: Session, resume_token: str):
    row = get_pending_tool_request_by_resume_token(db, resume_token)
    if not row:
        return None
    try:
        row.approved = True
        row.approved_at = utc_now()
        context_json = dict(getattr(row, "context_json", {}) or {})
        context_json["approval_granted"] = True
        granted = list(context_json.get("approved_idempotency_keys", []) or [])
        idempotency_key = str(getattr(row, "idempotency_key", "") or "")
        if idempotency_key and idempotency_key not in granted:
            granted.append(idempotency_key)
        context_json["approved_idempotency_keys"] = granted
        row.context_json = context_json
        row.updated_at = utc_now()
        db.commit()
        return row
    except Exception as exc:
        if not _is_missing_column_error(exc):
            raise
        _safe_rollback(db)
        table = _get_reflected_table(db, "pending_tool_requests")
        if table is None:
            raise
        context_json = dict(getattr(row, "context_json", {}) or {})
        context_json["approval_granted"] = True
        granted = list(context_json.get("approved_idempotency_keys", []) or [])
        idempotency_key = str(getattr(row, "idempotency_key", "") or context_json.get("idempotency_key", "") or "")
        if idempotency_key and idempotency_key not in granted:
            granted.append(idempotency_key)
        context_json["approved_idempotency_keys"] = granted
        update_values = {"context_json": context_json, "updated_at": utc_now()}
        if "approved" in table.c:
            update_values["approved"] = True
        if "approved_at" in table.c:
            update_values["approved_at"] = utc_now()
        db.execute(
            table.update()
            .where(table.c.resume_token == resume_token)
            .values(**{key: value for key, value in update_values.items() if key in table.c})
        )
        db.commit()
        return _pending_tool_request_from_mapping({**row.__dict__, **update_values, "approved": True})


def get_tool_idempotency_record(
    db: Session,
    workspace_id: str,
    tool_name: str,
    idempotency_key: str,
):
    ToolIdempotencyRecordModel = _get_tool_idempotency_model_cls()

    if not idempotency_key:
        return None
    return (
        db.query(ToolIdempotencyRecordModel)
        .filter(
            ToolIdempotencyRecordModel.workspace_id == workspace_id,
            ToolIdempotencyRecordModel.tool_name == tool_name,
            ToolIdempotencyRecordModel.idempotency_key == idempotency_key,
        )
        .first()
    )


def claim_tool_idempotency_record(
    db: Session,
    workspace_id: str,
    tool_name: str,
    idempotency_key: str,
    *,
    input_hash: str = "",
    status: str = "pending",
    actor_user_id: str | None = None,
    membership_id: str | None = None,
):
    ToolIdempotencyRecordModel = _get_tool_idempotency_model_cls()

    existing = get_tool_idempotency_record(db, workspace_id, tool_name, idempotency_key)
    if existing:
        return existing

    row = ToolIdempotencyRecordModel(
        id=_id(),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        tool_name=tool_name,
        idempotency_key=idempotency_key,
        input_hash=input_hash,
        status=status,
        output_json={},
        error_message="",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    try:
        db.commit()
        return row
    except Exception as exc:
        if not _is_integrity_error(exc):
            raise
        try:
            db.rollback()
        except Exception:
            pass
        return get_tool_idempotency_record(db, workspace_id, tool_name, idempotency_key)


def update_tool_idempotency_record(
    db: Session,
    workspace_id: str,
    tool_name: str,
    idempotency_key: str,
    *,
    input_hash: str = "",
    status: str = "",
    pending_request_id: str = "",
    tool_call_log_id: str = "",
    output_json: dict | None = None,
    error_message: str = "",
    completed: bool = False,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
):
    row = get_tool_idempotency_record(db, workspace_id, tool_name, idempotency_key)
    if not row:
        row = claim_tool_idempotency_record(
            db,
            workspace_id,
            tool_name,
            idempotency_key,
            input_hash=input_hash,
            status=status or "pending",
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        )
    if not row:
        return None
    if input_hash:
        row.input_hash = input_hash
    if actor_user_id is not None:
        row.actor_user_id = actor_user_id
    if membership_id is not None:
        row.membership_id = membership_id
    if status:
        row.status = status
    if pending_request_id:
        row.pending_request_id = pending_request_id
    if tool_call_log_id:
        row.tool_call_log_id = tool_call_log_id
    if output_json is not None:
        row.output_json = dict(output_json or {})
    if error_message:
        row.error_message = error_message
    row.updated_at = utc_now()
    if completed:
        row.completed_at = utc_now()
    db.commit()
    return row


# ── Scheduled Automations ────────────────────────────────────────────────────

def create_scheduled_task(
    db: Session,
    *,
    workspace_id: str,
    target_kind: str,
    target_name: str,
    schedule_type: str,
    timezone: str = "UTC",
    start_at=None,
    end_at=None,
    next_run_at=None,
    cron_expression: str = "",
    interval_seconds: int = 0,
    payload_json: dict | None = None,
    connector_context_json: dict | None = None,
    execution_policy_json: dict | None = None,
    retry_policy_json: dict | None = None,
    metadata_json: dict | None = None,
    actor_user_id: str | None = None,
    created_by_user_id: str | None = None,
    membership_id: str | None = None,
    task_kind: str = "automation",
    status: str = "active",
):
    ScheduledTaskModel = _get_scheduled_task_model_cls()
    row = ScheduledTaskModel(
        id=_id(),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        created_by_user_id=created_by_user_id or actor_user_id,
        membership_id=membership_id,
        task_kind=task_kind or "automation",
        target_kind=target_kind,
        target_name=target_name,
        status=status or "active",
        schedule_type=schedule_type,
        cron_expression=cron_expression or "",
        interval_seconds=int(interval_seconds or 0),
        timezone=timezone or "UTC",
        start_at=start_at,
        end_at=end_at,
        next_run_at=next_run_at,
        last_run_at=None,
        connector_context_json=dict(connector_context_json or {}),
        payload_json=dict(payload_json or {}),
        execution_policy_json=dict(execution_policy_json or {}),
        retry_policy_json=dict(retry_policy_json or {}),
        metadata_json=dict(metadata_json or {}),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_scheduled_task(db: Session, task_id: str):
    ScheduledTaskModel = _get_scheduled_task_model_cls()
    if not task_id:
        return None
    return db.query(ScheduledTaskModel).filter(ScheduledTaskModel.id == task_id).first()


def list_scheduled_tasks(
    db: Session,
    workspace_id: str,
    *,
    status: str = "",
    limit: int = 50,
):
    ScheduledTaskModel = _get_scheduled_task_model_cls()
    query = db.query(ScheduledTaskModel).filter(ScheduledTaskModel.workspace_id == workspace_id)
    if status:
        query = query.filter(ScheduledTaskModel.status == status)
    return query.order_by(ScheduledTaskModel.created_at.desc()).limit(limit).all()


def update_scheduled_task(
    db: Session,
    task_id: str,
    *,
    status: str | None = None,
    target_kind: str | None = None,
    target_name: str | None = None,
    schedule_type: str | None = None,
    timezone: str | None = None,
    start_at=None,
    end_at=None,
    cron_expression: str | None = None,
    interval_seconds: int | None = None,
    next_run_at=None,
    last_run_at=None,
    payload_json: dict | None = None,
    connector_context_json: dict | None = None,
    execution_policy_json: dict | None = None,
    retry_policy_json: dict | None = None,
    metadata_json: dict | None = None,
):
    row = get_scheduled_task(db, task_id)
    if not row:
        return None
    if status is not None:
        row.status = status
    if target_kind is not None:
        row.target_kind = target_kind
    if target_name is not None:
        row.target_name = target_name
    if schedule_type is not None:
        row.schedule_type = schedule_type
    if timezone is not None:
        row.timezone = timezone or "UTC"
    if start_at is not None:
        row.start_at = start_at
    if end_at is not None:
        row.end_at = end_at
    if cron_expression is not None:
        row.cron_expression = cron_expression or ""
    if interval_seconds is not None:
        row.interval_seconds = int(interval_seconds or 0)
    if next_run_at is not None:
        row.next_run_at = next_run_at
    if last_run_at is not None:
        row.last_run_at = last_run_at
    if payload_json is not None:
        row.payload_json = dict(payload_json or {})
    if connector_context_json is not None:
        row.connector_context_json = dict(connector_context_json or {})
    if execution_policy_json is not None:
        row.execution_policy_json = dict(execution_policy_json or {})
    if retry_policy_json is not None:
        row.retry_policy_json = dict(retry_policy_json or {})
    if metadata_json is not None:
        row.metadata_json = dict(metadata_json or {})
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row


def set_scheduled_task_status(db: Session, task_id: str, status: str):
    return update_scheduled_task(db, task_id, status=status)


def list_due_scheduled_tasks(db: Session, due_at, *, limit: int = 20):
    ScheduledTaskModel = _get_scheduled_task_model_cls()
    return (
        db.query(ScheduledTaskModel)
        .filter(
            ScheduledTaskModel.status == "active",
            ScheduledTaskModel.next_run_at.isnot(None),
            ScheduledTaskModel.next_run_at <= due_at,
        )
        .order_by(ScheduledTaskModel.next_run_at.asc(), ScheduledTaskModel.created_at.asc())
        .limit(limit)
        .all()
    )


def create_scheduled_task_run(
    db: Session,
    *,
    scheduled_task_id: str,
    workspace_id: str,
    planned_for,
    run_key: str,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
    idempotency_key: str = "",
    request_id: str = "",
    attempt_number: int = 1,
    status: str = "queued",
):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    existing = get_scheduled_task_run_by_key(db, run_key)
    if existing:
        return existing
    row = ScheduledTaskRunModel(
        id=_id(),
        scheduled_task_id=scheduled_task_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        run_key=run_key,
        status=status,
        planned_for=planned_for,
        request_id=request_id or "",
        idempotency_key=idempotency_key or "",
        attempt_number=max(1, int(attempt_number or 1)),
        result_json={},
        error_message="",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except Exception as exc:
        if not _is_integrity_error(exc):
            raise
        _safe_rollback(db)
        return get_scheduled_task_run_by_key(db, run_key)


def get_scheduled_task_run(db: Session, run_id: str):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    if not run_id:
        return None
    return db.query(ScheduledTaskRunModel).filter(ScheduledTaskRunModel.id == run_id).first()


def get_scheduled_task_run_by_key(db: Session, run_key: str):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    if not run_key:
        return None
    return db.query(ScheduledTaskRunModel).filter(ScheduledTaskRunModel.run_key == run_key).first()


def get_scheduled_task_run_by_resume_token(db: Session, resume_token: str):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    if not resume_token:
        return None
    return (
        db.query(ScheduledTaskRunModel)
        .filter(ScheduledTaskRunModel.resume_token == resume_token)
        .first()
    )


def list_scheduled_task_runs(
    db: Session,
    *,
    workspace_id: str = "",
    scheduled_task_id: str = "",
    status: str = "",
    limit: int = 50,
):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    query = db.query(ScheduledTaskRunModel)
    if workspace_id:
        query = query.filter(ScheduledTaskRunModel.workspace_id == workspace_id)
    if scheduled_task_id:
        query = query.filter(ScheduledTaskRunModel.scheduled_task_id == scheduled_task_id)
    if status:
        query = query.filter(ScheduledTaskRunModel.status == status)
    return query.order_by(ScheduledTaskRunModel.created_at.desc()).limit(limit).all()


def list_queued_scheduled_task_runs(db: Session, *, due_at=None, limit: int = 20):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    query = db.query(ScheduledTaskRunModel).filter(ScheduledTaskRunModel.status == "queued")
    if due_at is not None:
        query = query.filter(ScheduledTaskRunModel.planned_for <= due_at)
    return query.order_by(ScheduledTaskRunModel.planned_for.asc(), ScheduledTaskRunModel.created_at.asc()).limit(limit).all()


def claim_scheduled_task_run(db: Session, run_id: str):
    ScheduledTaskRunModel = _get_scheduled_task_run_model_cls()
    if not run_id:
        return None
    updated = (
        db.query(ScheduledTaskRunModel)
        .filter(
            ScheduledTaskRunModel.id == run_id,
            ScheduledTaskRunModel.status == "queued",
        )
        .update(
            {
                "status": "running",
                "started_at": utc_now(),
                "updated_at": utc_now(),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if not updated:
        return get_scheduled_task_run(db, run_id)
    return get_scheduled_task_run(db, run_id)


def update_scheduled_task_run(
    db: Session,
    run_id: str,
    *,
    status: str | None = None,
    result_json: dict | None = None,
    error_message: str | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    resume_token: str | None = None,
    finished_at=None,
):
    row = get_scheduled_task_run(db, run_id)
    if not row:
        return None
    if status is not None:
        row.status = status
    if result_json is not None:
        row.result_json = dict(result_json or {})
    if error_message is not None:
        row.error_message = error_message
    if request_id is not None:
        row.request_id = request_id
    if idempotency_key is not None:
        row.idempotency_key = idempotency_key
    if resume_token is not None:
        row.resume_token = resume_token
    if finished_at is not None:
        row.finished_at = finished_at
    row.updated_at = utc_now()
    db.commit()
    db.refresh(row)
    return row
