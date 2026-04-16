import json
from dataclasses import dataclass
from typing import Callable

try:
    from sqlalchemy.orm import Session
except Exception:  # pragma: no cover - fallback for constrained test environments
    Session = object

from helpers.executor import run_agent
from models.contracts import CapabilityRequest, WorkflowStepResult, WorkflowStepSpec


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    steps: tuple[WorkflowStepSpec, ...]
    finalizer: Callable[[list[dict]], str]


# ── Exceptions ────────────────────────────────────────────────────────────────

class WorkflowStepError(Exception):
    """Raised when a workflow step fails completely."""

    def __init__(self, label: str, agent_key: str, error_msg: str, result: dict | None = None):
        self.label = label
        self.agent_key = agent_key
        self.error_msg = error_msg
        self.result = result or {}
        super().__init__(error_msg)


class WorkflowInterrupt(Exception):
    """Raised when a workflow must pause for user action (auth or validation)."""

    def __init__(self, step_label: str, agent_key: str, data: dict):
        self.step_label = step_label
        self.agent_key = agent_key
        self.data = data
        super().__init__(f"Workflow interrupted at {step_label}")


def _seed_steps(resume_state: dict | None) -> list[dict]:
    """Start a workflow with any previously completed steps restored."""
    if not resume_state:
        return []
    return list(resume_state.get("completed_steps", []))


def _append_step_once(steps: list[dict], step_data: dict) -> None:
    """Prevent duplicate entries when resuming an already-completed step."""
    label = step_data.get("step")
    if any(existing.get("step") == label for existing in steps):
        return
    steps.append(step_data)


def _step_material(step_data: dict) -> str:
    """
    Build the handoff content for the next workflow step.

    When a step used a real tool, we include a compact structured dump so the
    next step can work from actual tool output, not only a human summary.
    """
    parts = [step_data.get("output", "")]
    tool_output = step_data.get("tool_output")
    if tool_output:
        serialized = json.dumps(tool_output, indent=2, default=str)
        parts.append(f"Structured tool output:\n```json\n{serialized[:3000]}\n```")
    return "\n\n".join(part for part in parts if part)


def _find_step(steps: list[dict], label: str) -> dict:
    for step in reversed(steps):
        if step.get("step") == label:
            return step
    return {}


def _step(
    label: str,
    agent_key: str,
    input_text: str,
    brain_context: str,
    completed_steps: list[dict],
    workspace_id: str = "",
    db: Session = None,
    resume_state: dict = None,
    current_workflow_key: str = "",
    capability_hint: CapabilityRequest | None = None,
    connector_context: dict | None = None,
) -> dict:
    """
    Executes a step, skipping if it was already completed in resume_state.
    """
    for prev in completed_steps:
        if prev.get("step") == label:
            return prev

    workflow_state = {
        "workflow_key": current_workflow_key,
        "completed_steps": completed_steps,
        "current_step": label,
        "is_retry": bool(resume_state),
    }
    if capability_hint:
        workflow_state["capability_hint"] = capability_hint.to_dict()

    result = run_agent(
        agent_key,
        input_text,
        brain_context,
        workspace_id=workspace_id,
        db=db,
        workflow_state=workflow_state,
        connector_context=connector_context,
    )

    interrupt_modes = {
        "connect_required",
        "auth_unavailable",
        "invalid_tool",
        "validation_error",
    }
    if result.get("connect_required") or result.get("mode") in interrupt_modes:
        result["workflow"] = current_workflow_key
        result["step_label"] = label
        raise WorkflowInterrupt(label, agent_key, result)

    if not result.get("success", False):
        raise WorkflowStepError(
            label,
            agent_key,
            result.get("output", "Unknown error"),
            result=result,
        )

    return WorkflowStepResult(
        step=label,
        agent=result.get("name", agent_key),
        input=input_text[:500],
        output=result["output"],
        tool_used=result.get("tool_used"),
        tool_output=result.get("tool_output"),
        success=True,
    ).to_dict()


def _run_workflow_definition(
    definition: WorkflowDefinition,
    user_input: str,
    brain_context: str,
    workspace_id: str = "",
    db: Session = None,
    resume_state: dict = None,
    connector_context: dict | None = None,
) -> dict:
    steps = _seed_steps(resume_state)
    runtime = {
        "workflow_key": definition.key,
        "user_input": user_input,
    }
    try:
        for spec in definition.steps:
            input_text = spec.prompt_builder(user_input, steps, runtime)
            step_result = _step(
                spec.label,
                spec.agent_key,
                input_text,
                brain_context,
                steps,
                workspace_id,
                db,
                resume_state,
                definition.key,
                capability_hint=spec.capability_hint,
                connector_context=connector_context,
            )
            _append_step_once(steps, step_result)

        final = definition.finalizer(steps)
        return {
            "workflow": definition.key,
            "steps": steps,
            "final_output": final,
            "error": False,
        }
    except WorkflowInterrupt as interrupt:
        return {
            "mode": "interrupt",
            "interrupt": interrupt.data,
            "steps": steps,
            "step_label": interrupt.step_label,
        }
    except WorkflowStepError as exc:
        return {
            "workflow": definition.key,
            "steps": steps,
            "final_output": f"⚠️ Error at '{exc.label}': {exc.error_msg}",
            "error": True,
            "mode": exc.result.get("mode", "workflow_error"),
            "step_label": exc.label,
        }


def _sectioned_output(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"## {title}\n{content}" for title, content in sections)


def _live_capability_instruction(
    *,
    action: str,
    system_label: str,
    discovery_first: bool = False,
    allow_draft: bool = False,
) -> str:
    parts = [f"Use a verified {system_label} capability for this {action}."]
    if discovery_first:
        parts.append("Prefer read or discovery before write when the exact target is unclear.")
    if allow_draft:
        parts.append("Prefer drafting over sending if execution details are still ambiguous.")
    parts.append("Do not simulate live system access or offer a fake manual success path.")
    return " ".join(parts)


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "marketing_campaign": WorkflowDefinition(
        key="marketing_campaign",
        steps=(
            WorkflowStepSpec(
                label="Draft campaign copy",
                agent_key="copywriter",
                prompt_builder=lambda user_input, steps, runtime: f"Create a marketing campaign for: {user_input}",
            ),
            WorkflowStepSpec(
                label="SEO optimization",
                agent_key="seo",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Optimize this marketing copy for SEO:\n\n{_step_material(_find_step(steps, 'Draft campaign copy'))}"
                ),
            ),
            WorkflowStepSpec(
                label="Social media posts",
                agent_key="social_media",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Convert this marketing campaign into social media posts:\n\n{_step_material(_find_step(steps, 'SEO optimization'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("CAMPAIGN COPY", _find_step(steps, "Draft campaign copy").get("output", "")),
                ("SEO VERSION", _find_step(steps, "SEO optimization").get("output", "")),
                ("SOCIAL POSTS", _find_step(steps, "Social media posts").get("output", "")),
            ]
        ),
    ),
    "content_creation": WorkflowDefinition(
        key="content_creation",
        steps=(
            WorkflowStepSpec(
                label="Write content",
                agent_key="copywriter",
                prompt_builder=lambda user_input, steps, runtime: f"Write a detailed blog post about: {user_input}",
            ),
            WorkflowStepSpec(
                label="SEO optimize content",
                agent_key="seo",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"SEO recommendations for:\n\n{_step_material(_find_step(steps, 'Write content'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("ARTICLE", _find_step(steps, "Write content").get("output", "")),
                ("SEO NOTES", _find_step(steps, "SEO optimize content").get("output", "")),
            ]
        ),
    ),
    "sales_outreach": WorkflowDefinition(
        key="sales_outreach",
        steps=(
            WorkflowStepSpec(
                label="Sales strategy",
                agent_key="sales",
                prompt_builder=lambda user_input, steps, runtime: f"Create a sales outreach strategy for: {user_input}",
            ),
            WorkflowStepSpec(
                label="Email sequence",
                agent_key="email_marketer",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Write outreach sequence for:\n\n{_step_material(_find_step(steps, 'Sales strategy'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("SALES STRATEGY", _find_step(steps, "Sales strategy").get("output", "")),
                ("EMAIL SEQUENCE", _find_step(steps, "Email sequence").get("output", "")),
            ]
        ),
    ),
    "support_setup": WorkflowDefinition(
        key="support_setup",
        steps=(
            WorkflowStepSpec(
                label="Support scripts",
                agent_key="support",
                prompt_builder=lambda user_input, steps, runtime: f"scripts/FAQ for: {user_input}",
            ),
            WorkflowStepSpec(
                label="Polish support copy",
                agent_key="copywriter",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Polish this support content:\n\n{_step_material(_find_step(steps, 'Support scripts'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("SUPPORT SCRIPTS", _find_step(steps, "Support scripts").get("output", "")),
                ("POLISHED VERSION", _find_step(steps, "Polish support copy").get("output", "")),
            ]
        ),
    ),
    "business_strategy": WorkflowDefinition(
        key="business_strategy",
        steps=(
            WorkflowStepSpec(
                label="Business strategy",
                agent_key="strategist",
                prompt_builder=lambda user_input, steps, runtime: f"Create strategy for: {user_input}",
            ),
            WorkflowStepSpec(
                label="KPI recommendations",
                agent_key="data_analyst",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"KPIs for strategy:\n\n{_step_material(_find_step(steps, 'Business strategy'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("STRATEGY", _find_step(steps, "Business strategy").get("output", "")),
                ("KPIs", _find_step(steps, "KPI recommendations").get("output", "")),
            ]
        ),
    ),
    "research_draft_send": WorkflowDefinition(
        key="research_draft_send",
        steps=(
            WorkflowStepSpec(
                label="Research topic",
                agent_key="assistant",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Research: {user_input}. "
                    f"{_live_capability_instruction(action='research task', system_label='web research', discovery_first=False)}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="research",
                    toolkit_family="TAVILY",
                    action_class="search",
                    operation="research",
                    requires_live_data=True,
                    preferred_tools=["TAVILY_SEARCH"],
                ),
            ),
            WorkflowStepSpec(
                label="Draft content",
                agent_key="copywriter",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Draft based on research:\n\n{_step_material(_find_step(steps, 'Research topic'))}"
                ),
            ),
            WorkflowStepSpec(
                label="Send email",
                agent_key="assistant",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"{_live_capability_instruction(action='email action', system_label='email', discovery_first=True, allow_draft=True)}\n\n"
                    f"{_step_material(_find_step(steps, 'Draft content'))}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="email",
                    toolkit_family="GMAIL",
                    action_class="send",
                    operation="write",
                    requires_live_data=True,
                    preferred_tools=["GMAIL_SEND_EMAIL"],
                    fallback_tools=["GMAIL_CREATE_EMAIL_DRAFT"],
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("RESEARCH", _find_step(steps, "Research topic").get("output", "")),
                ("DRAFT", _find_step(steps, "Draft content").get("output", "")),
                ("SEND STATUS", _find_step(steps, "Send email").get("output", "")),
            ]
        ),
    ),
    "lead_capture": WorkflowDefinition(
        key="lead_capture",
        steps=(
            WorkflowStepSpec(
                label="Extract Lead",
                agent_key="sales",
                prompt_builder=lambda user_input, steps, runtime: f"Extract results from: {user_input}",
            ),
            WorkflowStepSpec(
                label="Log to HubSpot",
                agent_key="sales",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"{_live_capability_instruction(action='CRM sync', system_label='CRM', discovery_first=True)}\n\n"
                    f"{_step_material(_find_step(steps, 'Extract Lead'))}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="crm",
                    toolkit_family="HUBSPOT",
                    action_class="create",
                    operation="write",
                    requires_live_data=True,
                    preferred_tools=["HUBSPOT_CREATE_CONTACT"],
                ),
            ),
            WorkflowStepSpec(
                label="Log to Sheets",
                agent_key="data_analyst",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"{_live_capability_instruction(action='spreadsheet update', system_label='spreadsheet', discovery_first=True)}\n\n"
                    f"{_step_material(_find_step(steps, 'Extract Lead'))}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="sheets",
                    toolkit_family="GOOGLE_SHEETS",
                    action_class="append",
                    operation="write",
                    requires_live_data=True,
                    preferred_tools=["GOOGLESHEETS_CREATE_SPREADSHEET_ROW"],
                    fallback_tools=["GOOGLESHEETS_BATCH_UPDATE_VALUES"],
                ),
            ),
        ),
        finalizer=lambda steps: (
            f"## LEAD DATA\n{_find_step(steps, 'Extract Lead').get('output', '')}\n\n"
            "## SYNC STATUS\n"
            f"- HubSpot: {'✅ Synced' if _find_step(steps, 'Log to HubSpot').get('tool_used') else '⚠️ Not synced (check HubSpot connection)'}\n"
            f"- Google Sheets: {'✅ Logged' if _find_step(steps, 'Log to Sheets').get('tool_used') else '⚠️ Not logged (check Sheets connection)'}"
        ),
    ),
    "email_triage": WorkflowDefinition(
        key="email_triage",
        steps=(
            WorkflowStepSpec(
                label="Read Emails",
                agent_key="assistant",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"{_live_capability_instruction(action='inbox triage', system_label='email inbox')} "
                    "Read recent messages and triage them.\n\n"
                    f"User request: {user_input}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="email",
                    toolkit_family="GMAIL",
                    action_class="read",
                    operation="read",
                    requires_live_data=True,
                    preferred_tools=["GMAIL_FETCH_EMAILS"],
                ),
            ),
            WorkflowStepSpec(
                label="Draft Replies",
                agent_key="assistant",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"{_live_capability_instruction(action='reply drafting', system_label='email', allow_draft=True)} "
                    f"Draft professional, context-aware replies for emails that need a response.\n\n{_step_material(_find_step(steps, 'Read Emails'))}"
                ),
                capability_hint=CapabilityRequest(
                    capability_group="email",
                    toolkit_family="GMAIL",
                    action_class="draft",
                    operation="draft",
                    requires_live_data=True,
                    preferred_tools=["GMAIL_CREATE_EMAIL_DRAFT"],
                    fallback_tools=["GMAIL_SEND_EMAIL"],
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("TRIAGE", _find_step(steps, "Read Emails").get("output", "")),
                ("DRAFTS", _find_step(steps, "Draft Replies").get("output", "")),
            ]
        ),
    ),
    "competitor_research": WorkflowDefinition(
        key="competitor_research",
        steps=(
            WorkflowStepSpec(
                label="Research competitors",
                agent_key="strategist",
                prompt_builder=lambda user_input, steps, runtime: f"Competitor info for: {user_input}",
            ),
            WorkflowStepSpec(
                label="Generate Report",
                agent_key="copywriter",
                prompt_builder=lambda user_input, steps, runtime: (
                    f"Write report based on:\n\n{_step_material(_find_step(steps, 'Research competitors'))}"
                ),
            ),
        ),
        finalizer=lambda steps: _sectioned_output(
            [
                ("RESEARCH", _find_step(steps, "Research competitors").get("output", "")),
                ("REPORT", _find_step(steps, "Generate Report").get("output", "")),
            ]
        ),
    ),
}


def marketing_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["marketing_campaign"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def content_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["content_creation"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def sales_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["sales_outreach"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def support_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["support_setup"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def strategy_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["business_strategy"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def research_draft_send_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["research_draft_send"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def lead_capture_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["lead_capture"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def email_triage_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["email_triage"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


def competitor_research_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    return _run_workflow_definition(WORKFLOW_DEFINITIONS["competitor_research"], user_input, brain_context, workspace_id, db, resume_state, connector_context)


WORKFLOWS = {
    "marketing_campaign": marketing_workflow,
    "content_creation": content_workflow,
    "sales_outreach": sales_workflow,
    "support_setup": support_workflow,
    "business_strategy": strategy_workflow,
    "research_draft_send": research_draft_send_workflow,
    "lead_capture": lead_capture_workflow,
    "email_triage": email_triage_workflow,
    "competitor_research": competitor_research_workflow,
}


def run_workflow(workflow_key: str, user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None, connector_context: dict | None = None) -> dict:
    fn = WORKFLOWS.get(workflow_key)
    if not fn:
        return {
            "workflow": workflow_key,
            "steps": [],
            "final_output": f"Unknown workflow: '{workflow_key}'.",
            "error": True,
        }
    return fn(user_input, brain_context, workspace_id, db, resume_state, connector_context)
