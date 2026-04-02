# ============================================================
# agents/executor.py — AGENT EXECUTION UNIT  (v6)
# ============================================================
# STRICT 5-STEP PIPELINE (enforced for EVERY agent, EVERY time):
#
#   Step 1  FETCH    — workspace.inject_into_prompt()     ← Brain AI
#   Step 2  BUILD    — build_system_prompt(config, context, wf_input)
#   Step 3  EXECUTE  — ExecutionLayer.execute()           ← LLM or OpenClaw
#   Step 4  TOOLS    — ToolExecutionLayer (auto + intent + keyword)
#   Step 5  SAVE     — workspace.update_memory(structured_output)
#
# v6 STRUCTURED OUTPUT FORMAT (spec requirement):
#
#   {
#       "agent":    "Copywriter",
#       "content":  "Here is your copy...",
#       "metadata": {
#           "intent":          "create_content",
#           "next_step":       "seo_optimize",
#           "tools_used":      ["mock_search_web"],
#           "tool_results":    {...},
#           "task":            "Write a LinkedIn post...",
#           "step":            1,
#           "workflow":        "content_pipeline",
#           "timestamp":       "...",
#           "tokens_approx":   420,
#           "success":         True,
#           "error":           "",
#       }
#   }
#
# v6 UPGRADES vs v5:
#   + inject_into_prompt() replaces get_full_context_for_agent()
#   + update_memory() called in Step 5 (structured, not raw string)
#   + intent detection added to metadata (detect_intent())
#   + next_step hint populated from workflow config
#   + tool triggering now 3 modes: auto + intent + keyword
# ============================================================

import logging
from datetime import datetime

from llm.llm_client import build_system_prompt
from llm.execution_layer import execution_layer
from tools.tool_registry import ToolExecutionLayer

logger = logging.getLogger(__name__)

# ── INTENT LABELS ────────────────────────────────────────────
# Maps agent names to their primary intent label.
# Surfaced in metadata.intent for downstream awareness.

AGENT_INTENT_MAP: dict[str, str] = {
    "Copywriter":           "create_content",
    "SEO Specialist":       "seo_optimize",
    "Social Media Manager": "format_social",
    "Email Marketer":       "write_email",
    "Sales Strategist":     "build_sales_strategy",
    "Business Strategist":  "build_strategy",
    "eCom Specialist":      "ecom_optimize",
    "Recruiter":            "recruitment",
    "Personal Growth":      "coaching",
    "Virtual Assistant":    "assist",
    "PR Specialist":        "public_relations",
    "Product Manager":      "product_strategy",
    "Customer Support":     "support_response",
    "Data Analyst":         "analyze_data",
}


class AgentExecutor:
    """
    Stateless execution unit. Same 5-step flow for single AND workflow modes.

    Called by:
      - Orchestrator  (single-agent mode)
      - WorkflowEngine (per step in a chain)

    Always returns v6 structured output:
        {"agent": str, "content": str, "metadata": dict}
    """

    def __init__(self, workspace, conversation_memory):
        self.workspace   = workspace
        self.conv_memory = conversation_memory
        self.tool_layer  = ToolExecutionLayer(workspace)

    def run(self,
            agent_name:     str,
            config:         dict,
            user_message:   str,
            workflow_input: str  = "",
            step_num:       int  = 0,
            workflow_name:  str  = "",
            next_step_hint: str  = "") -> dict:
        """
        Runs one agent through the strict 5-step pipeline.

        Args:
            agent_name      : e.g. "Copywriter"
            config          : agent dict from agents/configs.py
            user_message    : the task for this agent
            workflow_input  : previous agent's content (workflow mode)
            step_num        : position in workflow (0 = single)
            workflow_name   : parent workflow key
            next_step_hint  : name of next agent (for metadata.next_step)

        Returns:
            {
                "agent":    str,
                "content":  str,
                "metadata": dict,
            }
        """
        logger.info(
            f"[AGENT EXEC] ▶ {agent_name}"
            + (f" step {step_num} / '{workflow_name}'" if workflow_name else "")
        )

        tools_used:   list[str] = []
        tool_results: dict      = {}
        error_msg:    str       = ""
        raw_output:   str       = ""

        try:
            # ── STEP 1: FETCH — Brain AI context ──────────────
            # inject_into_prompt() returns brand_context + user_context
            # + conversation_history (last 4) + previous_outputs (last 5)
            shared_context = self.workspace.inject_into_prompt(agent_name)

            # ── STEP 2: BUILD — complete system prompt ─────────
            system_prompt = build_system_prompt(
                config=config,
                workspace_context=shared_context,
                workflow_input=workflow_input,
            )

            # ── STEP 3: EXECUTE — LLM or OpenClaw ─────────────
            history    = self.conv_memory.get_history(agent_name)
            raw_output = execution_layer.execute(
                agent_name=agent_name,
                config=config,
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
            )

            # ── STEP 4: TOOLS ─────────────────────────────────
            # 4a. auto_tools (always run — declared in config)
            auto_tools = [
                t for t in config.get("auto_tools", [])
                if t in config.get("allowed_tools", [])
            ]
            if auto_tools:
                auto_r = self.tool_layer.execute_auto_tools(
                    tool_names=auto_tools,
                    agent_name=agent_name,
                    agent_output=raw_output,
                )
                tool_results.update(auto_r)
                tools_used.extend(auto_r.keys())

            # 4b. intent_tools — triggered by detected intent
            # Example from spec: if "email" in task → run send_email tool
            intent_r = self.tool_layer.execute_intent_tools(
                intent=AGENT_INTENT_MAP.get(agent_name, ""),
                allowed_tools=config.get("allowed_tools", []),
                agent_name=agent_name,
                agent_output=raw_output,
            )
            for tool_name, result in intent_r.items():
                if tool_name not in tool_results:
                    tool_results[tool_name] = result
                    tools_used.append(tool_name)

            # 4c. keyword_tools — triggered by task content
            kw_r = self.tool_layer.execute_keyword_tools(
                task_text=user_message,
                allowed_tools=config.get("allowed_tools", []),
                agent_name=agent_name,
                agent_output=raw_output,
            )
            for tool_name, result in kw_r.items():
                if tool_name not in tool_results:
                    tool_results[tool_name] = result
                    tools_used.append(tool_name)

            # Append tool results block to content
            final_content = raw_output
            if tool_results:
                lines = "\n".join(
                    f"• `{name}`: {result}"
                    for name, result in tool_results.items()
                )
                final_content += f"\n\n---\n**🔧 Tool Results:**\n{lines}"

            success = True

        except Exception as e:
            logger.error(f"[AGENT EXEC] ❌ '{agent_name}': {e}", exc_info=True)
            final_content = f"Agent execution error: {str(e)}"
            error_msg     = str(e)
            success       = False

        # ── STEP 5: SAVE — update_memory() ───────────────────
        # Build the full v6 structured output
        structured_output = {
            "agent":   agent_name,
            "content": final_content,
            "metadata": {
                "intent":        AGENT_INTENT_MAP.get(agent_name, "general"),
                "next_step":     next_step_hint,
                "tools_used":    tools_used,
                "tool_results":  tool_results,
                "task":          user_message[:80],
                "step":          step_num,
                "workflow":      workflow_name,
                "timestamp":     datetime.now().isoformat(),
                "tokens_approx": len(final_content) // 4,
                "success":       success,
                "error":         error_msg,
                "emoji":         config.get("emoji", "🤖"),
            },
        }

        # update_memory() — single call (spec requirement)
        self.workspace.update_memory(
            structured_output=structured_output,
            step_num=step_num,
            workflow_name=workflow_name,
        )

        # Update per-agent conversation history
        self.conv_memory.add_message(agent_name, "user",      user_message)
        self.conv_memory.add_message(agent_name, "assistant", final_content)

        logger.info(
            f"[AGENT EXEC] ✅ {agent_name} | "
            f"intent={structured_output['metadata']['intent']} | "
            f"tools={tools_used or 'none'}"
        )

        return structured_output

    def run_tool_explicitly(self, agent_name: str, config: dict,
                            tool_name: str, **kwargs) -> str:
        """Explicit tool call with permission check. Called by orchestrator."""
        if tool_name not in config.get("allowed_tools", []):
            return f"❌ Agent '{agent_name}' not permitted to use '{tool_name}'."
        return self.tool_layer.call_one(tool_name=tool_name, agent_name=agent_name)
