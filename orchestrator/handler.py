"""
orchestrator/handler.py  —  The central brain. Routes every request.

Routing priority:
  1. force_agent      — user explicitly selected a helper in the UI
  2. force_workflow   — idea acceptance or direct workflow launch
  3. LLM router       — orchestrator/router.py (primary auto-route)
  4. Legacy fallback  — detect_workflow() keyword match → detect_agent() LLM pick
"""
import json
import hashlib
import logging
import uuid
from sqlalchemy.orm import Session

from brain.brain_ai import BrainAI
from brain.memory_extractor import extract_and_save
from helpers.executor import run_agent
from helpers.configs import AGENTS
from workflows.engine import run_workflow, WORKFLOWS
from storage import repositories as repo
from llm.client import generate_json
from orchestrator.router import route_request
from tools.connector_service import (
    normalize_connector_context,
    refresh_connector_status,
    validate_connector_context,
)
from automation.chat_intent import maybe_create_chat_schedule
from utils.logging_utils import log_event, log_exception, request_context

logger = logging.getLogger(__name__)


# ── Legacy routing (fallback only) ────────────────────────────────────────────

WORKFLOW_TRIGGERS = {
    "marketing_campaign": [
        "marketing campaign", "full campaign", "launch campaign",
        "promote my", "marketing plan", "ad campaign",
    ],
    "content_creation": [
        "write a blog", "write an article", "blog post",
        "long-form content", "content piece", "write content",
    ],
    "sales_outreach": [
        "cold email", "sales outreach", "lead outreach",
        "prospecting", "sales sequence", "reach out to leads",
    ],
    "support_setup": [
        "customer support", "help center", "faq", "support scripts",
        "support template", "reply to customers",
    ],
    "business_strategy": [
        "business strategy", "growth strategy", "business plan",
        "market entry", "strategic plan", "swot",
    ],
    "email_triage": [
        "check recent mails", "recent emails", "recent mail", "unread emails",
        "summarize unread emails", "check my inbox", "triage email", "triage inbox",
        "access my mail", "draft replies for last 1 day mails",
    ],
    "research_draft_send": [
        "research and email", "research and send", "research then email",
        "research topic and send", "research outreach",
    ],
    "lead_capture": [
        "save lead to hubspot", "capture lead", "sync lead", "log lead to sheets",
    ],
}


def detect_workflow(user_input: str) -> str | None:
    """Legacy keyword workflow detection. Used as fallback only."""
    lowered = user_input.lower()
    for workflow_key, triggers in WORKFLOW_TRIGGERS.items():
        if any(t in lowered for t in triggers):
            return workflow_key
    return None


def detect_agent(user_input: str) -> str:
    """
    Legacy LLM-based agent selection. Used as fallback only.
    Falls back to 'assistant' if uncertain.
    """
    agent_list = "\n".join(
        f"- {key}: {info['role']} — {info['goal']}"
        for key, info in AGENTS.items()
    )
    prompt = f"""
Given this user request: "{user_input}"

Choose the most suitable AI helper from this list:
{agent_list}

Respond with a JSON object:
{{"agent": "<agent_key>", "reason": "<one line why>"}}

Only use the exact keys from the list above.
Default to "assistant" if unsure.
"""
    try:
        raw = generate_json(prompt)
        data = json.loads(raw)
        return data.get("agent", "assistant")
    except Exception:
        return "assistant"


def detect_opportunity(output: str, brain_context: str) -> dict | None:
    """
    Checks if the helper output contains an actionable opportunity
    worth pushing to the Ideas Inbox.
    """
    prompt = f"""
You are analyzing an AI assistant's output for business opportunities.

Output:
{output[:1000]}

Business Context:
{brain_context[:500]}

Does this output suggest a follow-up action or opportunity the user should consider?
Respond with JSON:
{{
  "has_opportunity": true or false,
  "title": "short opportunity title",
  "description": "why this is an opportunity",
  "workflow_hint": "marketing_campaign | content_creation | sales_outreach | support_setup | business_strategy | none"
}}
"""
    try:
        raw = generate_json(prompt)
        data = json.loads(raw)
        if data.get("has_opportunity"):
            return data
    except Exception:
        pass
    return None


def _sanitize_history_output(text: str) -> str:
    lowered = str(text or "").lower()
    if (
        "needs access to" in lowered
        or "requested an invalid tool" in lowered
        or "tried to use" in lowered
        or lowered.startswith("error:")
        or "i can't process this request" in lowered
    ):
        return ""
    return str(text or "")


def _should_probe_opportunity(
    workspace_id: str,
    user_input: str,
    output: str,
    *,
    threshold: float = 0.4,
) -> bool:
    if len(output or "") <= 200:
        return False
    fingerprint = f"{workspace_id}|{user_input.strip()}|{(output or '')[:300]}"
    bucket = int(hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:8], 16)
    return (bucket % 1000) / 1000.0 < threshold


# ── Route execution helpers ───────────────────────────────────────────────────

def _exec_single_agent(agent_key: str, user_input: str,
                        brain_context: str,
                        workspace_id: str = "",
                        db: Session = None,
                        resume_state: dict = None,
                        route_context: dict | None = None,
                        connector_context: dict | None = None,
                        actor_user_id: str | None = None,
                        membership_id: str | None = None) -> dict:
    """Execute a single agent and return a standardised result dict.

    When workspace_id and db are provided, the agent runs in tool-aware
    mode — it can request Composio tools and may return connect_required.
    When they're absent, the agent runs in pure text-only mode (unchanged).

    Tool metadata (tool_used) is propagated when a tool was executed
    successfully, enabling the UI to show execution indicators.
    """
    history: list[dict] | None = None
    if workspace_id and db:
        try:
            rows = repo.get_conversations(db, workspace_id, limit=10)
            history = []
            for row in reversed(rows):
                history.append({"role": "user", "content": row.input})
                safe_output = _sanitize_history_output(row.output)
                if safe_output:
                    history.append({"role": "assistant", "content": safe_output})
        except Exception as exc:
            log_exception(
                logger,
                "agent.history_load_failed",
                exc,
                workspace_id=workspace_id,
                agent_name=agent_key,
            )

    log_event(
        logger,
        logging.INFO,
        "agent.execute.start",
        workspace_id=workspace_id,
        agent_name=agent_key,
        has_history=bool(history),
        is_resume=bool(resume_state),
    )
    agent_state = dict(resume_state or {})
    if actor_user_id and "actor_user_id" not in agent_state:
        agent_state["actor_user_id"] = actor_user_id
    if membership_id and "membership_id" not in agent_state:
        agent_state["membership_id"] = membership_id
    agent_result = run_agent(
        agent_key, user_input, brain_context,
        workspace_id=workspace_id,
        db=db,
        workflow_state=agent_state or resume_state,
        history=history,
        route_context=route_context,
        connector_context=connector_context,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
    )
    log_event(
        logger,
        logging.INFO,
        "agent.execute.finish",
        workspace_id=workspace_id,
        agent_name=agent_key,
        success=agent_result.get("success"),
        mode=agent_result.get("mode", "single"),
        tool_name=agent_result.get("tool_used"),
    )

    # ── connect_required: propagate as a distinct mode ────────────────
    if agent_result.get("connect_required"):
        return {
            "mode":          "connect_required",
            "agent":         agent_key,
            "name":          agent_result.get("name", agent_key),
            "output":        agent_result["output"],
            "steps":         [],
            "connect_required": True,
            "connect_url":   agent_result.get("connect_url"),
            "resume_token":  agent_result.get("resume_token", ""),
            "toolkit":       agent_result.get("toolkit", ""),
        }

    # ── auth_unavailable: propagate as distinct mode ──────────────────
    if agent_result.get("mode") == "auth_unavailable":
        return {
            "mode":    "auth_unavailable",
            "agent":   agent_key,
            "name":    agent_result.get("name", agent_key),
            "output":  agent_result["output"],
            "steps":   [],
            "toolkit": agent_result.get("toolkit", ""),
            "auth_error": agent_result.get("auth_error", ""),
        }

    if agent_result.get("mode") == "invalid_tool":
        return {
            "mode":    "invalid_tool",
            "agent":   agent_key,
            "name":    agent_result.get("name", agent_key),
            "output":  agent_result["output"],
            "steps":   [],
            "error":   True,
        }

    if agent_result.get("mode") == "validation_error":
        result = {
            "mode":    "validation_error",
            "agent":   agent_key,
            "name":    agent_result.get("name", agent_key),
            "output":  agent_result["output"],
            "steps":   [],
            "error":   True,
        }
        for field in ("approval_required", "approval_requirement", "resume_token", "pending_kind"):
            if field in agent_result:
                result[field] = agent_result.get(field)
        return result

    if agent_result.get("mode") == "tool_error":
        return {
            "mode":    "tool_error",
            "agent":   agent_key,
            "name":    agent_result.get("name", agent_key),
            "output":  agent_result["output"],
            "steps":   [],
            "error":   True,
        }

    # ── Normal single-agent response ──────────────────────────────────
    result = {
        "mode":   "single",
        "agent":  agent_key,
        "name":   agent_result.get("name", agent_key),
        "output": agent_result["output"],
        "steps":  [],
    }

    # Propagate tool metadata if a tool was used
    tool_used = agent_result.get("tool_used")
    if tool_used:
        result["tool_used"] = tool_used
    if agent_result.get("tool_output") is not None:
        result["tool_output"] = agent_result.get("tool_output")

    return result


def _exec_workflow(workflow_key: str, user_input: str,
                    brain_context: str, workspace_id: str,
                    db: Session, resume_state: dict = None,
                    connector_context: dict | None = None,
                    request_id: str = "",
                    actor_user_id: str | None = None,
                    membership_id: str | None = None) -> dict:
    """Execute a workflow chain and return a standardised result dict."""
    workflow_resume_state = dict(resume_state or {})
    if actor_user_id and "actor_user_id" not in workflow_resume_state:
        workflow_resume_state["actor_user_id"] = actor_user_id
    if membership_id and "membership_id" not in workflow_resume_state:
        workflow_resume_state["membership_id"] = membership_id
    wf_result = run_workflow(
        workflow_key, user_input, brain_context,
        workspace_id=workspace_id, db=db,
        resume_state=workflow_resume_state or resume_state,
        connector_context=connector_context,
    )

    # ── Handle workflow interrupt (connect_required or validation_error) ──
    if wf_result.get("mode") == "interrupt":
        interrupt = wf_result["interrupt"]
        interrupt_mode = interrupt.get("mode")
        if not interrupt_mode:
            interrupt_mode = "connect_required" if interrupt.get("connect_required") else "validation_error"
        repo.save_workflow_run(
            db,
            workspace_id,
            workflow_key,
            wf_result["steps"],
            interrupt.get("output", "Workflow paused."),
            status="paused",
            request_id=request_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            metadata_json={
                "mode": interrupt_mode,
                "workflow_paused": True,
                "step_label": wf_result.get("step_label"),
                "resume_token_present": bool(interrupt.get("resume_token")),
                "toolkit": interrupt.get("toolkit", ""),
            },
        )
        return {
            "mode":             interrupt_mode,
            "workflow":         workflow_key,
            "output":           interrupt.get("output", "Workflow paused."),
            "steps":            wf_result["steps"],
            "connect_required": interrupt.get("connect_required", False),
            "connect_url":      interrupt.get("connect_url"),
            "resume_token":     interrupt.get("resume_token", ""),
            "toolkit":          interrupt.get("toolkit", ""),
            "auth_error":       interrupt.get("auth_error", ""),
            "is_workflow":      True, # Help UI/API know this is a workflow
            "workflow_paused":  True,
            "step_label":       wf_result.get("step_label"),
            "error":            interrupt_mode != "connect_required",
            "approval_required": interrupt.get("approval_required", False),
            "approval_requirement": interrupt.get("approval_requirement"),
            "pending_kind": interrupt.get("pending_kind", ""),
        }

    if wf_result.get("error") and wf_result.get("mode") in {
        "auth_unavailable",
        "invalid_tool",
        "validation_error",
        "tool_error",
    }:
        mode = wf_result["mode"]
        repo.save_workflow_run(
            db,
            workspace_id,
            workflow_key,
            wf_result["steps"],
            wf_result["final_output"],
            status="failed" if mode == "tool_error" else "paused",
            request_id=request_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
            metadata_json={
                "mode": mode,
                "step_label": wf_result.get("step_label"),
                "toolkit": wf_result.get("toolkit", ""),
                "resume_token_present": bool(wf_result.get("resume_token")),
            },
        )
        return {
            "mode": mode,
            "workflow": workflow_key,
            "output": wf_result["final_output"],
            "steps": wf_result["steps"],
            "error": True,
            "toolkit": wf_result.get("toolkit", ""),
            "auth_error": wf_result.get("auth_error", ""),
            "is_workflow": True,
            "workflow_paused": mode in {"auth_unavailable", "invalid_tool", "validation_error"},
            "step_label": wf_result.get("step_label"),
            "approval_required": wf_result.get("approval_required", False),
            "approval_requirement": wf_result.get("approval_requirement"),
            "resume_token": wf_result.get("resume_token", ""),
            "pending_kind": wf_result.get("pending_kind", ""),
        }

    # ── Normal workflow response ──────────────────────────────────────
    result = {
        "mode":     "workflow",
        "workflow": workflow_key,
        "output":   wf_result["final_output"],
        "steps":    wf_result["steps"],
        "error":    wf_result.get("error", False),
    }
    repo.save_workflow_run(
        db, workspace_id, workflow_key,
        wf_result["steps"], wf_result["final_output"],
        status="completed",
        request_id=request_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        metadata_json={"mode": "workflow", "resumed": bool(resume_state)},
    )
    return result


def _exec_clarify(question: str) -> dict:
    """Return a clarification-request result."""
    return {
        "mode":   "clarify",
        "agent":  "system",
        "output": f"🤔 **I need a bit more detail to help you effectively.**\n\n{question}",
        "steps":  [],
    }


def _exec_reject(reason: str) -> dict:
    """Return a safe rejection result."""
    return {
        "mode":   "reject",
        "agent":  "system",
        "output": f"🚫 **I can't process this request.**\n\n{reason}",
        "steps":  [],
    }


# ── Router-driven auto-routing ────────────────────────────────────────────────

def _auto_route(user_input: str, workspace_id: str, db: Session,
                brain_context: str,
                resume_state: dict = None,
                connector_context: dict | None = None,
                request_id: str = "",
                actor_user_id: str | None = None,
                membership_id: str | None = None) -> dict:
    """
    Primary auto-routing path. Tries the LLM router first; falls back to
    legacy keyword → LLM detection if the router fails.
    """
    # ── Try the central LLM router ────────────────────────────────────────
    route = route_request(user_input, workspace_id, db, brain_context, connector_context=connector_context)

    if route:
        log_event(
            logger,
            logging.INFO,
            "route.selected",
            workspace_id=workspace_id,
            route_type=route.get("route_type"),
            agent_name=route.get("selected_agent"),
            workflow_name=route.get("selected_workflow"),
            confidence=route.get("confidence", 0.0),
            route_method=route.get("route_method", "llm_router"),
        )
        route_type = route["route_type"]

        if route_type == "single_agent":
            return _exec_single_agent(
                route["selected_agent"], user_input, brain_context,
                workspace_id=workspace_id, db=db,
                resume_state=resume_state,
                route_context=route,
                connector_context=connector_context,
                actor_user_id=actor_user_id,
                membership_id=membership_id,
            )

        if route_type == "workflow":
            wf_key = route.get("selected_workflow")
            if wf_key and wf_key in WORKFLOWS:
                return _exec_workflow(
                    wf_key, user_input, brain_context, workspace_id, db,
                    resume_state=resume_state,
                    connector_context=connector_context,
                    request_id=request_id,
                    actor_user_id=actor_user_id,
                    membership_id=membership_id,
                )
            # Router returned a workflow type but no valid key — fall through
            # to legacy detection which may find the right workflow.

        if route_type == "clarify":
            return _exec_clarify(
                route.get("clarification_question",
                           "Could you provide more detail?")
            )

        if route_type == "reject":
            return _exec_reject(
                route.get("reason",
                           "This request is outside the system's capabilities.")
            )

    # ── Fallback: legacy keyword → LLM detection ─────────────────────────
    workflow_key = detect_workflow(user_input)
    if workflow_key:
        log_event(
            logger,
            logging.INFO,
            "route.selected",
            workspace_id=workspace_id,
            route_type="workflow",
            workflow_name=workflow_key,
            confidence=0.50,
            route_method="legacy_keyword",
        )
        return _exec_workflow(
            workflow_key, user_input, brain_context, workspace_id, db,
            resume_state=resume_state,
            connector_context=connector_context,
            request_id=request_id,
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        )

    agent_key = detect_agent(user_input)
    log_event(
        logger,
        logging.INFO,
        "route.selected",
        workspace_id=workspace_id,
        route_type="single_agent",
        agent_name=agent_key,
        confidence=0.50,
        route_method="legacy_llm_agent",
    )
    return _exec_single_agent(
        agent_key, user_input, brain_context,
        workspace_id=workspace_id, db=db,
        resume_state=resume_state,
        connector_context=connector_context,
        actor_user_id=actor_user_id,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def handle_request(user_input: str, workspace_id: str, db: Session,
                   force_agent: str = None,
                   force_workflow: str = None,
                   resume_state: dict = None,
                   connector_context: dict | None = None,
                   actor_user_id: str | None = None,
                   membership_id: str | None = None) -> dict:
    """
    Main orchestrator function. Called by API and UI.

    Returns:
        {
          mode: "single" | "workflow" | "clarify" | "reject" | "connect_required",
          agent/workflow: str,
          output: str,
          steps: list (workflows only),
          idea: dict | None,
          error: bool,
          # Extra fields when mode == "connect_required":
          connect_required: True,
          connect_url: str | None,
          resume_token: str,
          toolkit: str,
        }
    """
    request_id = ""
    local_resume_state = resume_state
    if isinstance(local_resume_state, dict):
        request_id = str(local_resume_state.get("request_id", "")).strip()
    if not request_id:
        request_id = str(uuid.uuid4())
    if isinstance(local_resume_state, dict) and not local_resume_state.get("request_id"):
        local_resume_state = dict(local_resume_state)
        local_resume_state["request_id"] = request_id
    if isinstance(local_resume_state, dict):
        if actor_user_id and "actor_user_id" not in local_resume_state:
            local_resume_state["actor_user_id"] = actor_user_id
        if membership_id and "membership_id" not in local_resume_state:
            local_resume_state["membership_id"] = membership_id

    with request_context(request_id=request_id, workspace_id=workspace_id):
        request_cache: dict[str, object] = {}
        normalized_connector = normalize_connector_context(connector_context)
        brain_context = ""
        log_event(
            logger,
            logging.INFO,
            "request.start",
            workspace_id=workspace_id,
            forced_agent=force_agent or "",
            workflow_name=force_workflow or "",
            is_resume=bool(resume_state),
            selected_toolkit=normalized_connector.selected_toolkit,
            connector_mode=normalized_connector.mode,
        )

        normalized_connector, connector_status, connector_error = validate_connector_context(
            normalized_connector,
            workspace_id,
            db,
            request_cache=request_cache,
        )
        if connector_error:
            return {
                "mode": "validation_error",
                "agent": "system",
                "output": connector_error,
                "steps": [],
                "error": True,
                "idea": None,
                "connector_context": normalized_connector.to_dict(),
                "connector_status": connector_status.to_dict(),
            }

        if not force_agent and not force_workflow and not resume_state:
            workflow_hint = detect_workflow(user_input)
            schedule_result = maybe_create_chat_schedule(
                db=db,
                workspace_id=workspace_id,
                user_input=user_input,
                workflow_key=workflow_hint,
                connector_context=normalized_connector.to_dict(),
                actor_user_id=actor_user_id,
                membership_id=membership_id,
            )
            if (
                schedule_result
                and schedule_result.get("schedule_target_required")
                and not workflow_hint
            ):
                brain = BrainAI(workspace_id, db)
                brain_context = brain.get_relevant_context(user_input)
                route_hint = route_request(
                    user_input,
                    workspace_id,
                    db,
                    brain_context,
                    connector_context=normalized_connector.to_dict(),
                )
                routed_workflow = ""
                if isinstance(route_hint, dict) and route_hint.get("route_type") == "workflow":
                    candidate = str(route_hint.get("selected_workflow", "") or "").strip()
                    if candidate in WORKFLOWS:
                        routed_workflow = candidate
                if routed_workflow:
                    schedule_result = maybe_create_chat_schedule(
                        db=db,
                        workspace_id=workspace_id,
                        user_input=user_input,
                        workflow_key=routed_workflow,
                        connector_context=normalized_connector.to_dict(),
                        actor_user_id=actor_user_id,
                        membership_id=membership_id,
                    )
            if schedule_result:
                repo.save_conversation(
                    db,
                    workspace_id,
                    schedule_result.get("agent", "system"),
                    user_input,
                    schedule_result["output"],
                    request_id=request_id,
                    metadata_json={
                        "mode": schedule_result.get("mode", ""),
                        "automation_id": schedule_result.get("automation", {}).get("id", ""),
                    },
                    actor_user_id=actor_user_id,
                    membership_id=membership_id,
                )
                return schedule_result

        # 1. Load Brain AI context
        if not brain_context:
            brain = BrainAI(workspace_id, db)
            brain_context = brain.get_relevant_context(user_input)

        # 2. Route — priority: force_agent > force_workflow > LLM router > legacy
        if force_agent:
            result = _exec_single_agent(
                force_agent, user_input, brain_context,
                workspace_id=workspace_id, db=db,
                resume_state=local_resume_state,
                connector_context=normalized_connector.to_dict(),
                actor_user_id=actor_user_id,
                membership_id=membership_id,
            )
        elif force_workflow and force_workflow in WORKFLOWS:
            result = _exec_workflow(
                force_workflow, user_input, brain_context, workspace_id, db,
                resume_state=local_resume_state,
                connector_context=normalized_connector.to_dict(),
                request_id=request_id,
                actor_user_id=actor_user_id,
                membership_id=membership_id,
            )
        else:
            result = _auto_route(
                user_input,
                workspace_id,
                db,
                brain_context,
                resume_state=local_resume_state,
                connector_context=normalized_connector.to_dict(),
                request_id=request_id,
                actor_user_id=actor_user_id,
                membership_id=membership_id,
            )

        # 3. Save conversation — always, including on workflow error / clarify / reject,
        #    so the exchange is visible in chat history and persists after refresh.
        agent_label = result.get("agent") or result.get("workflow", "system")
        repo.save_conversation(
            db,
            workspace_id,
            agent_label,
            user_input,
            result["output"],
            request_id=request_id,
            metadata_json={
                "mode": result.get("mode", ""),
                "workflow": result.get("workflow", ""),
                "toolkit": result.get("toolkit", ""),
                "workflow_resumed": bool(local_resume_state),
            },
            actor_user_id=actor_user_id,
            membership_id=membership_id,
        )

        # 4. Auto-extract memory — skip on error, clarify, reject, and connect_required.
        is_error   = result.get("error", False)
        is_special = result.get("mode") in (
            "clarify",
            "reject",
            "connect_required",
            "auth_unavailable",
            "invalid_tool",
            "validation_error",
            "tool_error",
        )
        if not is_error and not is_special:
            extract_and_save(
                workspace_id,
                result["output"],
                db,
                user_input=user_input,
                assistant_output=result["output"],
                agent_key=result.get("agent", ""),
                workflow_name=result.get("workflow", ""),
                workflow_steps=result.get("steps", []),
                tool_used=result.get("tool_used", ""),
                tool_output=result.get("tool_output"),
                toolkit=result.get("toolkit", ""),
                source_kind="workflow_output" if result.get("mode") == "workflow" else "assistant_output",
                route_context={
                    "mode": result.get("mode", ""),
                    "workflow_resumed": bool(local_resume_state),
                    "connector_context": normalized_connector.to_dict(),
                },
            )

        # 5. Check for Ideas Inbox opportunity — skip on error / clarify / reject.
        if not is_error and not is_special:
            should_probe = result.get("mode") in ("single",) and _should_probe_opportunity(
                workspace_id,
                user_input,
                result.get("output", ""),
            )
            opportunity = detect_opportunity(result["output"], brain_context) if should_probe else None
            if opportunity:
                idea = repo.push_idea(
                    db, workspace_id,
                    title=opportunity["title"],
                    description=opportunity["description"],
                    source_agent=agent_label,
                    workflow_hint=opportunity.get("workflow_hint", "")
                )
                result["idea"] = {
                    "id":          idea.id,
                    "title":       idea.title,
                    "description": idea.description,
                }
            else:
                result["idea"] = None
        else:
            result["idea"] = None

        # Propagate error flag so callers (UI, API) can surface it correctly.
        result.setdefault("error", False)
        if local_resume_state and result.get("mode") == "workflow":
            result["workflow_resumed"] = True
        if local_resume_state and result.get("mode") == "single":
            result["workflow_resumed"] = bool(local_resume_state.get("workflow_key"))
        result["connector_context"] = normalized_connector.to_dict()
        result["connector_status"] = connector_status.to_dict()

        log_event(
            logger,
            logging.INFO,
            "request.finish",
            workspace_id=workspace_id,
            agent_name=result.get("agent"),
            workflow_name=result.get("workflow"),
            mode=result.get("mode"),
            error=result.get("error", False),
        )
        return result
