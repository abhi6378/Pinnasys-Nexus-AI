"""
orchestrator/handler.py  —  The central brain. Routes every request.
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


# ── Workflow detection keywords ───────────────────────────────────────────────

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
    lowered = user_input.lower()
    for workflow_key, triggers in WORKFLOW_TRIGGERS.items():
        if any(t in lowered for t in triggers):
            return workflow_key
    return None


def detect_agent(user_input: str) -> str:
    """
    Uses GPT-4o-mini to pick the best helper if no workflow detected.
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


# ── Main entry point ──────────────────────────────────────────────────────────

def handle_request(user_input: str, workspace_id: str, db: Session,
                   force_agent: str = None) -> dict:
    """
    Main orchestrator function. Called by API and UI.

    Returns:
        {
          mode: "single" | "workflow",
          agent/workflow: str,
          output: str,
          steps: list (workflows only),
          idea: dict | None
        }
    """
    # 1. Load Brain AI context
    brain = BrainAI(workspace_id, db)
    brain_context = brain.get_relevant_context(user_input)

    result = {}

    # 2. Route: workflow or single agent
    if force_agent:
        # User explicitly chose a helper
        agent_result = run_agent(force_agent, user_input, brain_context)
        result = {
            "mode":   "single",
            "agent":  force_agent,
            "name":   agent_result.get("name", force_agent),
            "output": agent_result["output"],
            "steps":  [],
        }
    else:
        workflow_key = detect_workflow(user_input)
        if workflow_key:
            wf_result = run_workflow(workflow_key, user_input, brain_context)
            result = {
                "mode":     "workflow",
                "workflow": workflow_key,
                "output":   wf_result["final_output"],
                "steps":    wf_result["steps"],
            }
            # Save workflow run
            repo.save_workflow_run(
                db, workspace_id, workflow_key,
                wf_result["steps"], wf_result["final_output"]
            )
        else:
            agent_key = detect_agent(user_input)
            agent_result = run_agent(agent_key, user_input, brain_context)
            result = {
                "mode":   "single",
                "agent":  agent_key,
                "name":   agent_result.get("name", agent_key),
                "output": agent_result["output"],
                "steps":  [],
            }

    # 3. Save conversation
    agent_label = result.get("agent") or result.get("workflow", "system")
    repo.save_conversation(db, workspace_id, agent_label, user_input, result["output"])

    # 4. Auto-extract memory from output
    extract_and_save(workspace_id, result["output"], db)

    # 5. Check for Ideas Inbox opportunity
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
            "id":    idea.id,
            "title": idea.title,
            "description": idea.description,
        }
    else:
        result["idea"] = None

    return result
