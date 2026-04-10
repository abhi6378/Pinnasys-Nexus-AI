"""
api/routes.py  —  FastAPI REST API
Run with: uvicorn api.routes:app --reload
"""
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from storage.db import init_db, get_db, SessionLocal
from storage import repositories as repo
from workspace.manager import create_workspace, list_workspaces, get_workspace_context
from brain.quiz_engine import get_next_question, save_answer, quiz_progress
from orchestrator.handler import handle_request
from helpers.configs import list_agents

app = FastAPI(title="Sintra Clone API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


# ── Schemas ──────────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str

class ChatRequest(BaseModel):
    workspace_id: str
    message: str
    agent: Optional[str] = None

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


# ── Workspace ─────────────────────────────────────────────────────────────────

@app.post("/workspace/create")
def api_create_workspace(req: CreateWorkspaceRequest, db: Session = Depends(get_db)):
    ws = create_workspace(req.name, db)
    return {"id": ws.id, "name": ws.name}


@app.get("/workspaces")
def api_list_workspaces(db: Session = Depends(get_db)):
    return list_workspaces(db)


@app.get("/workspace/{workspace_id}")
def api_get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    ctx = get_workspace_context(workspace_id, db)
    if not ctx:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ctx


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/chat")
def api_chat(req: ChatRequest, db: Session = Depends(get_db)):
    result = handle_request(req.message, req.workspace_id, db, force_agent=req.agent)
    return result


# ── Brain AI ─────────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/brain")
def api_get_brain(workspace_id: str, db: Session = Depends(get_db)):
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
def api_add_knowledge(req: KnowledgeRequest, db: Session = Depends(get_db)):
    item = repo.add_knowledge(
        db, req.workspace_id, req.type, req.title, req.content, req.tags
    )
    return {"id": item.id, "title": item.title}


@app.get("/workspace/{workspace_id}/knowledge")
def api_list_knowledge(workspace_id: str, db: Session = Depends(get_db)):
    items = repo.list_all_knowledge(db, workspace_id)
    return [{"id": i.id, "type": i.type, "title": i.title,
             "content": i.content[:200], "tags": i.tags} for i in items]


@app.delete("/knowledge/{item_id}")
def api_delete_knowledge(item_id: str, db: Session = Depends(get_db)):
    repo.delete_knowledge(db, item_id)
    return {"deleted": item_id}


# ── Quiz ─────────────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/quiz/next")
def api_quiz_next(workspace_id: str, db: Session = Depends(get_db)):
    question = get_next_question(workspace_id, db)
    progress = quiz_progress(workspace_id, db)
    return {"question": question, "progress": progress}


@app.post("/quiz/answer")
def api_quiz_answer(req: QuizAnswerRequest, db: Session = Depends(get_db)):
    save_answer(req.workspace_id, req.field, req.question, req.answer, db)
    return {"saved": True}


# ── Helpers ──────────────────────────────────────────────────────────────────

@app.get("/helpers")
def api_list_helpers():
    return list_agents()


# ── Ideas Inbox ───────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/ideas")
def api_get_ideas(workspace_id: str, db: Session = Depends(get_db)):
    ideas = repo.get_ideas(db, workspace_id)
    return [{"id": i.id, "title": i.title, "description": i.description,
             "source_agent": i.source_agent, "status": i.status,
             "workflow_hint": i.workflow_hint,
             "created_at": str(i.created_at)} for i in ideas]


@app.post("/ideas/{idea_id}/accept")
def api_accept_idea(idea_id: str, db: Session = Depends(get_db)):
    idea = repo.update_idea_status(db, idea_id, "accepted")
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    # Trigger workflow if hint provided
    if idea.workflow_hint and idea.workflow_hint != "none":
        from orchestrator.handler import handle_request
        result = handle_request(
            idea.description, idea.workspace_id, db
        )
        return {"accepted": True, "workflow_triggered": idea.workflow_hint, "result": result}
    return {"accepted": True}


@app.post("/ideas/{idea_id}/reject")
def api_reject_idea(idea_id: str, db: Session = Depends(get_db)):
    idea = repo.update_idea_status(db, idea_id, "rejected")
    return {"rejected": idea_id}


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/conversations")
def api_conversations(workspace_id: str, db: Session = Depends(get_db)):
    convs = repo.get_conversations(db, workspace_id)
    return [{"id": c.id, "helper": c.helper, "input": c.input,
             "output": c.output[:300], "created_at": str(c.created_at)}
            for c in convs]


# ── Workflow history ──────────────────────────────────────────────────────────

@app.get("/workspace/{workspace_id}/workflows")
def api_workflow_history(workspace_id: str, db: Session = Depends(get_db)):
    runs = repo.get_workflow_runs(db, workspace_id)
    return [{"id": r.id, "workflow_name": r.workflow_name,
             "steps": r.steps, "final_output": r.final_output[:300],
             "created_at": str(r.created_at)} for r in runs]
