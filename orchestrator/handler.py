# ============================================================
# orchestrator/handler.py
# ------------------------------------------------------------
# THE ORCHESTRATOR — The most critical component.
#
# Responsibilities:
#   1. Receive user input + selected agent
#   2. Pull business context from shared workspace memory
#   3. Decide: single agent OR multi-agent workflow
#   4. Build the full system prompt (role + context + tools)
#   5. Route to: direct LLM OR OpenClaw runtime
#   6. Save output back to shared workspace
#   7. Return final response to UI
#
# Agents do NOT communicate directly.
# ALL coordination happens HERE — in the orchestrator.
# ============================================================

from agents.configs import AGENTS
from llm.llm_client import build_system_prompt, run_llm, run_router_llm
from workflow.engine import WorkflowEngine, WORKFLOWS
from tools.tool_registry import call_tool
from openclaw.client import OpenClawClient


# OpenClaw client (used when use_openclaw: True)
_openclaw = OpenClawClient()


class Orchestrator:
    """
    Central control layer. Receives all user requests and decides
    how to fulfill them — single agent or multi-agent workflow.
    """

    def __init__(self, workspace, conversation_memory):
        self.workspace = workspace
        self.conv_memory = conversation_memory
        self.workflow_engine = WorkflowEngine(workspace, conversation_memory)

    # ── MAIN ENTRY POINT ──────────────────────────────────────

    def handle(self, user_input: str, agent_name: str,
               mode: str = "single") -> dict:
        """
        Main method called by the UI for every user message.

        Args:
            user_input  : What the user typed
            agent_name  : Which agent is selected in the UI
            mode        : "single" | "auto" | "workflow:<name>"

        Returns:
            dict: {
                "type"        : "single" | "workflow",
                "agent"       : agent name,
                "response"    : final response string,
                "steps"       : list (for workflow only),
                "routed_to"   : which agent/workflow was used,
                "reason"      : routing explanation,
            }
        """
        # Log the user message to workspace
        self.workspace.log_user_message(agent_name, user_input)

        # ── AUTO MODE: let GPT decide the routing ─────────────
        if mode == "auto":
            return self._auto_route(user_input)

        # ── WORKFLOW MODE: explicit workflow requested ─────────
        if mode.startswith("workflow:"):
            workflow_name = mode.split(":", 1)[1]
            return self._run_workflow(user_input, workflow_name)

        # ── SINGLE AGENT MODE: user picked an agent ───────────
        return self._run_single_agent(user_input, agent_name)

    # ── SINGLE AGENT EXECUTION ────────────────────────────────

    def _run_single_agent(self, user_input: str, agent_name: str) -> dict:
        """Runs a single agent and returns its response."""
        config = AGENTS.get(agent_name)
        if not config:
            return {
                "type": "single",
                "agent": agent_name,
                "response": f"❌ Agent '{agent_name}' not found.",
                "routed_to": agent_name,
                "reason": "Agent not found in configs.",
            }

        # Step 1: Get shared business context from workspace
        workspace_context = self.workspace.get_business_context_string()

        # Step 2: Build the complete system prompt
        system_prompt = build_system_prompt(
            config=config,
            workspace_context=workspace_context,
        )

        # Step 3: Get conversation history for this agent
        history = self.conv_memory.get_history(agent_name)

        # Step 4: Route to OpenClaw or direct LLM
        if config["use_openclaw"]:
            response = self._run_openclaw(config, system_prompt, user_input)
        else:
            response = run_llm(system_prompt, user_input, history)

        # Step 5: Save output to shared workspace
        self.workspace.save_agent_output(
            agent_name=agent_name,
            output=response,
            task=user_input[:80],
        )

        # Step 6: Save to conversation memory
        self.conv_memory.add_message(agent_name, "user", user_input)
        self.conv_memory.add_message(agent_name, "assistant", response)

        return {
            "type": "single",
            "agent": agent_name,
            "response": response,
            "routed_to": agent_name,
            "reason": "Direct agent selection by user.",
        }

    # ── WORKFLOW EXECUTION ────────────────────────────────────

    def _run_workflow(self, user_input: str, workflow_name: str) -> dict:
        """Runs a multi-agent workflow chain."""
        result = self.workflow_engine.run(
            workflow_name=workflow_name,
            user_task=user_input,
        )

        if not result["success"]:
            return {
                "type": "error",
                "response": result.get("error", "Workflow failed."),
                "steps": [],
            }

        return {
            "type": "workflow",
            "workflow_name": result["workflow_name"],
            "description": result["description"],
            "steps": result["steps"],
            "response": result["final_output"],
            "routed_to": workflow_name,
            "reason": f"Multi-agent workflow: {result['description']}",
        }

    # ── AUTO ROUTING ──────────────────────────────────────────

    def _auto_route(self, user_input: str) -> dict:
        """
        Uses GPT to intelligently pick the right agent or workflow
        based on the user's message content.
        """
        available_agents = list(AGENTS.keys())
        routing = run_router_llm(user_input, available_agents)

        if routing["mode"] == "workflow":
            result = self._run_workflow(user_input, routing["workflow"])
            result["reason"] = routing.get("reason", "")
            return result
        else:
            agent_name = routing.get("agent", "Virtual Assistant")
            result = self._run_single_agent(user_input, agent_name)
            result["reason"] = routing.get("reason", "")
            return result

    # ── OPENCLAW EXECUTION ────────────────────────────────────

    def _run_openclaw(self, config: dict, system_prompt: str,
                      user_input: str) -> str:
        """Routes to OpenClaw gateway for tool-enabled agents."""
        if not _openclaw.health_check():
            # Graceful fallback to direct LLM if gateway is down
            fallback = run_llm(system_prompt, user_input)
            return (
                fallback + "\n\n---\n"
                "⚠️ *OpenClaw gateway offline — using direct LLM fallback. "
                "Start with: `openclaw gateway start`*"
            )

        return _openclaw.run_agent(
            agent_name=config["name"],
            system_prompt=system_prompt,
            user_message=user_input,
            allowed_tools=config.get("allowed_tools", []),
        )
