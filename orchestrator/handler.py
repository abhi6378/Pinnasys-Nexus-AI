"""
orchestrator/handler.py  —  The central brain. Routes every request.

Routing priority:
  1. force_agent      — user explicitly selected a helper in the UI
  2. force_workflow   — idea acceptance or direct workflow launch
  3. LLM router       — orchestrator/router.py (primary auto-route)
  4. Legacy fallback  — detect_workflow() keyword match → detect_agent() LLM pick
"""
import json
from sqlalchemy.orm import Session

from brain.brain_ai import BrainAI
from brain.memory_extractor import extract_and_save
from helpers.executor import run_agent
from helpers.configs import AGENTS
from workflows.engine import run_workflow, WORKFLOWS
from storage import repositories as repo
from llm.client import generate_json
from orchestrator.router import route_request


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


# ── Route execution helpers ───────────────────────────────────────────────────

def _exec_single_agent(agent_key: str, user_input: str,
                        brain_context: str) -> dict:
    """Execute a single agent and return a standardised result dict."""
    agent_result = run_agent(agent_key, user_input, brain_context)
    return {
        "mode":   "single",
        "agent":  agent_key,
        "name":   agent_result.get("name", agent_key),
        "output": agent_result["output"],
        "steps":  [],
    }


def _exec_workflow(workflow_key: str, user_input: str,
                    brain_context: str, workspace_id: str,
                    db: Session) -> dict:
    """Execute a workflow chain and return a standardised result dict."""
    wf_result = run_workflow(workflow_key, user_input, brain_context)
    result = {
        "mode":     "workflow",
        "workflow": workflow_key,
        "output":   wf_result["final_output"],
        "steps":    wf_result["steps"],
        "error":    wf_result.get("error", False),
    }
    repo.save_workflow_run(
        db, workspace_id, workflow_key,
        wf_result["steps"], wf_result["final_output"]
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
                brain_context: str) -> dict:
    """
    Primary auto-routing path. Tries the LLM router first; falls back to
    legacy keyword → LLM detection if the router fails.
    """
    # ── Try the central LLM router ────────────────────────────────────────
    route = route_request(user_input, workspace_id, db, brain_context)

    if route:
        route_type = route["route_type"]

        if route_type == "single_agent":
            return _exec_single_agent(
                route["selected_agent"], user_input, brain_context
            )

        if route_type == "workflow":
            wf_key = route.get("selected_workflow")
            if wf_key and wf_key in WORKFLOWS:
                return _exec_workflow(
                    wf_key, user_input, brain_context, workspace_id, db
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
        return _exec_workflow(
            workflow_key, user_input, brain_context, workspace_id, db
        )

    agent_key = detect_agent(user_input)
    return _exec_single_agent(agent_key, user_input, brain_context)


# ── Main entry point ──────────────────────────────────────────────────────────

def handle_request(user_input: str, workspace_id: str, db: Session,
                   force_agent: str = None,
                   force_workflow: str = None) -> dict:
    """
    Main orchestrator function. Called by API and UI.

    Returns:
        {
          mode: "single" | "workflow" | "clarify" | "reject",
          agent/workflow: str,
          output: str,
          steps: list (workflows only),
          idea: dict | None,
          error: bool,
        }
    """
    # 1. Load Brain AI context
    brain = BrainAI(workspace_id, db)
    brain_context = brain.get_relevant_context(user_input)

    # 2. Route — priority: force_agent > force_workflow > LLM router > legacy
    if force_agent:
        result = _exec_single_agent(force_agent, user_input, brain_context)
    elif force_workflow and force_workflow in WORKFLOWS:
        result = _exec_workflow(
            force_workflow, user_input, brain_context, workspace_id, db
        )
    else:
        result = _auto_route(user_input, workspace_id, db, brain_context)

    # 3. Save conversation — always, including on workflow error / clarify / reject,
    #    so the exchange is visible in chat history and persists after refresh.
    agent_label = result.get("agent") or result.get("workflow", "system")
    repo.save_conversation(db, workspace_id, agent_label, user_input, result["output"])

    # 4. Auto-extract memory — skip on error, clarify, and reject.
    is_error   = result.get("error", False)
    is_special = result.get("mode") in ("clarify", "reject")
    if not is_error and not is_special:
        extract_and_save(workspace_id, result["output"], db)

    # 5. Check for Ideas Inbox opportunity — skip on error / clarify / reject.
    if not is_error and not is_special:
        opportunity = detect_opportunity(result["output"], brain_context)
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

    return result
