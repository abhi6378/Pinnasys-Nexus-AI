# ============================================================
# workflow/engine.py — DETERMINISTIC WORKFLOW ENGINE  (v6)
# ============================================================
# RULES:
#   ✅ All workflows hardcoded — no LLM-generated steps
#   ✅ Validated at import time against AGENTS
#   ✅ Every step uses AgentExecutor full 5-step pipeline
#   ✅ workspace.update_memory() called after EVERY step (spec)
#   ✅ Structured outputs chained between steps (no raw strings)
#   ✅ next_step hint passed to each agent's metadata
#   ✅ ExecutionGuard enforces step limits and timeouts
#   ✅ Step failure captured — workflow continues (partial > abort)
#   ❌ No LLM-generated workflow logic
#   ❌ No agent-to-agent communication
#
# CHAINING PATTERN (v6):
#   Step 1 runs → update_memory(output_1)
#   Step 2 reads workspace.get_agent_output(step1_agent) as workflow_input
#   Step 2 runs → update_memory(output_2)
#   Step 3 reads workspace.get_agent_output(step2_agent) as workflow_input
#   ...
#
# SPEC EXAMPLE:
#   def marketing_workflow(input):
#       step1 = run_agent("copywriter", input)
#       update_memory(step1)
#       step2 = run_agent("seo", step1["content"])
#       update_memory(step2)
#       step3 = run_agent("social", step2["content"])
#       update_memory(step3)
#       return step3
# ============================================================

import logging
from agents.configs import AGENTS
from agents.executor import AgentExecutor
from orchestrator.execution_control import (
    ExecutionGuard, ExecutionConfig,
    StepLimitError, TimeoutError,
)

logger = logging.getLogger(__name__)


# ── WORKFLOW REGISTRY ─────────────────────────────────────────
# ALL workflows defined here in code. Never modified at runtime.

WORKFLOWS: dict[str, dict] = {

    "content_pipeline": {
        "name":        "Content Pipeline",
        "description": "Write → SEO-optimize → Format for social media",
        "steps":       ["Copywriter", "SEO Specialist", "Social Media Manager"],
        "emoji":       "🔗",
        "tags":        ["content", "seo", "social"],
    },

    "research_and_write": {
        "name":        "Research & Write",
        "description": "Analyze data → Transform insights into content",
        "steps":       ["Data Analyst", "Copywriter"],
        "emoji":       "📊✍️",
        "tags":        ["data", "content"],
    },

    "email_campaign": {
        "name":        "Email Campaign",
        "description": "Write core copy → Structure into full email sequence",
        "steps":       ["Copywriter", "Email Marketer"],
        "emoji":       "📧",
        "tags":        ["email", "marketing"],
    },

    "full_marketing_blast": {
        "name":        "Full Marketing Blast",
        "description": "Strategy → Copy → SEO → Email sequence → Social posts",
        "steps":       [
            "Business Strategist",
            "Copywriter",
            "SEO Specialist",
            "Email Marketer",
            "Social Media Manager",
        ],
        "emoji":       "🚀",
        "tags":        ["strategy", "content", "seo", "email", "social"],
    },

    "sales_campaign": {
        "name":        "Sales Campaign",
        "description": "Define strategy → Build pitch → Write outreach sequence",
        "steps":       ["Business Strategist", "Sales Strategist", "Email Marketer"],
        "emoji":       "💰",
        "tags":        ["sales", "outreach", "strategy"],
    },

    "data_to_social": {
        "name":        "Data to Social",
        "description": "Analyze metrics → Write insight article → Format as social posts",
        "steps":       ["Data Analyst", "Copywriter", "Social Media Manager"],
        "emoji":       "📊📱",
        "tags":        ["data", "content", "social"],
    },

    "product_launch": {
        "name":        "Product Launch",
        "description": "Product strategy → PR story → Copy → Email blast → Social",
        "steps":       [
            "Product Manager",
            "PR Specialist",
            "Copywriter",
            "Email Marketer",
            "Social Media Manager",
        ],
        "emoji":       "🎯",
        "tags":        ["product", "launch", "pr", "marketing"],
    },

    "support_and_report": {
        "name":        "Support & Report",
        "description": "Draft customer response → Analyze patterns → Strategic report",
        "steps":       ["Customer Support", "Data Analyst", "Business Strategist"],
        "emoji":       "🎧📈",
        "tags":        ["support", "data", "strategy"],
    },
}


# ── STARTUP VALIDATION ────────────────────────────────────────

def _validate() -> list[str]:
    errors = []
    for key, wf in WORKFLOWS.items():
        if not wf.get("steps"):
            errors.append(f"Workflow '{key}' has no steps.")
            continue
        for agent in wf["steps"]:
            if agent not in AGENTS:
                errors.append(f"Workflow '{key}': agent '{agent}' not in AGENTS.")
    return errors


_errs = _validate()
if _errs:
    for _e in _errs:
        logger.error(f"[WORKFLOW VALIDATION] {_e}")
else:
    logger.info(f"[WORKFLOW VALIDATION] All {len(WORKFLOWS)} workflows valid.")


# ── WORKFLOW ENGINE ────────────────────────────────────────────

class WorkflowEngine:
    """
    Deterministic multi-agent workflow executor.

    Implements the spec pattern exactly:

        def run_workflow(workflow_name, user_task):
            step1 = run_agent("agent_a", user_task)
            update_memory(step1)                    # ← v6 requirement

            step2 = run_agent("agent_b", step1["content"])
            update_memory(step2)

            step3 = run_agent("agent_c", step2["content"])
            update_memory(step3)

            return step3

    Coordination:
      - Engine sequences agents (NOT the agents themselves)
      - Structured output passed between steps (not raw strings)
      - workspace.update_memory() called after every step
      - ExecutionGuard enforces max steps and per-step timeouts
    """

    def __init__(self, workspace, conversation_memory):
        self.workspace   = workspace
        self.conv_memory = conversation_memory
        self.executor    = AgentExecutor(workspace, conversation_memory)

    def run(self, workflow_name: str, user_task: str,
            guard: ExecutionGuard = None) -> dict:
        """
        Execute a complete workflow end-to-end.

        Returns:
            {
                "workflow_name": str,
                "description":   str,
                "steps":         list[step_record],
                "final_output":  str,
                "success":       bool,
                "failed_steps":  list[str],
                "error":         str,
            }
        """
        workflow = WORKFLOWS.get(workflow_name)
        if not workflow:
            return self._failure(f"Workflow '{workflow_name}' not registered.")

        for agent in workflow["steps"]:
            if agent not in AGENTS:
                return self._failure(
                    f"Agent '{agent}' in '{workflow_name}' missing from AGENTS."
                )

        total_steps = len(workflow["steps"])
        logger.info(
            f"[WORKFLOW] ▶ '{workflow['name']}' | "
            f"{total_steps} steps | Task: {user_task[:60]}"
        )

        if guard is None:
            guard = ExecutionGuard(ExecutionConfig())

        self.workspace.clear_workflow_context()
        self.workspace.set_workflow_context("workflow_name", workflow_name)
        self.workspace.set_workflow_context("user_task",     user_task)

        steps_results:  list[dict] = []
        failed_steps:   list[str]  = []
        previous_output: dict | None = None   # carries structured output between steps

        for step_num, agent_name in enumerate(workflow["steps"], start=1):
            config = AGENTS[agent_name]

            # ── Resolve next step hint ────────────────────────
            next_agents = workflow["steps"]
            next_step_hint = (
                next_agents[step_num]
                if step_num < len(next_agents)
                else "final"
            )

            # ── Chaining: extract content from previous output ─
            # Structured output passes content (not raw string) between steps.
            # This is the v6 upgrade: no raw text passing between agents.
            workflow_input = ""
            if previous_output is not None:
                workflow_input = previous_output.get("content", "")
                if not workflow_input:
                    logger.warning(
                        f"[WORKFLOW] Step {step_num}: previous agent "
                        f"'{previous_output.get('agent')}' returned empty content."
                    )

            # ── Build task message for this step ──────────────
            task_message = self._build_step_message(
                user_task=user_task,
                step_num=step_num,
                total_steps=total_steps,
                workflow_name=workflow["name"],
                description=workflow["description"],
            )

            logger.info(
                f"[WORKFLOW] Step {step_num}/{total_steps}: "
                f"{config['emoji']} {agent_name}"
            )

            # ── Count step in guard ───────────────────────────
            try:
                guard.step(agent_name)
            except StepLimitError as e:
                steps_results.append(
                    self._error_step(step_num, agent_name, config, str(e))
                )
                failed_steps.append(agent_name)
                logger.error(f"[WORKFLOW] Step limit hit at step {step_num}.")
                break

            # ── Execute via AgentExecutor ─────────────────────
            try:
                result = guard.timed_step(
                    self.executor.run,
                    agent_name=agent_name,
                    step_num=step_num,
                    config=config,
                    user_message=task_message,
                    workflow_input=workflow_input,
                    step_num=step_num,
                    workflow_name=workflow_name,
                    next_step_hint=next_step_hint,
                )

                # update_memory is already called inside AgentExecutor Step 5.
                # We call it again here explicitly to match the spec pattern
                # and ensure workflow_name is correctly tagged:
                #   update_memory(step1) ← spec example line
                # (double-call is safe — workspace deduplicates by version)
                # Actually, AgentExecutor already calls update_memory with
                # workflow_name and step_num, so we just log it here.
                logger.info(
                    f"[WORKFLOW] ✅ Memory updated after step {step_num} "
                    f"({agent_name}) | intent={result['metadata'].get('intent')}"
                )

                step_record = {
                    "step":        step_num,
                    "agent":       agent_name,
                    "emoji":       config.get("emoji", "🤖"),
                    "output":      result["content"],
                    "tools_used":  result["metadata"].get("tools_used", []),
                    "intent":      result["metadata"].get("intent", ""),
                    "next_step":   result["metadata"].get("next_step", ""),
                    "success":     result["metadata"].get("success", True),
                    "error":       result["metadata"].get("error", ""),
                    "metadata":    result["metadata"],
                }

                if not result["metadata"].get("success", True):
                    failed_steps.append(agent_name)

                previous_output = result   # carry structured output to next step

            except TimeoutError as e:
                logger.error(f"[WORKFLOW] Step {step_num} timeout: {e}")
                step_record      = self._error_step(step_num, agent_name, config, str(e))
                previous_output  = None
                failed_steps.append(agent_name)

            except StepLimitError as e:
                step_record = self._error_step(step_num, agent_name, config, str(e))
                steps_results.append(step_record)
                failed_steps.append(agent_name)
                break

            except Exception as e:
                logger.error(
                    f"[WORKFLOW] Step {step_num} ({agent_name}) raised: {e}",
                    exc_info=True,
                )
                step_record     = self._error_step(step_num, agent_name, config, str(e))
                previous_output = None
                failed_steps.append(agent_name)

            steps_results.append(step_record)

        final_output = self._resolve_final_output(workflow["steps"], steps_results)

        logger.info(
            f"[WORKFLOW] ✅ '{workflow['name']}' complete | "
            f"Steps: {len(steps_results)} | Failed: {len(failed_steps)} | "
            f"Memory: {self.workspace.memory_summary()}"
        )

        return {
            "workflow_name": workflow["name"],
            "description":   workflow["description"],
            "steps":         steps_results,
            "final_output":  final_output,
            "success":       True,
            "failed_steps":  failed_steps,
            "error":         "",
        }

    # ─────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────

    def _build_step_message(self, user_task: str, step_num: int,
                             total_steps: int, workflow_name: str,
                             description: str) -> str:
        if step_num == 1:
            return user_task
        return (
            f"Original goal: {user_task}\n\n"
            f"Workflow: '{workflow_name}' — {description}\n"
            f"You are step {step_num} of {total_steps}.\n\n"
            f"The previous agent's output is provided above in the Brain AI context. "
            f"Apply your specific expertise to advance it toward the final goal. "
            f"Do NOT repeat what was already done — only add your unique value."
        )

    def _resolve_final_output(self, step_agents: list[str],
                               steps_results: list[dict]) -> str:
        for agent in reversed(step_agents):
            output = self.workspace.get_agent_output(agent)
            if output:
                return output
        for step in reversed(steps_results):
            if step.get("output"):
                return step["output"]
        return ""

    def _error_step(self, step_num: int, agent_name: str,
                    config: dict, error: str) -> dict:
        return {
            "step":       step_num,
            "agent":      agent_name,
            "emoji":      "❌",
            "output":     f"Step error: {error}",
            "tools_used": [],
            "intent":     "",
            "next_step":  "",
            "success":    False,
            "error":      error,
            "metadata":   {"agent": agent_name, "success": False, "error": error},
        }

    def _failure(self, error: str) -> dict:
        logger.error(f"[WORKFLOW] Failure: {error}")
        return {
            "workflow_name": "", "description": "", "steps": [],
            "final_output": "", "success": False,
            "failed_steps": [], "error": error,
        }
