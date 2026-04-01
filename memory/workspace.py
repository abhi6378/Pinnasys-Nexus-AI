# ============================================================
# memory/workspace.py
# ------------------------------------------------------------
# THE BRAIN AI — Shared memory accessible by ALL agents.
#
# This is what separates a real multi-agent system from
# 12 independent chatbots. Every agent reads from and writes
# to the same workspace. When the SEO agent optimizes content
# written by the Copywriter, it reads that content from here.
#
# Structure:
#   business_context  : company name, industry, target audience
#   brand_tone        : how the brand communicates
#   documents         : any uploaded docs / reference material
#   agent_outputs     : results produced by each agent this session
#   workflow_context  : data passed between agents in a workflow
#   conversation_log  : full history of all agent interactions
# ============================================================

from datetime import datetime


class WorkspaceMemory:
    """
    Central shared memory store for all agents.
    Think of it as a shared Google Doc that every agent can read and write.
    """

    def __init__(self):
        # ── Business context (set once by user or admin) ──────
        self.business_context = {
            "company_name": "",
            "industry": "",
            "target_audience": "",
            "product_service": "",
            "usp": "",              # unique selling proposition
            "competitors": [],
        }

        # ── Brand voice ───────────────────────────────────────
        self.brand_tone = {
            "overall_tone": "",
            "keywords_to_use": [],
            "keywords_to_avoid": [],
            "writing_style": "",
        }

        # ── Agent outputs (keyed by agent name) ───────────────
        # Stores the most recent output from each agent.
        # Workflow engine reads from here to chain agents.
        self.agent_outputs = {}

        # ── Workflow context (temp, cleared after workflow) ───
        # Data passed between agents during a multi-step workflow.
        self.workflow_context = {}

        # ── Documents / reference material ───────────────────
        self.documents = {}

        # ── Full interaction log ───────────────────────────────
        self.interaction_log = []

    # ── Business Context ──────────────────────────────────────

    def set_business_context(self, **kwargs):
        """Update business context fields."""
        for key, value in kwargs.items():
            if key in self.business_context:
                self.business_context[key] = value

    def get_business_context_string(self) -> str:
        """
        Returns business context as a formatted string.
        This gets injected into every agent's system prompt
        so all agents know about the business.
        """
        ctx = self.business_context
        if not any(ctx.values()):
            return ""

        parts = []
        if ctx["company_name"]:
            parts.append(f"Company: {ctx['company_name']}")
        if ctx["industry"]:
            parts.append(f"Industry: {ctx['industry']}")
        if ctx["target_audience"]:
            parts.append(f"Target audience: {ctx['target_audience']}")
        if ctx["product_service"]:
            parts.append(f"Product/Service: {ctx['product_service']}")
        if ctx["usp"]:
            parts.append(f"Unique value proposition: {ctx['usp']}")
        if ctx["brand_tone"]["overall_tone"]:
            parts.append(f"Brand tone: {ctx['brand_tone']['overall_tone']}")

        return "BUSINESS CONTEXT:\n" + "\n".join(parts) if parts else ""

    # ── Agent Outputs ─────────────────────────────────────────

    def save_agent_output(self, agent_name: str, output: str, task: str = ""):
        """
        Saves an agent's output to shared memory.
        Other agents in a workflow chain read from here.
        """
        self.agent_outputs[agent_name] = {
            "output": output,
            "task": task,
            "timestamp": datetime.now().isoformat(),
        }
        # Also log it
        self.interaction_log.append({
            "type": "agent_output",
            "agent": agent_name,
            "task": task,
            "output_preview": output[:200] + "..." if len(output) > 200 else output,
            "timestamp": datetime.now().isoformat(),
        })

    def get_agent_output(self, agent_name: str) -> str:
        """Returns the most recent output from a specific agent."""
        entry = self.agent_outputs.get(agent_name)
        return entry["output"] if entry else ""

    def get_all_agent_outputs_summary(self) -> str:
        """
        Returns a summary of all agent outputs.
        Used to give an agent full context of what others have done.
        """
        if not self.agent_outputs:
            return ""
        parts = ["PREVIOUS AGENT OUTPUTS IN THIS SESSION:"]
        for agent, data in self.agent_outputs.items():
            parts.append(f"\n[{agent}] ({data['task']}):\n{data['output'][:300]}...")
        return "\n".join(parts)

    # ── Workflow Context ──────────────────────────────────────

    def set_workflow_context(self, key: str, value):
        """Store a value in the active workflow's context."""
        self.workflow_context[key] = value

    def get_workflow_context(self, key: str, default=None):
        """Retrieve a value from workflow context."""
        return self.workflow_context.get(key, default)

    def clear_workflow_context(self):
        """Clear workflow context after workflow completes."""
        self.workflow_context = {}

    # ── Documents ─────────────────────────────────────────────

    def add_document(self, name: str, content: str):
        """Add a reference document all agents can read."""
        self.documents[name] = {
            "content": content,
            "added_at": datetime.now().isoformat(),
        }

    def get_document(self, name: str) -> str:
        doc = self.documents.get(name)
        return doc["content"] if doc else ""

    def list_documents(self) -> list:
        return list(self.documents.keys())

    # ── Interaction Log ───────────────────────────────────────

    def log_user_message(self, agent_name: str, message: str):
        self.interaction_log.append({
            "type": "user_message",
            "agent": agent_name,
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })

    def get_recent_log(self, n: int = 10) -> list:
        return self.interaction_log[-n:]
