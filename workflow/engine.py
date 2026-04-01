# ============================================================
# workflow/engine.py
# ------------------------------------------------------------
# The Workflow Engine enables multi-agent task chaining.
#
# How it works:
#   1. A workflow is a named sequence of agent steps
#   2. Each step runs an agent with the user's original task
#   3. Each agent's output is passed as input to the next agent
#   4. All outputs are saved to shared workspace memory
#   5. Final output is the last agent's response
#
# Example — content_pipeline workflow:
#   Step 1: Copywriter   → writes the draft
#   Step 2: SEO Agent    → gets Copywriter's draft, optimizes it
#   Step 3: Social Media → gets SEO output, formats for platforms
#
# The shared workspace is what makes this powerful — each agent
# doesn't just see the previous agent's output, it can also see
# ALL prior outputs in the session.
# ============================================================

from agents.configs import AGENTS
from llm.llm_client import build_system_prompt, run_llm
from tools.tool_registry import call_tool


# ── WORKFLOW DEFINITIONS ──────────────────────────────────────
# Each workflow is a list of agent names in execution order.
# Agents must have can_chain: True in their config.

WORKFLOWS = {
    "content_pipeline": {
        "name": "Content Pipeline",
        "description": "Write → SEO optimize → Format for social media",
        "steps": ["Copywriter", "SEO Specialist", "Social Media Manager"],
        "emoji": "🔗",
    },
    "research_and_write": {
        "name": "Research & Write",
        "description": "Analyze data → Turn insights into content",
        "steps": ["Data Analyst", "Copywriter"],
        "emoji": "📊✍️",
    },
}


class WorkflowEngine:
    """
    Executes multi-agent workflows by chaining agents sequentially.
    Each agent receives the previous agent's output as context.
    """

    def __init__(self, workspace, conversation_memory):
        self.workspace = workspace
        self.conv_memory = conversation_memory

    def run(self, workflow_name: str, user_task: str) -> dict:
        """
        Runs a complete workflow and returns all step results.

        Args:
            workflow_name : Key from WORKFLOWS dict
            user_task     : The original user request

        Returns:
            dict: {
                "workflow_name": str,
                "steps": [
                    {"agent": str, "output": str, "step": int},
                    ...
                ],
                "final_output": str,
                "success": bool
            }
        """
        workflow = WORKFLOWS.get(workflow_name)
        if not workflow:
            return {
                "success": False,
                "error": f"Workflow '{workflow_name}' not found.",
                "steps": [],
                "final_output": "",
            }

        steps_results = []
        previous_output = ""    # Output from the last agent, passed to the next
        self.workspace.clear_workflow_context()

        for step_num, agent_name in enumerate(workflow["steps"], start=1):
            config = AGENTS.get(agent_name)
            if not config:
                continue

            # ── Build context for this step ───────────────────
            workspace_context = self.workspace.get_business_context_string()

            # Construct the task message for this agent
            if step_num == 1:
                # First agent gets the raw user task
                task_message = user_task
            else:
                # Subsequent agents get user task + previous agent's output
                task_message = (
                    f"Original task: {user_task}\n\n"
                    f"Work from the following input produced by {workflow['steps'][step_num-2]}:\n"
                    f"{previous_output}"
                )

            # Build system prompt with workflow context
            system_prompt = build_system_prompt(
                config=config,
                workspace_context=workspace_context,
                workflow_input=previous_output if step_num > 1 else "",
            )

            # ── Run this agent ────────────────────────────────
            history = self.conv_memory.get_history(agent_name)
            output = run_llm(
                system_prompt=system_prompt,
                user_message=task_message,
                history=history,
            )

            # ── Save to workspace ─────────────────────────────
            self.workspace.save_agent_output(
                agent_name=agent_name,
                output=output,
                task=f"Workflow step {step_num}: {user_task[:50]}",
            )

            # ── Save to conversation memory ───────────────────
            self.conv_memory.add_message(agent_name, "user", task_message)
            self.conv_memory.add_message(agent_name, "assistant", output)

            steps_results.append({
                "step": step_num,
                "agent": agent_name,
                "emoji": config.get("emoji", "🤖"),
                "output": output,
            })

            # Pass this output to the next agent
            previous_output = output

        return {
            "workflow_name": workflow["name"],
            "description": workflow["description"],
            "steps": steps_results,
            "final_output": previous_output,
            "success": True,
        }
