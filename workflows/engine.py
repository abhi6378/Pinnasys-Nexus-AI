import json
from typing import Optional, List, Dict, Any
from helpers.executor import run_agent
from sqlalchemy.orm import Session

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
        self.data = data # Contains connect_required or validation_error info
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


# ── Step runner (tool-aware) ──────────────────────────────────────────────────

def _step(
    label: str,
    agent_key: str,
    input_text: str,
    brain_context: str,
    completed_steps: list[dict],
    workspace_id: str = "",
    db: Session = None,
    resume_state: dict = None,
    current_workflow_key: str = ""
) -> dict:
    """
    Executes a step, skipping if it was already completed in resume_state.
    """
    # 1. Skip if already done
    for prev in completed_steps:
        if prev.get("step") == label:
            return prev

    # Prepare workflow state to persist if we pause
    # This will be saved in PendingToolRequest.context_json
    workflow_state = {
        "workflow_key": current_workflow_key,
        "completed_steps": completed_steps,
        "current_step": label,
        "is_retry": bool(resume_state),
    }

    # 2. Run the agent (tool-aware)
    result = run_agent(
        agent_key, 
        input_text, 
        brain_context,
        workspace_id=workspace_id,
        db=db,
        workflow_state=workflow_state
    )

    # 3. Handle interrupts (resume-required or setup-blocked workflow states)
    interrupt_modes = {
        "connect_required",
        "auth_unavailable",
        "invalid_tool",
        "validation_error",
    }
    if result.get("connect_required") or result.get("mode") in interrupt_modes:
        # Add workflow info to the interrupt data for the UI
        result["workflow"] = current_workflow_key
        result["step_label"] = label
        raise WorkflowInterrupt(label, agent_key, result)

    # 4. Handle failures
    if not result.get("success", False):
        raise WorkflowStepError(
            label,
            agent_key,
            result.get("output", "Unknown error"),
            result=result,
        )

    # 5. Return successful step data
    return {
        "step":    label,
        "agent":   result.get("name", agent_key),
        "input":   input_text[:500],
        "output":  result["output"],
        "tool_used": result.get("tool_used"),
        "tool_output": result.get("tool_output"),
        "success": True,
    }


# ── Workflow Implementations ──────────────────────────────────────────────────

def marketing_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "marketing_campaign"
    try:
        s1 = _step("Draft campaign copy", "copywriter", f"Create a marketing campaign for: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)

        s2 = _step("SEO optimization", "seo", f"Optimize this marketing copy for SEO:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)

        s3 = _step("Social media posts", "social_media", f"Convert this marketing campaign into social media posts:\n\n{_step_material(s2)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s3)

        final = f"## CAMPAIGN COPY\n{s1['output']}\n\n## SEO VERSION\n{s2['output']}\n\n## SOCIAL POSTS\n{s3['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def content_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "content_creation"
    try:
        s1 = _step("Write content", "copywriter", f"Write a detailed blog post about: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("SEO optimize content", "seo", f"SEO recommendations for:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        final = f"## ARTICLE\n{s1['output']}\n\n## SEO NOTES\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def sales_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "sales_outreach"
    try:
        s1 = _step("Sales strategy", "sales", f"Create a sales outreach strategy for: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("Email sequence", "email_marketer", f"Write outreach sequence for:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        final = f"## SALES STRATEGY\n{s1['output']}\n\n## EMAIL SEQUENCE\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def support_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "support_setup"
    try:
        s1 = _step("Support scripts", "support", f"scripts/FAQ for: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("Polish support copy", "copywriter", f"Polish this support content:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        final = f"## SUPPORT SCRIPTS\n{s1['output']}\n\n## POLISHED VERSION\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def strategy_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "business_strategy"
    try:
        s1 = _step("Business strategy", "strategist", f"Create strategy for: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("KPI recommendations", "data_analyst", f"KPIs for strategy:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        final = f"## STRATEGY\n{s1['output']}\n\n## KPIs\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

# ── Mission 11 Workflows ──────────────────────────────────────────────────────

def research_draft_send_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "research_draft_send"
    try:
        s1 = _step("Research topic", "assistant", f"Research: {user_input}. Use the Tavily search tool if live web data is needed; do not simulate search results.", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("Draft content", "copywriter", f"Draft based on research:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        s3 = _step("Send email", "assistant", f"Send this content via Gmail using a real tool call. Do not provide a manual send workaround.\n\n{_step_material(s2)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s3)
        final = f"## RESEARCH\n{s1['output']}\n\n## DRAFT\n{s2['output']}\n\n## SEND STATUS\n{s3['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def lead_capture_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "lead_capture"
    try:
        s1 = _step("Extract Lead", "sales", f"Extract results from: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("Log to HubSpot", "sales", f"Create the real HubSpot contact using a HubSpot tool. Do not suggest manual CRM entry.\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        s3 = _step("Log to Sheets", "data_analyst", f"Append the extracted lead to Google Sheets using a real Sheets tool. Do not suggest a manual spreadsheet workaround.\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s3)
        hubspot_status = "✅ Synced" if s2.get("tool_used") else "⚠️ Not synced (check HubSpot connection)"
        sheets_status = "✅ Logged" if s3.get("tool_used") else "⚠️ Not logged (check Sheets connection)"
        final = (
            f"## LEAD DATA\n{s1['output']}\n\n"
            f"## SYNC STATUS\n"
            f"- HubSpot: {hubspot_status}\n"
            f"- Google Sheets: {sheets_status}"
        )
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def email_triage_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "email_triage"
    try:
        s1 = _step("Read Emails", "assistant", f"Fetch recent Gmail messages using a real Gmail tool and triage them. Do not simulate inbox access.\n\nUser request: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step(
            "Draft Replies",
            "assistant",
            (
                "Based on the triaged emails below, create Gmail draft replies for any emails "
                "that need a response. Use GMAIL_CREATE_EMAIL_DRAFT for each reply. "
                f"Draft professional, context-aware replies.\n\n{_step_material(s1)}"
            ),
            brain_context,
            steps,
            workspace_id,
            db,
            resume_state,
            wk,
        )
        _append_step_once(steps, s2)
        final = f"## TRIAGE\n{s1['output']}\n\n## DRAFTS\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }

def competitor_research_workflow(user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    steps = _seed_steps(resume_state)
    wk = "competitor_research"
    try:
        s1 = _step("Research competitors", "strategist", f"Competitor info for: {user_input}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s1)
        s2 = _step("Generate Report", "copywriter", f"Write report based on:\n\n{_step_material(s1)}", brain_context, steps, workspace_id, db, resume_state, wk)
        _append_step_once(steps, s2)
        final = f"## RESEARCH\n{s1['output']}\n\n## REPORT\n{s2['output']}"
        return {"workflow": wk, "steps": steps, "final_output": final, "error": False}
    except WorkflowInterrupt as i:
        return {"mode": "interrupt", "interrupt": i.data, "steps": steps, "step_label": i.step_label}
    except WorkflowStepError as e:
        return {
            "workflow": wk,
            "steps": steps,
            "final_output": f"⚠️ Error at '{e.label}': {e.error_msg}",
            "error": True,
            "mode": e.result.get("mode", "workflow_error"),
            "step_label": e.label,
        }


# ── Workflow Registry ─────────────────────────────────────────────────────────

WORKFLOWS = {
    "marketing_campaign": marketing_workflow,
    "content_creation":   content_workflow,
    "sales_outreach":     sales_workflow,
    "support_setup":      support_workflow,
    "business_strategy":  strategy_workflow,
    "research_draft_send": research_draft_send_workflow,
    "lead_capture":       lead_capture_workflow,
    "email_triage":       email_triage_workflow,
    "competitor_research": competitor_research_workflow,
}

def run_workflow(workflow_key: str, user_input: str, brain_context: str, workspace_id: str = "", db: Session = None, resume_state: dict = None) -> dict:
    fn = WORKFLOWS.get(workflow_key)
    if not fn:
        return {"workflow": workflow_key, "steps": [], "final_output": f"Unknown workflow: '{workflow_key}'.", "error": True}
    return fn(user_input, brain_context, workspace_id, db, resume_state)
