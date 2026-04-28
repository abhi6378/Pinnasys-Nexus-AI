"""
api/routes.py  —  FastAPI REST API
Run with: uvicorn api.routes:app --reload
"""
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from auth.service import (
    AuthServiceError,
    clear_session_cookie,
    extract_session_token,
    get_current_user_from_request,
    hash_session_token,
    is_auth_required,
    require_workspace_access,
    set_session_cookie,
    sign_in_with_google_payload,
    validate_google_csrf,
    verify_google_credential,
)
from storage.db import init_db, get_db, SessionLocal
from storage import repositories as repo
from automation import service as automation_service
from workspace.manager import create_workspace, list_workspaces, get_workspace_context
from brain.quiz_engine import get_next_question, save_answer, quiz_progress
from orchestrator.handler import handle_request
from helpers.configs import list_agents
from models.contracts import RuntimeActorContext
from tools.connector_service import (
    list_connector_accounts,
    list_workspace_connectors,
    normalize_connector_context,
    persist_connector_context,
    refresh_connector_status,
    resolve_persisted_connector_preference,
)
from tools.composio_client import get_connect_link
from tools.tool_registry import get_toolkit_metadata, normalize_toolkit_key
from utils.logging_utils import configure_logging
from utils.perf import timed_log
from utils.runtime_config import parse_allowed_origins, validate_production_config

app = FastAPI(title="Nexus Ai API", version="1.0.0")
configure_logging()
logger = logging.getLogger(__name__)

allowed_origins = parse_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    validate_production_config(allowed_origins=allowed_origins)
    init_db()


# ── Schemas ──────────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str

class GoogleAuthRequest(BaseModel):
    credential: str
    g_csrf_token: str = ""

class UserSummary(BaseModel):
    id: str
    email: str = ""
    display_name: str = ""
    avatar_url: str = ""

class WorkspaceSummary(BaseModel):
    id: str
    name: str
    created_at: str = ""
    membership_id: str = ""
    membership_role: str = ""

class AuthStateResponse(BaseModel):
    authenticated: bool
    user: Optional[UserSummary] = None
    workspaces: list[WorkspaceSummary] = []
    default_workspace: Optional[WorkspaceSummary] = None

class AuthLoginResponse(AuthStateResponse):
    access_token: str = ""
    token_type: str = "bearer"

class ConnectorContextRequest(BaseModel):
    mode: str = "auto"
    selected_toolkit: str = ""
    selected_connector_key: str = ""
    selected_account_id: str = ""
    selected_account_alias: str = ""
    enforce_toolkit: bool = False
    enforce_account: bool = False
    source: str = "api"

    def to_connector_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selected_toolkit": self.selected_toolkit,
            "selected_connector_key": self.selected_connector_key,
            "selected_account_id": self.selected_account_id,
            "selected_account_alias": self.selected_account_alias,
            "enforce_toolkit": self.enforce_toolkit,
            "enforce_account": self.enforce_account,
            "source": self.source,
        }

class ConnectorPreferenceUpdateRequest(BaseModel):
    connector_context: ConnectorContextRequest = Field(default_factory=ConnectorContextRequest)

class ConnectorPreferenceResponse(BaseModel):
    workspace_id: str
    scope_type: str = "auto"
    winning_scope: str = "auto"
    selected_by_user_id: str = ""
    connector_context: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = ""

class ConnectorAccountResponse(BaseModel):
    toolkit: str = ""
    connected_account_id: str = ""
    account_alias: str = ""
    display_label: str = ""
    status: str = ""
    is_default: bool = False
    is_selected: bool = False
    source: str = ""
    last_verified_at: str = ""
    stale: bool = False

class ConnectorStatusResponse(BaseModel):
    toolkit: str = ""
    connector_key: str = ""
    label: str = ""
    slug: str = ""
    connected: bool = False
    status: str = ""
    source: str = ""
    validation_status: str = ""
    status_reason: str = ""
    stale: bool = False
    stale_selection: bool = False
    account_required: bool = False
    account_count: int = 0
    selected_account_id: str = ""
    selected_account_alias: str = ""
    effective_account_id: str = ""
    effective_account_alias: str = ""
    connect_url: Optional[str] = None
    setup_message: str = ""
    connection_mode: str = ""
    auth_mode: str = ""
    last_verified_at: str = ""
    remote_attempted: bool = False
    accounts: list[ConnectorAccountResponse] = Field(default_factory=list)

class ChatRequest(BaseModel):
    workspace_id: str
    message: str
    agent: Optional[str] = None
    connector_context: Optional[ConnectorContextRequest] = None

class ResumeRequest(BaseModel):
    resume_token: str
    workspace_id: str

class ApproveRequest(BaseModel):
    resume_token: str
    workspace_id: str

class KnowledgeRequest(BaseModel):
    workspace_id: str
    title: str
    content: str
    type: str = "text"
    tags: list = []

class QuizAnswerRequest(BaseModel):
    workspace_id: str
    field: str
    question: str
    answer: str

class IdeaStatusRequest(BaseModel):
    status: str

class AutomationScheduleRequest(BaseModel):
    schedule_type: str = "once"
    timezone: str = "UTC"
    start_at: str = ""
    end_at: str = ""
    cron_expression: str = ""
    interval_seconds: int = 0

class AutomationPayloadRequest(BaseModel):
    target_kind: str = "workflow"
    target_name: str = ""
    user_input: str
    force_agent: str = ""
    force_workflow: str = ""
    payload_json: dict[str, Any] = Field(default_factory=dict)

class AutomationPolicyRequest(BaseModel):
    approval_policy: str = "per_run"
    allow_write_actions: bool = True
    idempotency_scope: str = "scheduled_run"

class AutomationRetryPolicyRequest(BaseModel):
    max_attempts: int = 1
    backoff_seconds: int = 300

class AutomationCreateRequest(BaseModel):
    schedule: AutomationScheduleRequest
    payload: AutomationPayloadRequest
    connector_context: Optional[ConnectorContextRequest] = None
    retry_policy: AutomationRetryPolicyRequest = Field(default_factory=AutomationRetryPolicyRequest)
    execution_policy: AutomationPolicyRequest = Field(default_factory=AutomationPolicyRequest)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

class AutomationUpdateRequest(BaseModel):
    schedule: Optional[AutomationScheduleRequest] = None
    payload: Optional[AutomationPayloadRequest] = None
    connector_context: Optional[ConnectorContextRequest] = None
    retry_policy: Optional[AutomationRetryPolicyRequest] = None
    execution_policy: Optional[AutomationPolicyRequest] = None
    metadata_json: Optional[dict[str, Any]] = None


@dataclass
class RequestActor:
    user: Any | None = None
    membership: Any | None = None
    workspace_id: str = ""
    auth_required: bool = False

    @property
    def actor_user_id(self) -> str | None:
        return getattr(self.user, "id", None) if self.user else None

    @property
    def membership_id(self) -> str | None:
        return getattr(self.membership, "id", None) if self.membership else None

    @property
    def membership_role(self) -> str:
        return str(getattr(self.membership, "role", "") or "")

    def to_runtime_context(self) -> RuntimeActorContext:
        return RuntimeActorContext(
            workspace_id=self.workspace_id,
            actor_user_id=self.actor_user_id or "",
            membership_id=self.membership_id or "",
            auth_required=bool(self.auth_required),
            membership_role=self.membership_role,
        )


def _current_user(request: Request, db: Session):
    return get_current_user_from_request(db, request)


def _require_workspace_if_needed(db: Session, workspace_id: str, user) -> None:
    try:
        require_workspace_access(db, workspace_id, user)
    except AuthServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _translate_auth_error(exc: AuthServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _actor_for_workspace(request: Request, db: Session, workspace_id: str) -> RequestActor:
    user = _current_user(request, db)
    _require_workspace_if_needed(db, workspace_id, user)
    membership = repo.get_workspace_membership(db, workspace_id, user.id) if user else None
    return RequestActor(
        user=user,
        membership=membership,
        workspace_id=workspace_id,
        auth_required=is_auth_required(),
    )


def _normalize_connector_payload(value: ConnectorContextRequest | None) -> dict | None:
    if value is None:
        return None
    mode = (value.mode or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise HTTPException(status_code=422, detail="connector_context.mode must be 'auto' or 'manual'.")
    payload = value.to_connector_dict()
    payload["mode"] = mode
    connector = normalize_connector_context(payload)
    toolkit_candidate = connector.selected_toolkit or connector.selected_connector_key
    if mode == "manual" and not toolkit_candidate:
        raise HTTPException(status_code=422, detail="Manual connector mode requires selected_toolkit.")
    if toolkit_candidate:
        toolkit = normalize_toolkit_key(toolkit_candidate)
        if not toolkit or not get_toolkit_metadata(toolkit):
            raise HTTPException(status_code=422, detail=f"Unsupported connector toolkit: {toolkit_candidate}.")
        connector.selected_toolkit = toolkit
        connector.selected_connector_key = toolkit
    return connector.to_dict()


def _workspace_payload(ws, membership=None) -> dict:
    return {
        "id": ws.id,
        "name": ws.name,
        "created_at": str(getattr(ws, "created_at", "") or ""),
        "membership_id": str(getattr(membership, "id", "") or ""),
        "membership_role": str(getattr(membership, "role", "") or ""),
    }


def _automation_task_for_workspace(db: Session, workspace_id: str, task_id: str):
    task = repo.get_scheduled_task(db, task_id)
    if not task or getattr(task, "workspace_id", "") != workspace_id:
        raise HTTPException(status_code=404, detail="Automation not found.")
    return task


def _model_dict(value: BaseModel | None) -> dict | None:
    if value is None:
        return None
    return value.model_dump()


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/auth/google", response_model=AuthLoginResponse)
def api_auth_google(req: GoogleAuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    with timed_log(logger, "api.auth_google"):
        try:
            validate_google_csrf(request, req.g_csrf_token)
            payload = verify_google_credential(req.credential)
        except AuthServiceError as exc:
            raise _translate_auth_error(exc) from exc
        user, workspace, session_token = sign_in_with_google_payload(db, payload)
        set_session_cookie(response, session_token)
        memberships = repo.list_user_memberships(db, user.id)
        workspace_pairs = [
            (repo.get_workspace(db, membership.workspace_id), membership)
            for membership in memberships
        ]
        workspace_pairs = [(row, membership) for row, membership in workspace_pairs if row]
        default_membership = next(
            (membership for row, membership in workspace_pairs if row.id == workspace.id),
            workspace_pairs[0][1] if workspace_pairs else None,
        )
        return {
            "authenticated": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "display_name": user.display_name,
                "avatar_url": user.avatar_url,
            },
            "default_workspace": _workspace_payload(workspace, default_membership),
            "workspaces": [_workspace_payload(row, membership) for row, membership in workspace_pairs],
            "access_token": session_token,
            "token_type": "bearer",
        }


@app.get("/auth/me", response_model=AuthStateResponse)
def api_auth_me(request: Request, db: Session = Depends(get_db)):
    with timed_log(logger, "api.auth_me"):
        user = _current_user(request, db)
        if not user:
            if is_auth_required():
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
            return {"authenticated": False, "user": None, "workspaces": []}
        memberships = repo.list_user_memberships(db, user.id)
        workspace_pairs = [
            (repo.get_workspace(db, membership.workspace_id), membership)
            for membership in memberships
        ]
        workspace_pairs = [(workspace, membership) for workspace, membership in workspace_pairs if workspace]
        return {
            "authenticated": True,
            "user": user.to_dict(),
            "workspaces": [_workspace_payload(workspace, membership) for workspace, membership in workspace_pairs],
            "default_workspace": _workspace_payload(workspace_pairs[0][0], workspace_pairs[0][1]) if workspace_pairs else None,
        }


@app.post("/auth/logout")
def api_auth_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = extract_session_token(request)
    if token:
        repo.revoke_auth_session(db, hash_session_token(token))
    clear_session_cookie(response)
    return {"authenticated": False}


# ── Workspace ─────────────────────────────────────────────────────────────────

@app.post("/workspace/create")
def api_create_workspace(req: CreateWorkspaceRequest, request: Request, db: Session = Depends(get_db)):
    user = _current_user(request, db)
    if is_auth_required() and not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    ws = create_workspace(req.name, db, owner_user_id=user.id if user else None)
    return {"id": ws.id, "name": ws.name}


@app.get("/workspaces")
def api_list_workspaces(request: Request, db: Session = Depends(get_db)):
    user = _current_user(request, db)
    if user:
        memberships = repo.list_user_memberships(db, user.id)
        payloads = []
        for membership in memberships:
            workspace = repo.get_workspace(db, membership.workspace_id)
            if workspace:
                payloads.append(_workspace_payload(workspace, membership))
        return payloads
    if is_auth_required():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return list_workspaces(db)


@app.get("/workspace/{workspace_id}")
def api_get_workspace(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    ctx = get_workspace_context(workspace_id, db)
    if not ctx:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ctx


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat")
def api_chat(req: ChatRequest, request: Request, db: Session = Depends(get_db)):
    with timed_log(logger, "api.chat", workspace_id=req.workspace_id):
        actor = _actor_for_workspace(request, db, req.workspace_id)
        connector_context = _normalize_connector_payload(req.connector_context)
        result = handle_request(
            req.message,
            req.workspace_id,
            db,
            force_agent=req.agent,
            connector_context=connector_context,
            actor_user_id=actor.actor_user_id,
            membership_id=actor.membership_id,
        )
        return result


@app.post("/chat/resume")
def api_chat_resume(req: ResumeRequest, request: Request, db: Session = Depends(get_db)):
    """
    Resume a request that was paused because a tool needed authentication.

    The client passes the resume_token that was returned in the original
    connect_required response.  We look up the pending request, extract
    the original user message, and re-send it through handle_request().
    """
    with timed_log(logger, "api.chat_resume", workspace_id=req.workspace_id):
        from tools.tool_executor import (
            get_pending_request,
            mark_request_resumed,
            mark_request_completed,
        )

        actor = _actor_for_workspace(request, db, req.workspace_id)
        pending = get_pending_request(db, req.resume_token)
        if not pending:
            raise HTTPException(
                status_code=404,
                detail="Resume token not found or already used.",
            )

        # Mark it as resumed so it can't be replayed
        mark_request_resumed(db, req.resume_token)

        # Invalidate stale Composio session so fresh connection state is picked up
        from tools.composio_client import invalidate_session
        invalidate_session(req.workspace_id)
        if getattr(pending, "requested_toolkit", ""):
            refresh_connector_status(
                req.workspace_id,
                pending.requested_toolkit,
                db,
                request_cache={},
            )

        # Determine if this was a workflow resume
        context = dict(pending.context_json or {})
        if actor.actor_user_id:
            context.setdefault("actor_user_id", actor.actor_user_id)
        if actor.membership_id:
            context.setdefault("membership_id", actor.membership_id)
        wf_key = context.get("workflow_key")
        connector_context = context.get("connector_context")

        # Re-send the original request through the orchestrator
        result = handle_request(
            pending.original_input,
            req.workspace_id,
            db,
            force_agent=pending.agent_key if (pending.agent_key and not wf_key) else None,
            force_workflow=wf_key,
            resume_state=context,
            connector_context=connector_context,
            actor_user_id=actor.actor_user_id,
            membership_id=actor.membership_id,
        )
        automation_service.complete_run_from_resume(db, context.get("scheduled_run_id"), result)
        if result.get("mode") not in {
            "connect_required", "auth_unavailable", "invalid_tool",
            "validation_error", "tool_error"
        } and not result.get("error", False):
            mark_request_completed(db, req.resume_token)
        return result


@app.post("/chat/approve")
def api_chat_approve(req: ApproveRequest, request: Request, db: Session = Depends(get_db)):
    with timed_log(logger, "api.chat_approve", workspace_id=req.workspace_id):
        from tools.tool_executor import (
            get_pending_request,
            mark_request_approved,
            mark_request_resumed,
            mark_request_completed,
        )

        actor = _actor_for_workspace(request, db, req.workspace_id)
        pending = get_pending_request(db, req.resume_token)
        if not pending:
            raise HTTPException(
                status_code=404,
                detail="Resume token not found or already used.",
            )

        mark_request_approved(db, req.resume_token)
        mark_request_resumed(db, req.resume_token)

        context = dict(getattr(pending, "context_json", {}) or {})
        if actor.actor_user_id:
            context.setdefault("actor_user_id", actor.actor_user_id)
        if actor.membership_id:
            context.setdefault("membership_id", actor.membership_id)
        context["approval_granted"] = True
        approved_keys = list(context.get("approved_idempotency_keys", []) or [])
        if getattr(pending, "idempotency_key", "") and pending.idempotency_key not in approved_keys:
            approved_keys.append(pending.idempotency_key)
        context["approved_idempotency_keys"] = approved_keys
        workflow_key = context.get("workflow_key")
        connector_context = context.get("connector_context")

        result = handle_request(
            pending.original_input,
            req.workspace_id,
            db,
            force_agent=pending.agent_key if (pending.agent_key and not workflow_key) else None,
            force_workflow=workflow_key,
            resume_state=context,
            connector_context=connector_context,
            actor_user_id=actor.actor_user_id,
            membership_id=actor.membership_id,
        )
        automation_service.complete_run_from_resume(db, context.get("scheduled_run_id"), result)
        if result.get("mode") not in {
            "connect_required", "auth_unavailable", "invalid_tool",
            "validation_error", "tool_error"
        } and not result.get("error", False):
            mark_request_completed(db, req.resume_token)
        return result


# ── Brain AI ─────────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/brain")
def api_get_brain(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    brain = repo.get_brain(db, workspace_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Brain not found")
    return {
        "company_name":  brain.company_name,
        "brand_context": brain.brand_context,
        "tone":          brain.tone,
        "audience":      brain.audience,
        "goals":         brain.goals,
        "services":      brain.services,
        "pricing":       brain.pricing,
        "competitors":   brain.competitors,
        "support_style": brain.support_style,
    }


@app.post("/brain/add-knowledge")
def api_add_knowledge(req: KnowledgeRequest, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, req.workspace_id)
    item = repo.add_knowledge(
        db, req.workspace_id, req.type, req.title, req.content, req.tags
    )
    return {"id": item.id, "title": item.title}


@app.get("/workspace/{workspace_id}/knowledge")
def api_list_knowledge(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    items = repo.list_all_knowledge(db, workspace_id)
    return [{"id": i.id, "type": i.type, "title": i.title,
             "content": i.content[:200], "tags": i.tags} for i in items]


@app.delete("/knowledge/{item_id}")
def api_delete_knowledge(item_id: str, db: Session = Depends(get_db)):
    repo.delete_knowledge(db, item_id)
    return {"deleted": item_id}


# ── Quiz ─────────────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/quiz/next")
def api_quiz_next(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    question = get_next_question(workspace_id, db)
    progress = quiz_progress(workspace_id, db)
    return {"question": question, "progress": progress}


@app.post("/quiz/answer")
def api_quiz_answer(req: QuizAnswerRequest, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, req.workspace_id)
    save_answer(req.workspace_id, req.field, req.question, req.answer, db)
    return {"saved": True}


# ── Helpers ──────────────────────────────────────────────────────────────────

@app.get("/helpers")
def api_list_helpers():
    return list_agents()


@app.get("/workspace/{workspace_id}/connectors", response_model=list[ConnectorStatusResponse])
def api_list_connectors(workspace_id: str, request: Request, refresh: bool = False, selected_toolkit: str = "", db: Session = Depends(get_db)):
    with timed_log(logger, "api.connectors.list", workspace_id=workspace_id, refresh=refresh, selected_toolkit=selected_toolkit):
        _actor_for_workspace(request, db, workspace_id)
        return list_workspace_connectors(
            workspace_id,
            db,
            request_cache={},
            refresh=refresh,
            selected_toolkit=selected_toolkit,
            include_connect_url=bool(selected_toolkit),
        )


@app.get("/workspace/{workspace_id}/connector-preference", response_model=ConnectorPreferenceResponse)
def api_get_connector_preference(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    actor = _actor_for_workspace(request, db, workspace_id)
    resolved = resolve_persisted_connector_preference(
        workspace_id,
        db,
        user_id=actor.actor_user_id,
        membership_id=actor.membership_id,
    )
    return resolved


@app.put("/workspace/{workspace_id}/connector-preference", response_model=ConnectorPreferenceResponse)
def api_put_connector_preference(
    workspace_id: str,
    req: ConnectorPreferenceUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = _actor_for_workspace(request, db, workspace_id)
    scope_type = "membership" if actor.membership_id else ("user" if actor.actor_user_id else "workspace")
    connector_context = _normalize_connector_payload(req.connector_context) or {}
    persist_connector_context(
        workspace_id,
        connector_context,
        db,
        scope_type=scope_type,
        user_id=actor.actor_user_id,
        membership_id=actor.membership_id,
        selected_by_user_id=actor.actor_user_id,
    )
    return resolve_persisted_connector_preference(
        workspace_id,
        db,
        user_id=actor.actor_user_id,
        membership_id=actor.membership_id,
    )


@app.get("/workspace/{workspace_id}/connectors/{toolkit}/accounts", response_model=list[ConnectorAccountResponse])
def api_list_connector_accounts(workspace_id: str, toolkit: str, request: Request, refresh: bool = False, db: Session = Depends(get_db)):
    with timed_log(logger, "api.connectors.accounts", workspace_id=workspace_id, toolkit=toolkit, refresh=refresh):
        _actor_for_workspace(request, db, workspace_id)
        return list_connector_accounts(
            workspace_id,
            toolkit,
            db,
            request_cache={},
            include_disconnected=True,
            refresh=refresh,
            allow_remote=True,
        )


@app.get("/workspace/{workspace_id}/connectors/{toolkit}/connect-link")
def api_get_connector_link(workspace_id: str, toolkit: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    return {"connect_url": get_connect_link(workspace_id, toolkit)}


@app.post("/workspace/{workspace_id}/connectors/{toolkit}/refresh", response_model=ConnectorStatusResponse)
def api_refresh_connector(workspace_id: str, toolkit: str, request: Request, db: Session = Depends(get_db)):
    with timed_log(logger, "api.connectors.refresh", workspace_id=workspace_id, toolkit=toolkit):
        _actor_for_workspace(request, db, workspace_id)
        summary = refresh_connector_status(workspace_id, toolkit, db, request_cache={})
        return summary.to_dict()


# ── Ideas Inbox ───────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/ideas")
def api_get_ideas(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    ideas = repo.get_ideas(db, workspace_id)
    return [{"id": i.id, "title": i.title, "description": i.description,
             "source_agent": i.source_agent, "status": i.status,
             "workflow_hint": i.workflow_hint,
             "created_at": str(i.created_at)} for i in ideas]


@app.post("/ideas/{idea_id}/accept")
def api_accept_idea(idea_id: str, request: Request, db: Session = Depends(get_db)):
    idea = repo.update_idea_status(db, idea_id, "accepted")
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    actor = _actor_for_workspace(request, db, idea.workspace_id)
    # Pass workflow_hint as force_workflow so routing is authoritative.
    # If hint is empty/"none", force_workflow=None and auto-routing applies.
    hint = (idea.workflow_hint or "").strip()
    result = handle_request(
        idea.description, idea.workspace_id, db,
        force_workflow=hint if hint not in ("", "none") else None,
        actor_user_id=actor.actor_user_id,
        membership_id=actor.membership_id,
    )
    return {
        "accepted": True,
        "workflow_triggered": hint or "auto",
        "result": result,
    }


@app.post("/ideas/{idea_id}/reject")
def api_reject_idea(idea_id: str, request: Request, db: Session = Depends(get_db)):
    idea = repo.update_idea_status(db, idea_id, "rejected")
    if idea:
        _actor_for_workspace(request, db, idea.workspace_id)
    return {"rejected": idea_id}


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/conversations")
def api_conversations(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    convs = repo.get_conversations(db, workspace_id)
    return [{"id": c.id, "helper": c.helper, "input": c.input,
             "output": c.output[:300], "created_at": str(c.created_at),
             "actor_user_id": getattr(c, "actor_user_id", "") or "",
             "membership_id": getattr(c, "membership_id", "") or ""}
            for c in convs]


# ── Workflow history ──────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/workflows")
def api_workflow_history(workspace_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    runs = repo.get_workflow_runs(db, workspace_id)
    return [{"id": r.id, "workflow_name": r.workflow_name,
             "steps": r.steps, "final_output": r.final_output[:300],
             "created_at": str(r.created_at),
             "actor_user_id": getattr(r, "actor_user_id", "") or "",
             "membership_id": getattr(r, "membership_id", "") or ""} for r in runs]


# ── Automations ──────────────────────────────────────────────────────────────

@app.post("/workspace/{workspace_id}/automations")
def api_create_automation(workspace_id: str, req: AutomationCreateRequest, request: Request, db: Session = Depends(get_db)):
    actor = _actor_for_workspace(request, db, workspace_id)
    try:
        task = automation_service.create_schedule(
            db,
            workspace_id=workspace_id,
            actor_user_id=actor.actor_user_id,
            membership_id=actor.membership_id,
            schedule=req.schedule.model_dump(),
            payload=req.payload.model_dump(),
            connector_context=_normalize_connector_payload(req.connector_context),
            retry_policy=req.retry_policy.model_dump(),
            execution_policy=req.execution_policy.model_dump(),
            metadata_json=req.metadata_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return automation_service.task_to_dict(task)


@app.get("/workspace/{workspace_id}/automations")
def api_list_automations(workspace_id: str, request: Request, status: str = "", db: Session = Depends(get_db)):
    with timed_log(logger, "api.automations.list", workspace_id=workspace_id, status=status):
        _actor_for_workspace(request, db, workspace_id)
        return automation_service.list_schedules(db, workspace_id, status=status)


@app.get("/workspace/{workspace_id}/automations/{task_id}")
def api_get_automation(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    task = _automation_task_for_workspace(db, workspace_id, task_id)
    return automation_service.task_to_dict(task)


@app.patch("/workspace/{workspace_id}/automations/{task_id}")
def api_update_automation(workspace_id: str, task_id: str, req: AutomationUpdateRequest, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    _automation_task_for_workspace(db, workspace_id, task_id)
    try:
        task = automation_service.update_schedule(
            db,
            task_id,
            schedule=_model_dict(req.schedule),
            payload=_model_dict(req.payload),
            connector_context=_normalize_connector_payload(req.connector_context) if req.connector_context else None,
            retry_policy=_model_dict(req.retry_policy),
            execution_policy=_model_dict(req.execution_policy),
            metadata_json=req.metadata_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return automation_service.task_to_dict(task)


@app.post("/workspace/{workspace_id}/automations/{task_id}/pause")
def api_pause_automation(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    _automation_task_for_workspace(db, workspace_id, task_id)
    return automation_service.task_to_dict(automation_service.pause_schedule(db, task_id))


@app.post("/workspace/{workspace_id}/automations/{task_id}/resume")
def api_resume_automation(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    _automation_task_for_workspace(db, workspace_id, task_id)
    return automation_service.task_to_dict(automation_service.resume_schedule(db, task_id))


@app.post("/workspace/{workspace_id}/automations/{task_id}/cancel")
def api_cancel_automation(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    _automation_task_for_workspace(db, workspace_id, task_id)
    return automation_service.task_to_dict(automation_service.cancel_schedule(db, task_id))


@app.post("/workspace/{workspace_id}/automations/{task_id}/run-now")
def api_run_automation_now(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    with timed_log(logger, "api.automations.run_now", workspace_id=workspace_id, task_id=task_id):
        _actor_for_workspace(request, db, workspace_id)
        _automation_task_for_workspace(db, workspace_id, task_id)
        run = automation_service.run_now(db, task_id)
        return automation_service.run_to_dict(run)


@app.get("/workspace/{workspace_id}/automations/{task_id}/runs")
def api_list_automation_runs(workspace_id: str, task_id: str, request: Request, db: Session = Depends(get_db)):
    _actor_for_workspace(request, db, workspace_id)
    _automation_task_for_workspace(db, workspace_id, task_id)
    return automation_service.list_runs(db, workspace_id=workspace_id, scheduled_task_id=task_id)
