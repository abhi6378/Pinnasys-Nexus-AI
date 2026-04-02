# ============================================================
# llm/llm_client.py  (v5)
# ============================================================
# LangChain abstraction over OpenAI.
#
# LangChain is used STRICTLY as:
#   ✅ LLM call wrapper (ChatOpenAI)
#   ✅ Prompt template helper (SystemMessage, HumanMessage, AIMessage)
#   ✅ Tool calling utilities
#
# LangChain is NOT used for:
#   ❌ Orchestration or routing
#   ❌ Workflow control
#   ❌ Agent autonomy (no AgentExecutor)
# ============================================================

import os
import json
import re
import logging

from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from tools.tool_registry import get_tools_description

logger = logging.getLogger(__name__)


def get_llm(model: str = "gpt-4o-mini",
            temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=os.getenv("OPENAI_API_KEY"),
        max_tokens=1500,
    )


def build_system_prompt(config: dict,
                        workspace_context: str = "",
                        workflow_input: str = "") -> str:
    """
    Builds the full system prompt for an agent.

    Sections:
      1. Role (WHO the agent is)
      2. Tone (HOW the agent writes)
      3. Brain AI context (business + brand + prior outputs)
      4. Available tools
      5. Workflow handoff input (if chained)
      6. Output format instructions
      7. Behavioral guardrails
    """
    parts: list[str] = []

    # 1. Role
    parts.append(config["role"])

    # 2. Tone
    parts.append(f"\nTone guidelines: {config['tone']}")

    # 3. Brain AI — shared workspace context
    if workspace_context:
        parts.append(
            f"\n\n{'='*56}\n"
            f"BRAIN AI — SHARED WORKSPACE CONTEXT\n"
            f"(injected by the orchestrator — apply to all outputs)\n"
            f"{'='*56}\n"
            f"{workspace_context}"
        )

    # 4. Available tools
    tool_desc = get_tools_description(config.get("allowed_tools", []))
    if tool_desc:
        parts.append(tool_desc)

    # 5. Workflow input (previous agent's output)
    if workflow_input:
        parts.append(
            f"\n\n{'='*56}\n"
            f"INPUT FROM PREVIOUS AGENT\n"
            f"Build on this — improve it, don't repeat it.\n"
            f"{'='*56}\n"
            f"{workflow_input}"
        )

    # 6. Output format
    parts.append(
        "\n\nOUTPUT FORMAT:\n"
        "- Use clear headers (##) to structure your response\n"
        "- Be specific and actionable — no generic advice\n"
        "- Ground everything in the business context above\n"
        "- Include concrete examples where applicable"
    )

    # 7. Guardrails
    parts.append(
        "\n\nGUARDRAILS:\n"
        "- Stay in character at all times\n"
        "- Do NOT explain what you're about to do — just do it\n"
        "- Do NOT add disclaimers or meta-commentary\n"
        "- Apply brand voice to everything you write"
    )

    return "\n".join(parts)


def run_llm(system_prompt: str,
            user_message: str,
            history: list = None,
            model: str = "gpt-4o-mini") -> str:
    """
    Calls the LLM with full context. Returns plain string.
    LangChain used as wrapper ONLY — no agent control logic.
    """
    llm = get_llm(model=model)
    messages = [SystemMessage(content=system_prompt)]

    for msg in (history or []):
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    messages.append(HumanMessage(content=user_message))

    try:
        response = llm.invoke(messages)
        logger.info(f"[LLM] Success. ~{len(response.content)//4} tokens")
        return response.content
    except Exception as e:
        logger.error(f"[LLM] Error: {e}")
        return f"❌ LLM Error: {str(e)}"


def run_router_llm(user_message: str,
                   available_agents: list[str]) -> dict:
    """
    GPT-powered routing suggestion (last-resort, auto mode only).
    Called ONLY when intent classifier finds no match.
    DecisionLayer validates the suggestion before any execution.
    """
    llm          = get_llm(model="gpt-4o-mini", temperature=0.0)
    agents_list  = "\n".join(f"  - {a}" for a in available_agents)
    workflows_str = (
        "  - content_pipeline    : Write → SEO → Social\n"
        "  - research_and_write  : Data → Copy\n"
        "  - email_campaign      : Copy → Email sequence\n"
        "  - full_marketing_blast: Strategy → Copy → SEO → Email → Social\n"
        "  - sales_campaign      : Strategy → Sales pitch → Outreach\n"
        "  - data_to_social      : Data → Article → Social\n"
        "  - product_launch      : PM → PR → Copy → Email → Social\n"
        "  - support_and_report  : Support → Data → Strategy report"
    )

    system = (
        "You are a routing engine. Your ONLY job is to read the request "
        "and pick the best agent or workflow. Reply ONLY with valid JSON."
    )
    prompt = f"""Request: "{user_message}"

Agents:
{agents_list}

Workflows (use ONLY when 2+ agents are clearly needed in sequence):
{workflows_str}

Return ONLY this JSON:
{{
  "mode":     "single" or "workflow",
  "agent":    "exact agent name" (if single),
  "workflow": "workflow_key"     (if workflow),
  "reason":   "one sentence"
}}"""

    try:
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=prompt)])
        text = re.sub(r"```(?:json)?|```", "", resp.content.strip()).strip()
        result = json.loads(text)
        logger.info(f"[LLM ROUTER] {result}")
        return result
    except Exception as e:
        logger.error(f"[LLM ROUTER] Parse failed: {e}")
        return {
            "mode":   "single",
            "agent":  "Virtual Assistant",
            "reason": f"Router fallback: {str(e)}",
        }
