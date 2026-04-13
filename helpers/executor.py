"""
helpers/executor.py  —  Runs a single helper with Brain AI context

Mission 6 update: tool availability is now driven by the agent config's
``allowed_tools`` list, not by scanning the registry. The config is the
single authority for which tools an agent may use.

Backward compatibility:
  - Signature is  run_agent(agent_key, user_input, brain_context, **kwargs)
  - Existing callers (handler.py, engine.py) pass 3 positional args → text-only
  - When workspace_id + db are passed, tool-aware mode activates for agents
    whose config has  tool_mode == "tool_enabled"  and  allowed_tools != [].
  - Return contract is the same:  {agent, name?, output, success}
    plus optional keys:  {tool_used, connect_required, connect_url, resume_token}

Tool execution flow:
  1. Read tool_mode + allowed_tools from agent config.
  2. If tool_mode != "tool_enabled" or allowed_tools is empty → text-only path.
  3. Resolve allowed_tools names to full registry entries via get_tools_by_names().
  4. Call generate_with_tool_awareness() with the resolved tool list.
  5. Parse the LLM response:
     a) Normal text → return as-is.
     b) JSON with tool_call → validate name is in allowed_tools → execute.
  6. If tool executed successfully → feed result back for final human response.
  7. Hard cap on tool loop iterations (MAX_TOOL_ITERATIONS).
  8. Duplicate-call prevention via seen_calls set.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from helpers.configs import get_agent
from llm.client import generate, generate_with_tool_awareness
from tools.tool_registry import get_tools_by_names, is_agent_allowed, get_tool

logger = logging.getLogger(__name__)

# Maximum number of tool calls per single run_agent invocation.
# Prevents runaway loops if the LLM keeps requesting tools.
MAX_TOOL_ITERATIONS = 5


# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(agent: dict, brain_context: str) -> str:
    base = f"""You are {agent['name']}, an AI {agent['role']}.

Your goal: {agent['goal']}
Your tone: {agent['tone']}
Your boundaries: {agent['boundaries']}
Output format: {agent['output_format']}

--- BUSINESS CONTEXT (use this to personalize your response) ---
{brain_context}
--- END CONTEXT ---

Always stay in your role. Use the business context to make your response
specific and relevant to this business. Never make up facts not in the context.
"""

    # Inject agent-level tool guidance if present in the config.
    # This gives the agent personality-level awareness of tool usage.
    tool_instructions = agent.get("tool_instructions")
    if tool_instructions:
        base += (
            f"\n--- TOOL USAGE GUIDANCE ---\n"
            f"{tool_instructions}\n"
            f"--- END GUIDANCE ---\n"
        )

    return base


# ── Tool-call JSON parser ─────────────────────────────────────────────────────

def _extract_tool_call(raw_output: str) -> Optional[dict]:
    """
    Try to extract a structured tool_call from the LLM's raw output.

    The LLM is instructed to respond with:
      {"message": "...", "tool_call": {"name": "TOOL_NAME", "params": {...}}}

    We accept this both as:
      - Pure JSON (the entire response is JSON)
      - JSON inside a ```json code fence

    Returns the parsed dict if a tool_call is found, or None for plain text.
    """
    text = raw_output.strip()

    # ── Try 1: strip markdown code fence if present ───────────────────────
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?\s*```",
        text,
        re.DOTALL,
    )
    if fence_match:
        text = fence_match.group(1).strip()

    # ── Try 2: parse as JSON ─────────────────────────────────────────────
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool_call" in data:
            tool_call = data["tool_call"]
            if isinstance(tool_call, dict) and "name" in tool_call:
                return data
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return None


def _format_connect_required_message(
    tool_name: str,
    toolkit: str,
    connect_url: Optional[str],
    agent_name: str,
) -> str:
    """Build a user-facing message when a tool needs OAuth."""
    if connect_url:
        return (
            f"🔗 **{agent_name} needs access to {toolkit}** to complete this request.\n\n"
            f"Please connect your {toolkit} account to continue:\n"
            f"👉 [{toolkit} — Connect Now]({connect_url})\n\n"
            f"_Once connected, try your request again and I'll execute it for you._"
        )
    return (
        f"🔗 **{agent_name} needs access to {toolkit}** to use `{tool_name}`, "
        f"but the connection service is currently unavailable. "
        f"Please try again later."
    )


def _format_tool_error_message(
    tool_name: str,
    error: str,
    agent_name: str,
) -> str:
    """Build a user-facing message when a tool call fails."""
    return (
        f"⚠️ **{agent_name} tried to use `{tool_name}` but it failed.**\n\n"
        f"Error: {error}\n\n"
        f"_I'll try to help with what I know instead._"
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_agent(
    agent_key: str,
    user_input: str,
    brain_context: str = "",
    *,
    workspace_id: str = "",
    db=None,
) -> dict:
    """
    Executes a single helper.

    Parameters:
        agent_key     — Key from helpers/configs.py
        user_input    — The user's message
        brain_context — Business context from BrainAI

    Keyword-only (optional — backward compat):
        workspace_id  — Required for tool execution; without it, text-only mode.
        db            — SQLAlchemy session; required for tool logging/pending reqs.

    Returns:
        {
            "agent":   str,               # agent key
            "name":    str,               # display name
            "output":  str,               # final response text
            "success": bool,              # True if completed without error
            # Optional (only when tools are involved):
            "tool_used":          str | None,   # tool_name that was executed
            "connect_required":   bool,         # True when auth is needed
            "connect_url":        str | None,   # OAuth URL
            "resume_token":       str | None,   # for pending request resume
        }
    """
    agent = get_agent(agent_key)
    if not agent:
        return {
            "agent": agent_key,
            "output": f"Unknown agent: {agent_key}",
            "success": False,
        }

    system_prompt = build_system_prompt(
        agent, brain_context or "No context available."
    )
    agent_name = agent["name"]

    # ── Determine if this agent has tools available ───────────────────────
    # Tools are only available when ALL three conditions are met:
    #   1. workspace_id and db are provided (caller opted in)
    #   2. Agent config has tool_mode == "tool_enabled"
    #   3. Agent config has a non-empty allowed_tools list
    # The config's allowed_tools is the SINGLE SOURCE OF TRUTH for what
    # tools this agent may use. The registry just provides metadata.
    available_tools = []
    config_tools = agent.get("allowed_tools", [])
    tool_mode = agent.get("tool_mode", "text_only")

    if workspace_id and db and tool_mode == "tool_enabled" and config_tools:
        available_tools = get_tools_by_names(config_tools)

    # ── Text-only path (no tools, or tool infrastructure not provided) ────
    if not available_tools:
        try:
            output = generate(
                prompt=user_input,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1500,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": output,
                "success": True,
            }
        except Exception as e:
            return {
                "agent": agent_key,
                "output": f"Error: {str(e)}",
                "success": False,
            }

    # ── Tool-aware path ───────────────────────────────────────────────────
    return _run_with_tools(
        agent_key=agent_key,
        agent=agent,
        agent_name=agent_name,
        system_prompt=system_prompt,
        user_input=user_input,
        available_tools=available_tools,
        workspace_id=workspace_id,
        db=db,
    )


# ── Tool-aware execution loop ────────────────────────────────────────────────

def _run_with_tools(
    agent_key: str,
    agent: dict,
    agent_name: str,
    system_prompt: str,
    user_input: str,
    available_tools: list[dict],
    workspace_id: str,
    db,
) -> dict:
    """
    Calls the LLM with tool awareness. If the LLM requests a tool:
      - Validates tool name is in agent config's allowed_tools (config gate)
      - Validates it exists in the registry (registry gate)
      - Validates agent permission in registry (permission gate)
      - Checks connection via tool_executor
      - Executes if connected
      - Feeds tool output back for a final human response
      - Caps iterations at MAX_TOOL_ITERATIONS

    Returns the same dict shape as run_agent().
    """
    # Lazy import to avoid circular dependency at module load time.
    # tool_executor imports from tool_registry which is fine, but we
    # keep the import local to be explicit about the dependency.
    from tools.tool_executor import attempt_tool_call

    current_prompt = user_input
    tool_used = None
    # Track (tool_name, params_hash) to prevent identical repeated calls.
    seen_calls: set[tuple[str, str]] = set()
    # Build the config-level allow-set once for fast O(1) membership tests.
    config_allowed: set[str] = set(agent.get("allowed_tools", []))

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            raw_output = generate_with_tool_awareness(
                prompt=current_prompt,
                system_prompt=system_prompt,
                available_tools=available_tools,
                temperature=0.7,
                max_tokens=2000,
            )
        except Exception as e:
            return {
                "agent": agent_key,
                "output": f"Error: {str(e)}",
                "success": False,
            }

        # ── Parse: is this a tool call or plain text? ─────────────────────
        parsed = _extract_tool_call(raw_output)

        if parsed is None:
            # Normal text response — return directly (most common path)
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": raw_output,
                "success": True,
                "tool_used": tool_used,
            }

        # ── Structured tool call detected ─────────────────────────────────
        tool_call = parsed["tool_call"]
        tool_name = tool_call.get("name", "")
        tool_params = tool_call.get("params", {})
        message = parsed.get("message", "")

        logger.info(
            "Agent '%s' requesting tool '%s' (iteration %d/%d)",
            agent_key, tool_name, iteration + 1, MAX_TOOL_ITERATIONS,
        )

        # ── Guard: prevent identical duplicate tool calls ─────────────────
        try:
            call_key = (tool_name, json.dumps(tool_params, sort_keys=True))
        except (TypeError, ValueError):
            call_key = (tool_name, str(tool_params))

        if call_key in seen_calls:
            logger.warning(
                "Duplicate tool call '%s' by agent '%s' — forcing text fallback",
                tool_name, agent_key,
            )
            current_prompt = (
                f"You already called the tool '{tool_name}' with the same parameters. "
                f"Do NOT call it again. Please respond to the original request "
                f"using only your own knowledge, without calling any tools.\n\n"
                f"Original request: {user_input}"
            )
            available_tools = []
            continue
        seen_calls.add(call_key)

        # ── Gate 1: config-level allowed_tools check ──────────────────────
        # The agent config's allowed_tools is the primary authority.
        # Even if a tool exists in the registry, the agent can only use it
        # if its config explicitly lists it. This prevents hallucinated
        # tool names from slipping through.
        if tool_name not in config_allowed:
            logger.warning(
                "Agent '%s' requested tool '%s' which is not in its "
                "allowed_tools config", agent_key, tool_name,
            )
            current_prompt = (
                f"The tool '{tool_name}' is not in your allowed tools list. "
                f"You can only use these tools: {', '.join(sorted(config_allowed))}. "
                f"Please respond to the original request using only your "
                f"own knowledge, without calling any tools.\n\n"
                f"Original request: {user_input}"
            )
            available_tools = []
            continue

        # ── Gate 2: does this tool exist in the registry? ─────────────────
        tool_entry = get_tool(tool_name)
        if tool_entry is None:
            logger.warning(
                "Agent '%s' requested unknown tool '%s'", agent_key, tool_name
            )
            # Don't crash — ask the LLM to respond without the tool
            current_prompt = (
                f"The tool '{tool_name}' is not available. "
                f"Please respond to the original request using only your "
                f"own knowledge, without calling any tools.\n\n"
                f"Original request: {user_input}"
            )
            available_tools = []  # prevent further tool attempts
            continue

        # ── Gate 3: is this agent allowed in the registry? ────────────────
        if not is_agent_allowed(tool_name, agent_key):
            logger.warning(
                "Agent '%s' not allowed to use tool '%s'", agent_key, tool_name
            )
            current_prompt = (
                f"You are not authorized to use the tool '{tool_name}'. "
                f"Please respond to the original request using only your "
                f"own knowledge, without calling any tools.\n\n"
                f"Original request: {user_input}"
            )
            available_tools = []
            continue

        # ── Execute via tool_executor (validate → connect → log → exec) ──
        result = attempt_tool_call(
            tool_name=tool_name,
            agent_key=agent_key,
            workspace_id=workspace_id,
            db=db,
            input_args=tool_params,
            original_input=user_input,
        )

        status = result.get("status", "failure")

        # ── connect_required: return the Connect Link to the user ─────────
        if status == "connect_required":
            toolkit = result.get("toolkit", tool_entry.get("toolkit", ""))
            connect_url = result.get("connect_url")
            resume_token = result.get("resume_token", "")

            output_msg = _format_connect_required_message(
                tool_name, toolkit, connect_url, agent_name,
            )

            return {
                "agent": agent_key,
                "name": agent_name,
                "output": output_msg,
                "success": True,  # Not an error — it's an expected auth flow
                "tool_used": None,
                "connect_required": True,
                "connect_url": connect_url,
                "resume_token": resume_token,
                "toolkit": toolkit,
            }

        # ── failure: tell the user and fall back to text ──────────────────
        if status in ("failure", "validation_error", "timeout"):
            error_msg = result.get("error", "Unknown error")
            logger.error(
                "Tool '%s' failed for agent '%s': %s",
                tool_name, agent_key, error_msg,
            )
            # Give the LLM one chance to respond without the tool
            current_prompt = (
                f"I tried to use the tool '{tool_name}' but it failed "
                f"with error: {error_msg}. "
                f"Please respond to the original request using only your "
                f"own knowledge, without calling any tools.\n\n"
                f"Original request: {user_input}"
            )
            available_tools = []
            continue

        # ── success: feed tool output back into the LLM for final answer ──
        if status == "success":
            tool_used = tool_name
            tool_output = result.get("output", {})

            # Format tool output for the LLM
            if isinstance(tool_output, dict):
                tool_output_str = json.dumps(tool_output, indent=2, default=str)
            else:
                tool_output_str = str(tool_output)

            # Ask the LLM to produce a human-readable final response
            current_prompt = (
                f"I executed the tool '{tool_name}' successfully. "
                f"Here is the result:\n\n"
                f"```\n{tool_output_str[:2000]}\n```\n\n"
                f"Now provide a clear, helpful summary of this result "
                f"for the user. The original request was: {user_input}"
            )
            # Disable tools for the follow-up (we already executed one)
            available_tools = []
            continue

        # ── Unknown status — treat as failure ─────────────────────────────
        logger.error(
            "Unexpected tool status '%s' for tool '%s'", status, tool_name
        )
        current_prompt = (
            f"The tool '{tool_name}' returned an unexpected result. "
            f"Please respond to the original request normally.\n\n"
            f"Original request: {user_input}"
        )
        available_tools = []
        continue

    # ── Loop exhausted — return whatever we have ──────────────────────────
    logger.warning(
        "Tool loop exhausted after %d iterations for agent '%s'",
        MAX_TOOL_ITERATIONS, agent_key,
    )
    return {
        "agent": agent_key,
        "name": agent_name,
        "output": (
            f"⚠️ **{agent_name} reached the maximum number of tool attempts.**\n\n"
            f"Please try a simpler request or contact support."
        ),
        "success": False,
        "tool_used": tool_used,
    }
