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
import logging
from helpers.configs import AGENTS
from models.contracts import ApprovalRequirement, RouteDecision, RouteStepSkeleton
from workflows.engine import WORKFLOWS
from llm.client import generate_json
from storage import repositories as repo
from tools.capability_layer import summarize_agent_capabilities
from tools.tool_registry import get_capability_group_metadata
from utils.logging_utils import log_event, log_exception


logger = logging.getLogger(__name__)


# ── Router system prompt ──────────────────────────────────────────────────────

ROUTER_SYSTEM_PROMPT = """You are the routing brain of a multi-agent AI system.

Your job is to choose the single best execution path for the user's latest request.
Route based on the real end-to-end job to be done, not on superficial keywords alone.

You must choose exactly one route_type:
- single_agent: one agent can complete the request well enough
- workflow: the request clearly needs multiple sequential steps or specialists
- clarify: important routing-critical details are missing
- reject: unsafe, impossible, or outside the product scope

Core routing rules:
1. Prefer the simplest route that can succeed.
2. Use workflow only when the request truly needs multiple ordered steps, multiple specialists, or a named workflow already fits the job.
3. Use single_agent when one agent can own the request, even if that agent may later use tools.
4. Use clarify instead of guessing when a missing detail would likely cause the wrong route or a failed external action.
5. Use reject only for unsafe, impossible, or unsupported requests.
6. Never invent agents, workflows, tools, or capabilities that are not in the provided catalog.
7. Prefer capability fit over title similarity. Tool-backed responsibilities matter.
8. Distinguish text generation from live system access:
   - drafting, strategy, rewriting, or explanation often stays single_agent
   - reading or changing external systems should route to the owner of those toolkits
   - if a request can be satisfied as a draft without touching a live system, prefer the draft path
   - if the user explicitly wants execution in a live system, route to the agent or workflow that owns that capability
9. Prefer assistant for general admin and cross-functional live actions unless a specialist is clearly better.
10. Prefer specialist agents for pure domain work:
   - copywriter for copy
   - seo for SEO
   - strategist for business strategy or competitor framing
   - social_media for social content and platform actions
   - sales for CRM and outreach execution
   - data_analyst for structured logging or KPI/data work
11. Clarify when execution-critical targets are missing, such as:
   - Slack summary without channel or DM target
   - email send/draft without recipient
   - calendar event without a concrete date/time
   - GitHub issue request without repo context
   - sheet append/log request without a destination sheet if it is not inferable
12. Do not over-clarify. If the request is still routable and the chosen agent can reasonably ask a follow-up during execution, keep routing simple.
13. Missing info should only force clarify when it blocks safe execution or would likely cause the wrong route.
14. Distinguish read/discovery from write/execute:
   - when the user wants to inspect, summarize, research, or review, treat it as read or research
   - when the user wants to send, post, update, create, sync, or schedule, treat it as write or execute
15. Never assume a live action happened during routing. Routing should only decide the right path, not the final outcome.

High-priority intent rules:
- Inbox-access, unread-email review, inbox triage, or draft-reply requests should route to email_triage unless a simpler Gmail-capable single-agent route is clearly better.
- Research whose end goal is outreach or sending an email should route to research_draft_send.
- Capturing lead data from text and syncing it into systems should route to lead_capture.
- Competitor research plus a report should prefer competitor_research.

Output requirements:
- Return JSON only. No markdown, no prose outside the JSON object.
- Be decisive but conservative. If confidence is low, choose clarify.
- Use capability-oriented labels that can generalize to future tools and connectors.

Output schema:
{
  "route_type": "single_agent | workflow | clarify | reject",
  "confidence": 0.0-1.0,
  "primary_intent": "short label",
  "intent": "short label or same as primary_intent",
  "domain": "broad business domain such as communication, research, crm, productivity, content",
  "system_family": "email | slack | calendar | crm | sheets | github | research | social | content | strategy | general",
  "operation": "read | write | draft | analyze | research | schedule | execute | publish",
  "requires_live_data": true,
  "approval_required": false,
  "missing_info": ["optional list of routing-critical missing details"],
  "reason": "short routing reason",
  "selected_agent": "agent_key or null",
  "selected_workflow": "workflow_key or null",
  "steps": [
    {
      "agent": "agent_key",
      "task": "short task description",
      "system_family": "optional system family",
      "operation": "optional operation",
      "capability_group": "optional capability group"
    }
  ],
  "clarification_question": "string or empty",
  "risk_flags": ["optional list of short strings"]
}

Validation rules:
- If route_type = single_agent, selected_agent must be set and steps must contain exactly one step.
- If route_type = workflow, selected_workflow should be set when a catalog workflow fits, and steps must reflect the ordered execution.
- If route_type = clarify, clarification_question must be filled and must ask only for the missing routing-critical detail.
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
    "research_draft_send": {
        "title": "Research & Outreach",
        "description": "Research a topic → Draft an email based on findings → Send it via Gmail",
        "agents": ["assistant", "copywriter"],
    },
    "lead_capture": {
        "title": "Lead Capture Sync",
        "description": "Extract lead info → Sync to HubSpot CRM → Append row to Google Sheets",
        "agents": ["sales", "data_analyst"],
    },
    "email_triage": {
        "title": "Email Triage",
        "description": "Review recent emails → Summarize them → Draft replies",
        "agents": ["assistant", "assistant"],
    },
    "competitor_research": {
        "title": "Competitor Insight",
        "description": "Research main competitors → Generate competitive analysis and report",
        "agents": ["strategist", "copywriter"],
    },
}


# ── Build the prompt payload ──────────────────────────────────────────────────

def _build_agent_catalog() -> str:
    """Format all agents as a compact catalog for the router."""
    lines = []
    for key, info in AGENTS.items():
        use_cases = ", ".join(info.get("use_cases", [])[:3])
        capability_summary = summarize_agent_capabilities(key, agent_config=info)
        capability_groups = ", ".join(capability_summary.get("capability_groups", [])) or "text_only"
        toolkits = ", ".join(capability_summary.get("toolkits", [])) or "none"
        lines.append(
            f"- {key}: {info['name']} — {info['role']}. "
            f"Goal: {info['goal']} "
            f"Use cases: {use_cases}. "
            f"Capability groups: {capability_groups}. "
            f"Toolkits: {toolkits}."
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


def _build_routing_hints() -> str:
    """Provide compact intent-first routing hints."""
    return "\n".join([
        "- Route from the user's end goal first, not surface keywords alone.",
        "- Use capability ownership to break ties when multiple agents sound plausible.",
        "- Prefer workflows only for multi-step handoffs or known workflow fits.",
        "- Prefer clarify when a missing target would likely make a live action fail.",
        "- Draft, explain, summarize, and rewrite requests often stay text-first even when related live tools exist.",
        "- Read or discovery requests should not be treated as write requests.",
        "- Do not require clarification for details that the selected agent can safely infer or ask later during execution.",
        "- Keep legacy keyword behavior as a safety net, not as the main reasoning path.",
    ])


def _truncate_context(text: str, limit: int = 1500) -> str:
    """Trim long memory context at a sentence or paragraph boundary when possible."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    last_break = max(truncated.rfind("\n"), truncated.rfind(". "))
    if last_break > limit * 0.6:
        return truncated[:last_break + 1]
    return f"{truncated}..."


def _build_conversation_context(workspace_id: str, db, limit: int = 5) -> str:
    """Fetch recent conversation turns to give the router conversational memory."""
    try:
        rows = repo.get_conversations(db, workspace_id, limit=limit)
        if not rows:
            return "No previous conversations."
        parts = []
        for row in reversed(rows):  # oldest first
            output = str(row.output or "")
            # Skip poisoning the router with previous tool/auth/validation failures
            lowered_output = output.lower()
            if (
                ("i can't process this request" in lowered_output and "unavailable" in lowered_output)
                or "needs access to" in lowered_output
                or "requested an invalid tool" in lowered_output
                or "tried to use" in lowered_output
                or lowered_output.startswith("error:")
            ):
                continue
            parts.append(f"User: {row.input[:150]}")
            parts.append(f"{row.helper}: {output[:150]}")
        if not parts:
            return "No previous conversations."
        return "\n".join(parts)
    except Exception as exc:
        log_exception(
            logger,
            "router.conversation_context_failed",
            exc,
            workspace_id=workspace_id,
        )
        return "Conversation history unavailable."


def _build_router_prompt(
    user_input: str,
    conversation_context: str,
    memory_context: str,
    connector_context: dict | None = None,
) -> str:
    """Assemble the full user-side prompt sent to the LLM for routing."""
    agent_catalog = _build_agent_catalog()
    workflow_catalog = _build_workflow_catalog()
    routing_hints = _build_routing_hints()

    connector = connector_context or {}
    connector_block = "Auto connector mode."
    if connector.get("mode") == "manual" and connector.get("selected_toolkit"):
        connector_block = (
            f"Manual connector selected: {connector.get('selected_toolkit')}. "
            f"enforce_toolkit={bool(connector.get('enforce_toolkit'))}. "
            f"selected_account_id={connector.get('selected_account_id', '') or 'none'}."
        )

    return f"""=== USER REQUEST ===
{user_input}

=== CONVERSATION CONTEXT ===
{conversation_context}

=== MEMORY CONTEXT ===
{_truncate_context(memory_context) if memory_context else "No memory context available."}

=== CONNECTOR CONTEXT ===
{connector_block}

=== AVAILABLE AGENTS ===
{agent_catalog}

=== AVAILABLE WORKFLOWS ===
{workflow_catalog}

=== ROUTING HINTS ===
{routing_hints}

Decide the best route. Return JSON only."""


# ── Validation ────────────────────────────────────────────────────────────────

VALID_ROUTE_TYPES = {"single_agent", "workflow", "clarify", "reject"}
VALID_AGENT_KEYS = set(AGENTS.keys())
VALID_WORKFLOW_KEYS = set(WORKFLOWS.keys())

# If confidence is below this threshold, override to "clarify"
CONFIDENCE_THRESHOLD = 0.4


def _normalize_missing_info(value, clarification_question: str) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if clarification_question.strip():
        return [clarification_question.strip()]
    return []


def _infer_route_system_family(
    intent: str,
    selected_workflow: str | None,
    selected_agent: str | None,
    risk_flags: list[str],
) -> str:
    lowered = f"{intent} {selected_workflow or ''} {selected_agent or ''} {' '.join(risk_flags)}".lower()
    mapping = {
        "email": ("email", "gmail", "inbox"),
        "slack": ("slack", "channel", "dm"),
        "calendar": ("calendar", "meeting", "schedule"),
        "crm": ("crm", "hubspot", "lead"),
        "sheets": ("sheet", "spreadsheet"),
        "github": ("github", "issue", "repo"),
        "research": ("research", "competitor"),
        "social": ("social", "twitter", "linkedin"),
        "content": ("copy", "content", "blog"),
        "strategy": ("strategy", "business"),
    }
    for family, keywords in mapping.items():
        if any(keyword in lowered for keyword in keywords):
            return family
    return "general"


def _infer_operation(intent: str, risk_flags: list[str], selected_workflow: str | None) -> str:
    lowered = f"{intent} {selected_workflow or ''} {' '.join(risk_flags)}".lower()
    if any(token in lowered for token in ("draft", "triage")):
        return "draft"
    if any(token in lowered for token in ("send", "create", "capture", "log", "sync", "publish")):
        return "write"
    if any(token in lowered for token in ("research", "competitor")):
        return "research"
    if any(token in lowered for token in ("schedule", "calendar")):
        return "schedule"
    if any(token in lowered for token in ("analyze", "analysis", "report", "strategy")):
        return "analyze"
    return "read"


def _infer_domain(system_family: str, selected_agent: str | None) -> str:
    family_meta = get_capability_group_metadata(system_family)
    if family_meta.get("label"):
        return str(family_meta["label"])
    if selected_agent in {"copywriter", "seo", "social_media"}:
        return "content"
    if selected_agent in {"strategist", "data_analyst"}:
        return "analysis"
    return "general"


def _normalize_ordered_steps(value) -> list[RouteStepSkeleton]:
    steps: list[RouteStepSkeleton] = []
    if not isinstance(value, list):
        return steps
    for item in value:
        step = RouteStepSkeleton.from_value(item)
        if step:
            steps.append(step)
    return steps


def _normalize_approval_requirement(value, *, operation: str, risk_flags: list[str]) -> ApprovalRequirement:
    if isinstance(value, dict):
        return ApprovalRequirement.from_value(value)
    required = bool(value) or operation in {"write", "schedule", "publish"} or any(
        flag in {"write_action", "external_write", "live_action"} for flag in risk_flags
    )
    risk_level = "high" if required else ("medium" if operation in {"draft", "analyze"} else "low")
    return ApprovalRequirement(
        required=required,
        risk_level=risk_level,
        reason="Live external write action." if required else "",
        categories=list(risk_flags),
    )


def _normalize_router_decision(data: dict) -> RouteDecision | None:
    """Normalize router output into an internal structured decision."""
    if not isinstance(data, dict):
        return None

    clarification_question = str(data.get("clarification_question", "") or "").strip()
    intent = str(data.get("intent") or data.get("primary_intent") or "").strip()
    selected_agent = data.get("agent") or data.get("selected_agent")
    selected_workflow = data.get("workflow") or data.get("selected_workflow")
    risk_flags = [
        str(flag).strip() for flag in (data.get("risk_flags") or [])
        if str(flag).strip()
    ]
    system_family = str(data.get("system_family", "") or "").strip() or _infer_route_system_family(
        intent,
        selected_workflow,
        selected_agent,
        risk_flags,
    )
    operation = str(data.get("operation", "") or "").strip() or _infer_operation(
        intent,
        risk_flags,
        selected_workflow,
    )
    requires_live_data = bool(
        data.get("requires_live_data")
        if "requires_live_data" in data
        else system_family in {"email", "slack", "calendar", "crm", "sheets", "github", "research", "social"}
        or operation in {"read", "research", "write", "schedule"}
    )
    return RouteDecision(
        route_type=str(data.get("route_type", "") or "").strip(),
        confidence=data.get("confidence", 0.5),
        intent=intent,
        domain=str(data.get("domain", "") or "").strip() or _infer_domain(system_family, selected_agent),
        system_family=system_family,
        operation=operation,
        requires_live_data=requires_live_data,
        approval_required=_normalize_approval_requirement(
            data.get("approval_requirement", data.get("approval_required")),
            operation=operation,
            risk_flags=risk_flags,
        ),
        selected_agent=selected_agent,
        selected_workflow=selected_workflow,
        missing_info=_normalize_missing_info(data.get("missing_info"), clarification_question),
        reason=str(data.get("reason", "") or "").strip(),
        ordered_steps=_normalize_ordered_steps(data.get("steps", [])),
        clarification_question=clarification_question,
        risk_flags=risk_flags,
        route_method=str(data.get("route_method", "llm_router") or "llm_router"),
    )


def _validate_route(decision: RouteDecision) -> RouteDecision | None:
    """
    Validates the router JSON. Returns a cleaned dict or None if invalid.
    Applies safety overrides (e.g. low-confidence → clarify).
    """
    route_type = decision.route_type
    if route_type not in VALID_ROUTE_TYPES:
        return None

    confidence = decision.confidence
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5

    # Low-confidence override: prefer clarify over guessing
    if confidence < CONFIDENCE_THRESHOLD and route_type in ("single_agent", "workflow"):
        updated = decision.replace(**{
            "route_type": "clarify",
            "confidence": confidence,
            "reason": f"Low confidence ({confidence:.2f}). Asking for clarification.",
            "clarification_question": decision.clarification_question or "Could you provide more detail about what you need?",
        })
        return updated

    # Validate single_agent: selected_agent must exist
    if route_type == "single_agent":
        if decision.selected_agent not in VALID_AGENT_KEYS:
            return None  # invalid agent → fallback

    # Validate workflow: selected_workflow should exist if set
    if route_type == "workflow":
        if decision.selected_workflow and decision.selected_workflow not in VALID_WORKFLOW_KEYS:
            return None  # invalid workflow → fallback

    # Validate clarify: must have a question
    if route_type == "clarify":
        decision = decision.replace(**{
            "clarification_question": decision.clarification_question or "Could you provide more detail about what you need?",
        })

    # Validate reject: must have a reason
    if route_type == "reject":
        decision = decision.replace(**{
            "reason": decision.reason or "This request is outside the system's capabilities.",
        })

    return decision.replace(confidence=confidence, route_method=decision.route_method or "llm_router")


# ── Main entry point ──────────────────────────────────────────────────────────

def route_request_decision(
    user_input: str,
    workspace_id: str,
    db,
    brain_context: str = "",
    connector_context: dict | None = None,
) -> RouteDecision | None:
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
    log_event(
        logger,
        logging.INFO,
        "router.enter",
        workspace_id=workspace_id,
        has_brain_context=bool(brain_context),
    )
    try:
        conversation_context = _build_conversation_context(workspace_id, db)
        prompt = _build_router_prompt(user_input, conversation_context, brain_context, connector_context=connector_context)
        raw = generate_json(prompt, system_prompt=ROUTER_SYSTEM_PROMPT)
        data = json.loads(raw)
        decision = _normalize_router_decision(data)
        validated = _validate_route(decision) if decision else None
        if decision:
            log_event(
                logger,
                logging.INFO,
                "router.decision",
                workspace_id=workspace_id,
                route_type=decision.route_type,
                intent=decision.intent,
                agent_name=decision.selected_agent or "",
                workflow_name=decision.selected_workflow or "",
                confidence=validated.confidence if validated else decision.confidence,
                missing_info=decision.missing_info,
                system_family=decision.system_family,
                operation=decision.operation,
            )
        log_event(
            logger,
            logging.INFO,
            "router.exit",
            workspace_id=workspace_id,
            route_type=validated.route_type if validated else "fallback",
            agent_name=validated.selected_agent if validated else "",
            workflow_name=validated.selected_workflow if validated else "",
            route_method=validated.route_method if validated else "",
        )
        return validated
    except Exception as exc:
        log_exception(
            logger,
            "router.failed",
            exc,
            workspace_id=workspace_id,
        )
        # Any failure (network, JSON parse, etc.) → return None so caller
        # falls back to legacy routing. Never crash the request pipeline.
        return None


def route_request(
    user_input: str,
    workspace_id: str,
    db,
    brain_context: str = "",
    connector_context: dict | None = None,
) -> dict | None:
    decision = route_request_decision(user_input, workspace_id, db, brain_context, connector_context=connector_context)
    if not decision:
        return None
    payload = decision.to_legacy_dict()
    if connector_context:
        payload["connector_context"] = dict(connector_context)
    return payload
