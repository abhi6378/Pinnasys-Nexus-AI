"""
workflows/engine.py  —  All deterministic multi-step workflows
Each workflow chains helpers, passing output from one to the next.
A WorkflowStepError is raised when any step fails, halting the chain
immediately so garbage output never propagates to downstream steps.
"""
from helpers.executor import run_agent


# ── Circuit-breaker exception ─────────────────────────────────────────────────

class WorkflowStepError(Exception):
    """Raised when a workflow step's LLM call fails or returns success=False."""
    def __init__(self, label: str, agent_key: str, error_msg: str):
        self.label     = label
        self.agent_key = agent_key
        self.error_msg = error_msg
        super().__init__(error_msg)


# ── Step runner (raises on failure) ──────────────────────────────────────────

def _step(label: str, agent_key: str, input_text: str, brain_context: str) -> dict:
    result = run_agent(agent_key, input_text, brain_context)
    # If the LLM call failed, halt the chain immediately.
    # Previously this returned an error string that silently
    # became input to the next step.
    if not result.get("success", False):
        raise WorkflowStepError(label, agent_key, result["output"])
    return {
        "step":    label,
        "agent":   result.get("name", agent_key),
        "input":   input_text[:200],
        "output":  result["output"],
        "success": True,
    }


# ── Marketing Campaign Workflow ───────────────────────────────────────────────

def marketing_workflow(user_input: str, brain_context: str) -> dict:
    """
    Copywriter → SEO → Social Media
    Produces a full marketing campaign package.
    """
    steps = []
    try:
        s1 = _step(
            "Draft campaign copy", "copywriter",
            f"Create a marketing campaign for: {user_input}",
            brain_context
        )
        steps.append(s1)

        s2 = _step(
            "SEO optimization", "seo",
            f"Optimize this marketing copy for SEO:\n\n{s1['output']}",
            brain_context
        )
        steps.append(s2)

        s3 = _step(
            "Social media posts", "social_media",
            f"Convert this marketing campaign into social media posts "
            f"(Instagram, LinkedIn, Twitter/X):\n\n{s2['output']}",
            brain_context
        )
        steps.append(s3)

        final = (
            f"## CAMPAIGN COPY\n{s1['output']}\n\n"
            f"## SEO VERSION\n{s2['output']}\n\n"
            f"## SOCIAL POSTS\n{s3['output']}"
        )
        return {"workflow": "marketing_campaign", "steps": steps, "final_output": final, "error": False}

    except WorkflowStepError as e:
        return {
            "workflow":     "marketing_campaign",
            "steps":        steps,   # contains only the steps that succeeded
            "final_output": (
                f"⚠️ **Workflow halted at step '{e.label}'** (agent: `{e.agent_key}`).\n\n"
                f"Error: {e.error_msg}"
            ),
            "error": True,
        }


# ── Content Creation Workflow ─────────────────────────────────────────────────

def content_workflow(user_input: str, brain_context: str) -> dict:
    """
    Copywriter → SEO
    Blog post or article with SEO optimization.
    """
    steps = []
    try:
        s1 = _step(
            "Write content", "copywriter",
            f"Write a detailed blog post or article about: {user_input}",
            brain_context
        )
        steps.append(s1)

        s2 = _step(
            "SEO optimize content", "seo",
            f"Provide SEO recommendations and an optimized meta description for:\n\n{s1['output']}",
            brain_context
        )
        steps.append(s2)

        final = f"## ARTICLE\n{s1['output']}\n\n## SEO NOTES\n{s2['output']}"
        return {"workflow": "content_creation", "steps": steps, "final_output": final, "error": False}

    except WorkflowStepError as e:
        return {
            "workflow":     "content_creation",
            "steps":        steps,
            "final_output": (
                f"⚠️ **Workflow halted at step '{e.label}'** (agent: `{e.agent_key}`).\n\n"
                f"Error: {e.error_msg}"
            ),
            "error": True,
        }


# ── Sales Outreach Workflow ───────────────────────────────────────────────────

def sales_workflow(user_input: str, brain_context: str) -> dict:
    """
    Sales → Email Marketer
    Cold outreach sequence + email campaign.
    """
    steps = []
    try:
        s1 = _step(
            "Sales strategy", "sales",
            f"Create a sales outreach strategy for: {user_input}",
            brain_context
        )
        steps.append(s1)

        s2 = _step(
            "Email sequence", "email_marketer",
            f"Write a 3-email outreach sequence based on this strategy:\n\n{s1['output']}",
            brain_context
        )
        steps.append(s2)

        final = f"## SALES STRATEGY\n{s1['output']}\n\n## EMAIL SEQUENCE\n{s2['output']}"
        return {"workflow": "sales_outreach", "steps": steps, "final_output": final, "error": False}

    except WorkflowStepError as e:
        return {
            "workflow":     "sales_outreach",
            "steps":        steps,
            "final_output": (
                f"⚠️ **Workflow halted at step '{e.label}'** (agent: `{e.agent_key}`).\n\n"
                f"Error: {e.error_msg}"
            ),
            "error": True,
        }


# ── Support Setup Workflow ────────────────────────────────────────────────────

def support_workflow(user_input: str, brain_context: str) -> dict:
    """
    Support → Copywriter
    FAQ + support scripts + help copy.
    """
    steps = []
    try:
        s1 = _step(
            "Support scripts", "support",
            f"Create customer support scripts and FAQ answers for: {user_input}",
            brain_context
        )
        steps.append(s1)

        s2 = _step(
            "Polish support copy", "copywriter",
            f"Polish and improve this support content to be clearer and more friendly:\n\n{s1['output']}",
            brain_context
        )
        steps.append(s2)

        final = f"## SUPPORT SCRIPTS\n{s1['output']}\n\n## POLISHED VERSION\n{s2['output']}"
        return {"workflow": "support_setup", "steps": steps, "final_output": final, "error": False}

    except WorkflowStepError as e:
        return {
            "workflow":     "support_setup",
            "steps":        steps,
            "final_output": (
                f"⚠️ **Workflow halted at step '{e.label}'** (agent: `{e.agent_key}`).\n\n"
                f"Error: {e.error_msg}"
            ),
            "error": True,
        }


# ── Business Strategy Workflow ────────────────────────────────────────────────

def strategy_workflow(user_input: str, brain_context: str) -> dict:
    """
    Strategist → Data Analyst
    Full business strategy + data-backed insights.
    """
    steps = []
    try:
        s1 = _step(
            "Business strategy", "strategist",
            f"Create a business strategy for: {user_input}",
            brain_context
        )
        steps.append(s1)

        s2 = _step(
            "Data & KPI recommendations", "data_analyst",
            f"Based on this strategy, recommend KPIs and data points to track:\n\n{s1['output']}",
            brain_context
        )
        steps.append(s2)

        final = f"## STRATEGY\n{s1['output']}\n\n## KPIs & DATA\n{s2['output']}"
        return {"workflow": "business_strategy", "steps": steps, "final_output": final, "error": False}

    except WorkflowStepError as e:
        return {
            "workflow":     "business_strategy",
            "steps":        steps,
            "final_output": (
                f"⚠️ **Workflow halted at step '{e.label}'** (agent: `{e.agent_key}`).\n\n"
                f"Error: {e.error_msg}"
            ),
            "error": True,
        }


# ── Workflow Registry ─────────────────────────────────────────────────────────

WORKFLOWS = {
    "marketing_campaign": marketing_workflow,
    "content_creation":   content_workflow,
    "sales_outreach":     sales_workflow,
    "support_setup":      support_workflow,
    "business_strategy":  strategy_workflow,
}


def run_workflow(workflow_key: str, user_input: str, brain_context: str) -> dict:
    fn = WORKFLOWS.get(workflow_key)
    if not fn:
        return {
            "workflow":     workflow_key,
            "steps":        [],
            "final_output": f"Unknown workflow: '{workflow_key}'.",
            "error":        True,
        }
    return fn(user_input, brain_context)
