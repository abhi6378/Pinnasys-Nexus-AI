# ============================================================
# llm/llm_client.py
# ------------------------------------------------------------
# Abstracted LLM layer using LangChain.
#
# WHY LangChain here?
# LangChain gives us a clean abstraction over the OpenAI API:
#   - Easy to swap OpenAI → Claude → local model later
#   - Built-in prompt templates
#   - Memory integration
#   - Future: tool calling, agents, chains
#
# Used by the orchestrator for ALL direct LLM calls.
# The OpenClaw client handles the other 2 agents separately.
# ============================================================

import os
from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate
from tools.tool_registry import get_tools_description


# ── LLM SETUP ─────────────────────────────────────────────────

def get_llm(model: str = "gpt-4o-mini", temperature: float = 0.7):
    """
    Returns a LangChain ChatOpenAI instance.
    Swap the model name to change the LLM — nothing else needs to change.
    
    Models:
        gpt-4o-mini  → cheap, fast, good for demos
        gpt-4o       → more powerful, higher cost
        gpt-4-turbo  → balance of speed and quality
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=os.getenv("OPENAI_API_KEY"),
        max_tokens=1500,
    )


# ── PROMPT BUILDER ────────────────────────────────────────────

def build_system_prompt(config: dict, workspace_context: str = "",
                        workflow_input: str = "") -> str:
    """
    Builds the full system prompt for an agent by combining:
      1. Agent role (who they are)
      2. Agent tone (how they write)
      3. Business context from shared workspace memory
      4. Available tools description
      5. Workflow input (if this agent is part of a chain)

    Args:
        config           : Agent config dict from configs.py
        workspace_context: Business context from WorkspaceMemory
        workflow_input   : Content passed from a previous agent in a chain

    Returns:
        str: Complete system prompt
    """
    parts = [config["role"], f"\nTone: {config['tone']}"]

    # Inject business context from shared workspace
    if workspace_context:
        parts.append(f"\n\n{workspace_context}")

    # Inject tool descriptions so the agent knows what it can do
    tool_desc = get_tools_description(config.get("allowed_tools", []))
    if tool_desc:
        parts.append(tool_desc)

    # Inject workflow input from previous agent
    if workflow_input:
        parts.append(
            f"\n\nINPUT FROM PREVIOUS AGENT:\n{workflow_input}\n"
            f"Use the above as your starting point and build upon it."
        )

    parts.append(
        "\n\nAlways stay in character. Be specific and actionable. "
        "Do not add unnecessary preamble. Get straight to the task."
    )

    return "\n".join(parts)


# ── MAIN LLM CALL ─────────────────────────────────────────────

def run_llm(system_prompt: str, user_message: str,
            history: list = None, model: str = "gpt-4o-mini") -> str:
    """
    Calls the LLM via LangChain and returns the response string.

    Args:
        system_prompt : Full agent system prompt (role + context + tools)
        user_message  : The user's input
        history       : List of past messages [{"role": "...", "content": "..."}]
        model         : Which model to use

    Returns:
        str: LLM response text
    """
    llm = get_llm(model=model)

    # Build the messages list for LangChain
    messages = [SystemMessage(content=system_prompt)]

    # Add conversation history
    if history:
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

    # Add the current user message
    messages.append(HumanMessage(content=user_message))

    try:
        response = llm.invoke(messages)
        return response.content
    except Exception as e:
        return f"❌ LLM Error: {str(e)}"


# ── ROUTER LLM CALL ───────────────────────────────────────────

def run_router_llm(user_message: str, available_agents: list) -> dict:
    """
    Uses GPT to intelligently decide which agent(s) should handle
    a user's query, and whether it needs a single agent or a workflow.

    Returns:
        dict: {
            "mode": "single" | "workflow",
            "agent": "agent_name",           (if single)
            "workflow": "workflow_name",      (if workflow)
            "reason": "explanation"
        }
    """
    llm = get_llm(model="gpt-4o-mini", temperature=0.1)

    agents_list = "\n".join(f"  - {a}" for a in available_agents)

    system = (
        "You are a routing engine for a multi-agent AI system. "
        "Your job is to read a user's request and decide which agent or workflow should handle it. "
        "Reply ONLY with valid JSON. No explanation outside the JSON."
    )

    prompt = f"""User request: "{user_message}"

Available agents:
{agents_list}

Available workflows:
  - content_pipeline: Copywriter → SEO Specialist → Social Media Manager
    (Use when user wants to create AND optimize AND format content for social)
  - research_and_write: Data Analyst → Copywriter
    (Use when user wants data insights turned into content)

Decide:
- If this is a simple task for ONE agent → mode: "single"
- If this clearly benefits from a multi-step workflow → mode: "workflow"

Respond with ONLY this JSON:
{{
  "mode": "single" or "workflow",
  "agent": "exact agent name from the list above" (if single),
  "workflow": "content_pipeline" or "research_and_write" (if workflow),
  "reason": "one sentence explanation"
}}"""

    try:
        response = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=prompt)
        ])
        import json, re
        text = response.content.strip()
        # Strip markdown fences if present
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception as e:
        # Fallback: default to Virtual Assistant
        return {
            "mode": "single",
            "agent": "Virtual Assistant",
            "reason": f"Router fallback due to error: {str(e)}"
        }
