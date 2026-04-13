"""
orchestrator/router.py  —  Central LLM-based router for all request routing.

Replaces keyword-based detect_workflow() and LLM-based detect_agent() as the
PRIMARY routing mechanism. Those older functions remain in handler.py as
fallback only, invoked when the router call fails or returns invalid JSON.

Route types:
  single_agent — one agent handles the request
  workflow     — multi-step agent chain
  clarify      — request is too vague; ask user for more detail
  reject       — unsafe, impossible, or out-of-scope request
"""
import json
from helpers.configs import AGENTS
from workflows.engine import WORKFLOWS
from llm.client import generate_json
from storage import repositories as repo


# ── Router system prompt ──────────────────────────────────────────────────────

ROUTER_SYSTEM_PROMPT = """You are the routing brain of a multi-agent AI system.

Your job is to decide the best execution path for the user request using the available agents, workflows, and context.

You must choose exactly one of these route types:
- single_agent: one agent can solve it well
- workflow: multiple agents should execute in sequence
- clarify: the request is missing critical information
- reject: unsafe, impossible, or outside system scope

Decision rules:
1. Prefer the simplest valid route.
2. Use workflow only when the request has multiple distinct sub-tasks that benefit from different specialists.
3. Use single_agent when one agent clearly fits the main task.
4. Use clarify when important details are missing and routing would likely be wrong.
5. Use reject only for unsafe, impossible, or unsupported requests.
6. Do not invent agents or workflows that are not in the provided catalog.
7. Use the fewest steps possible.
8. If confidence is low, choose clarify instead of guessing.
9. Prefer agents whose capabilities closely match the task intent, constraints, and output format.
10. If the request is broad, choose the best first agent or a short workflow, not a long plan.

You are given:
- user request
- conversation context
- memory context
- available agents with capabilities
- available workflows with descriptions
- retrieved semantic candidates

You must return JSON only, with no markdown, no explanation, no extra text.

Output schema:
{
  "route_type": "single_agent | workflow | clarify | reject",
  "confidence": 0.0-1.0,
  "primary_intent": "short label",
  "reason": "short routing reason",
  "selected_agent": "agent_key or null",
  "selected_workflow": "workflow_key or null",
  "steps": [
    {
      "agent": "agent_key",
      "task": "short task description"
    }
  ],
  "clarification_question": "string or empty",
  "risk_flags": ["optional list of short strings"]
}

Routing policy:
- If route_type = single_agent, selected_agent must be set and steps should contain exactly one step.
- If route_type = workflow, selected_workflow may be set, and steps must describe the execution order.
- If route_type = clarify, clarification_question must be filled.
- If route_type = reject, reason must clearly explain why."""


# ── Workflow metadata for router context ──────────────────────────────────────

WORKFLOW_DESCRIPTIONS = {
    "marketing_campaign": {
        "title": "Marketing Campaign",
        "description": "Full campaign package: copy → SEO optimization → social media posts",
        "agents": ["copywriter", "seo", "social_media"],
    },
    "content_creation": {
        "title": "Content Creation",
        "description": "Blog post or article with SEO recommendations",
        "agents": ["copywriter", "seo"],
    },
    "sales_outreach": {
        "title": "Sales Outreach",
        "description": "Sales strategy + 3-email outreach sequence",
        "agents": ["sales", "email_marketer"],
    },
    "support_setup": {
        "title": "Support Setup",
        "description": "Customer support scripts + polished FAQ content",
        "agents": ["support", "copywriter"],
    },
    "business_strategy": {
        "title": "Business Strategy",
        "description": "Full business strategy + KPIs and data recommendations",
        "agents": ["strategist", "data_analyst"],
    },
}


# ── Build the prompt payload ──────────────────────────────────────────────────

def _build_agent_catalog() -> str:
    """Format all agents as a compact catalog for the router."""
    lines = []
    for key, info in AGENTS.items():
        use_cases = ", ".join(info.get("use_cases", [])[:3])
        lines.append(
            f"- {key}: {info['name']} — {info['role']}. "
            f"Goal: {info['goal']} "
            f"Use cases: {use_cases}"
        )
    return "\n".join(lines)


def _build_workflow_catalog() -> str:
    """Format all workflows as a compact catalog for the router."""
    lines = []
    for key, meta in WORKFLOW_DESCRIPTIONS.items():
        agents = " → ".join(meta["agents"])
        lines.append(
            f"- {key}: {meta['title']} — {meta['description']}. "
            f"Chain: {agents}"
        )
    return "\n".join(lines)


def _build_conversation_context(workspace_id: str, db, limit: int = 5) -> str:
    """Fetch recent conversation turns to give the router conversational memory."""
    try:
        rows = repo.get_conversations(db, workspace_id, limit=limit)
        if not rows:
            return "No previous conversations."
        parts = []
        for row in reversed(rows):  # oldest first
            parts.append(f"User: {row.input[:150]}")
            parts.append(f"{row.helper}: {row.output[:150]}")
        return "\n".join(parts)
    except Exception:
        return "Conversation history unavailable."


def _build_router_prompt(
    user_input: str,
    conversation_context: str,
    memory_context: str,
) -> str:
    """Assemble the full user-side prompt sent to the LLM for routing."""
    agent_catalog = _build_agent_catalog()
    workflow_catalog = _build_workflow_catalog()

    return f"""=== USER REQUEST ===
{user_input}

=== CONVERSATION CONTEXT ===
{conversation_context}

=== MEMORY CONTEXT ===
{memory_context[:800] if memory_context else "No memory context available."}

=== AVAILABLE AGENTS ===
{agent_catalog}

=== AVAILABLE WORKFLOWS ===
{workflow_catalog}

Decide the best route. Return JSON only."""


# ── Validation ────────────────────────────────────────────────────────────────

VALID_ROUTE_TYPES = {"single_agent", "workflow", "clarify", "reject"}
VALID_AGENT_KEYS = set(AGENTS.keys())
VALID_WORKFLOW_KEYS = set(WORKFLOWS.keys())

# If confidence is below this threshold, override to "clarify"
CONFIDENCE_THRESHOLD = 0.4


def _validate_route(data: dict) -> dict | None:
    """
    Validates the router JSON. Returns a cleaned dict or None if invalid.
    Applies safety overrides (e.g. low-confidence → clarify).
    """
    route_type = data.get("route_type", "")
    if route_type not in VALID_ROUTE_TYPES:
        return None

    confidence = data.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5

    # Low-confidence override: prefer clarify over guessing
    if confidence < CONFIDENCE_THRESHOLD and route_type in ("single_agent", "workflow"):
        data["route_type"]    = "clarify"
        data["confidence"]    = confidence
        data["reason"]        = f"Low confidence ({confidence:.2f}). Asking for clarification."
        data["clarification_question"] = data.get(
            "clarification_question",
            "Could you provide more detail about what you need?"
        )
        return data

    # Validate single_agent: selected_agent must exist
    if route_type == "single_agent":
        agent = data.get("selected_agent", "")
        if agent not in VALID_AGENT_KEYS:
            return None  # invalid agent → fallback

    # Validate workflow: selected_workflow should exist if set
    if route_type == "workflow":
        workflow = data.get("selected_workflow")
        if workflow and workflow not in VALID_WORKFLOW_KEYS:
            return None  # invalid workflow → fallback

    # Validate clarify: must have a question
    if route_type == "clarify":
        if not data.get("clarification_question"):
            data["clarification_question"] = "Could you provide more detail about what you need?"

    # Validate reject: must have a reason
    if route_type == "reject":
        if not data.get("reason"):
            data["reason"] = "This request is outside the system's capabilities."

    # Ensure steps is a list
    if not isinstance(data.get("steps"), list):
        data["steps"] = []

    data["confidence"] = confidence
    return data


# ── Main entry point ──────────────────────────────────────────────────────────

def route_request(
    user_input: str,
    workspace_id: str,
    db,
    brain_context: str = "",
) -> dict | None:
    """
    Calls the LLM router to decide the execution path.

    Returns a validated routing dict on success, or None if the LLM call
    fails or returns unparseable/invalid JSON. Callers should fall back to
    legacy routing (detect_workflow → detect_agent) when None is returned.

    Return schema (on success):
        {
            "route_type":             str,   # single_agent|workflow|clarify|reject
            "confidence":             float,
            "primary_intent":         str,
            "reason":                 str,
            "selected_agent":         str|None,
            "selected_workflow":      str|None,
            "steps":                  list[dict],
            "clarification_question": str,
            "risk_flags":             list[str],
        }
    """
    try:
        conversation_context = _build_conversation_context(workspace_id, db)
        prompt = _build_router_prompt(user_input, conversation_context, brain_context)
        raw = generate_json(prompt, system_prompt=ROUTER_SYSTEM_PROMPT)
        data = json.loads(raw)
        return _validate_route(data)
    except Exception:
        # Any failure (network, JSON parse, etc.) → return None so caller
        # falls back to legacy routing. Never crash the request pipeline.
        return None
