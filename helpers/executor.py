"""
helpers/executor.py  —  Runs a single helper with Brain AI context

Backward compatibility:
  - Signature is  run_agent(agent_key, user_input, brain_context, **kwargs)
  - Existing callers (handler.py, engine.py) pass 3 positional args → text-only
  - When workspace_id + db are passed, tool-aware mode activates for agents
    whose config has  tool_mode == "tool_enabled"  and  allowed_tools != [].
  - Return contract is the same:  {agent, name?, output, success}
    plus optional keys:  {tool_used, connect_required, connect_url, resume_token}

Tool execution flow:
  1. Resolve tool policy from capability groups, with legacy allowlist fallback.
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
from tools.capability_layer import (
    build_tool_usage_guidance,
    prepare_tools_for_prompt,
    resolve_agent_tool_access,
)
from tools.composio_client import get_tool_schemas
from tools.tool_registry import (
    get_tools_by_names,
    is_agent_allowed,
    get_tool,
)
from utils.logging_utils import log_event, log_exception

logger = logging.getLogger(__name__)

# Maximum number of tool calls per single run_agent invocation.
# Prevents runaway loops if the LLM keeps requesting tools.
MAX_TOOL_ITERATIONS = 5




# ── System prompt builder ─────────────────────────────────────────────────────

def build_system_prompt(agent: dict, brain_context: str, tool_guidance: str = "") -> str:
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
    tool_instructions = tool_guidance or agent.get("tool_instructions")
    if tool_instructions:
        base += (
            f"\n--- TOOL USAGE GUIDANCE ---\n"
            f"{tool_instructions}\n"
            f"--- END GUIDANCE ---\n"
        )

    return base


# ── Tool-call JSON parser ─────────────────────────────────────────────────────

def _extract_tool_call(raw_output: str | dict) -> Optional[dict]:
    """
    Try to extract a structured tool_call from the LLM's raw output.

    The LLM is instructed to respond with:
      {"message": "...", "tool_call": {"name": "TOOL_NAME", "params": {...}}}

    We accept this both as:
      - Pure JSON (the entire response is JSON)
      - JSON inside a ```json code fence

    Returns the parsed dict if a tool_call is found, or None for plain text.
    """
    if isinstance(raw_output, dict):
        tool_call = raw_output.get("tool_call")
        if isinstance(tool_call, dict) and tool_call.get("name"):
            return raw_output
        return None

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
        f"_The request stopped here so I do not invent a manual workaround._"
    )


def _format_invalid_tool_message(tool_name: str, error: str, agent_name: str) -> str:
    return (
        f"⚠️ **{agent_name} requested an invalid tool: `{tool_name}`.**\n\n"
        f"{error}\n\n"
        f"_This request was stopped so the app does not pretend the action succeeded._"
    )


def _format_validation_error_message(tool_name: str, error: str, agent_name: str) -> str:
    return (
        f"⚠️ **{agent_name} could not run `{tool_name}` because the request was invalid.**\n\n"
        f"{error}"
    )






# ── Main entry point ──────────────────────────────────────────────────────────

def run_agent(
    agent_key: str,
    user_input: str,
    brain_context: str = "",
    *,
    workspace_id: str = "",
    db=None,
    workflow_state: dict = None,
    history: list[dict] | None = None,
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
        log_event(
            logger,
            logging.WARNING,
            "agent.config_missing",
            workspace_id=workspace_id,
            agent_name=agent_key,
        )
        return {
            "agent": agent_key,
            "output": f"Unknown agent: {agent_key}",
            "success": False,
        }

    capability_access = resolve_agent_tool_access(agent_key, agent_config=agent)
    tool_mode = capability_access.get("tool_mode", agent.get("tool_mode", "text_only"))
    config_tools = capability_access.get("allowed_tools", [])
    configured_tool_names = list(agent.get("allowed_tools", []))
    tool_guidance = build_tool_usage_guidance(agent_key, agent_config=agent)
    system_prompt = build_system_prompt(
        agent,
        brain_context or "No context available.",
        tool_guidance=tool_guidance,
    )
    agent_name = agent["name"]
    log_event(
        logger,
        logging.INFO,
        "agent.run.enter",
        workspace_id=workspace_id,
        agent_name=agent_name,
        tool_mode=tool_mode,
        tool_resolution_source=capability_access.get("resolution_source", "unknown"),
        has_workflow_state=bool(workflow_state),
    )

    # ── Determine if this agent has tools available ───────────────────────
    # Tools are only available when ALL three conditions are met:
    #   1. workspace_id and db are provided (caller opted in)
    #   2. Agent config has tool_mode == "tool_enabled"
    #   3. Agent config has a non-empty allowed_tools list
    # The config's allowed_tools is the SINGLE SOURCE OF TRUTH for what
    # tools this agent may use. The registry just provides metadata.
    available_tools = []

    if workspace_id and db and tool_mode == "tool_enabled" and config_tools:
        available_tools = get_tools_by_names(config_tools)

    invalid_config_tools = capability_access.get("invalid_legacy_tools", [])
    if tool_mode == "tool_enabled" and invalid_config_tools:
        log_event(
            logger,
            logging.ERROR,
            "agent.invalid_tool_config",
            workspace_id=workspace_id,
            agent_name=agent_name,
            invalid_tool_names=invalid_config_tools,
        )

    if tool_mode == "tool_enabled" and configured_tool_names and not available_tools and workspace_id and db:
        invalid_names = ", ".join(invalid_config_tools or configured_tool_names)
        return {
            "agent": agent_key,
            "name": agent_name,
            "output": _format_invalid_tool_message(
                invalid_names,
                "The configured tool list for this agent does not match the registry.",
                agent_name,
            ),
            "success": False,
            "mode": "invalid_tool",
        }

    # ── Text-only path (no tools, or tool infrastructure not provided) ────
    if not available_tools:
        try:
            output = generate(
                prompt=user_input,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=1500,
                history=history,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": output,
                "success": True,
            }
        except Exception as e:
            log_exception(
                logger,
                "agent.run.failed",
                e,
                workspace_id=workspace_id,
                agent_name=agent_name,
                stage="text_generation",
            )
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
        workflow_state=workflow_state,
        history=history,
        capability_access=capability_access,
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
    workflow_state: dict = None,
    history: list[dict] | None = None,
    capability_access: dict | None = None,
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
    tool_output_payload = None
    tool_history: list[str] = []
    executed_tools: list[str] = []
    # Track (tool_name, params_hash) to prevent identical repeated calls.
    seen_calls: set[tuple[str, str]] = set()
    resolved_access = capability_access or resolve_agent_tool_access(agent_key, agent_config=agent)
    # Build the policy-level allow-set once for fast O(1) membership tests.
    config_allowed: set[str] = set(resolved_access.get("allowed_tools", []))
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_input})
    prompt_tools = available_tools
    try:
        filter_result = prepare_tools_for_prompt(agent_key, available_tools, user_input)
        prompt_tools = filter_result.get("tools") or available_tools
        log_event(
            logger,
            logging.INFO,
            "agent.tool_filter",
            workspace_id=workspace_id,
            agent_name=agent_name,
            filter_applied=filter_result.get("filter_applied", False),
            original_tool_count=len(available_tools),
            prompt_tool_count=len(prompt_tools),
            capability_groups=filter_result.get("groups", []),
            filter_reason=filter_result.get("reason", ""),
        )
    except Exception as exc:
        log_exception(
            logger,
            "agent.tool_filter_failed",
            exc,
            workspace_id=workspace_id,
            agent_name=agent_name,
        )
        prompt_tools = available_tools

    composio_tool_schemas = get_tool_schemas(
        workspace_id,
        [tool["tool_name"] for tool in prompt_tools],
    )
    use_native_tool_calling = bool(composio_tool_schemas)
    log_event(
        logger,
        logging.INFO,
        "agent.tools.enter",
        workspace_id=workspace_id,
        agent_name=agent_name,
        available_tool_count=len(prompt_tools),
        native_tool_calling=use_native_tool_calling,
    )

    for iteration in range(MAX_TOOL_ITERATIONS):
        try:
            raw_output = generate_with_tool_awareness(
                prompt=current_prompt,
                system_prompt=system_prompt,
                available_tools=prompt_tools,
                temperature=0.7,
                max_tokens=2000,
                composio_tool_schemas=composio_tool_schemas,
                history=history if not use_native_tool_calling else None,
                messages=messages if use_native_tool_calling else None,
            )
        except Exception as e:
            log_exception(
                logger,
                "agent.tools.llm_failed",
                e,
                workspace_id=workspace_id,
                agent_name=agent_name,
                iteration=iteration + 1,
            )
            return {
                "agent": agent_key,
                "output": f"Error: {str(e)}",
                "success": False,
            }

        # ── Parse: is this a tool call or plain text? ─────────────────────
        parsed = _extract_tool_call(raw_output)

        if parsed is None:
            # Normal text response — return directly
            # We trust the LLM's intelligence here. If it doesn't emit a tool call,
            # it is either responding conversationally or politely explaining
            # that it lacks the required tools/permissions to fulfill the request.
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": raw_output,
                "success": True,
                "tool_used": tool_used,
                "tool_output": tool_output_payload,
            }

        # ── Structured tool call detected ─────────────────────────────────
        tool_call = parsed["tool_call"]
        tool_name = tool_call.get("name", "")
        tool_params = tool_call.get("params", {})
        message = parsed.get("message", "")

        log_event(
            logger,
            logging.INFO,
            "agent.tool_requested",
            workspace_id=workspace_id,
            agent_name=agent_name,
            tool_name=tool_name,
            iteration=iteration + 1,
            max_iterations=MAX_TOOL_ITERATIONS,
        )

        # ── Guard: prevent identical duplicate tool calls ─────────────────
        try:
            call_key = (tool_name, json.dumps(tool_params, sort_keys=True))
        except (TypeError, ValueError):
            call_key = (tool_name, str(tool_params))

        if call_key in seen_calls:
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_duplicate",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=tool_name,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_validation_error_message(
                    tool_name,
                    "The same tool call was requested more than once with identical parameters.",
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }
        seen_calls.add(call_key)

        # ── Gate 1: config-level allowed_tools check ──────────────────────
        # The agent config's allowed_tools is the primary authority.
        # Even if a tool exists in the registry, the agent can only use it
        # if its config explicitly lists it. This prevents hallucinated
        # tool names from slipping through.
        if tool_name not in config_allowed:
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_not_in_policy",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=tool_name,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_invalid_tool_message(
                    tool_name,
                    (
                        "This tool is not in the agent's allowed tool policy. "
                        f"Allowed tools: {', '.join(sorted(config_allowed)) or 'none'}."
                    ),
                    agent_name,
                ),
                "success": False,
                "mode": "invalid_tool",
                "tool_used": tool_used,
            }

        # ── Gate 2: does this tool exist in the registry? ─────────────────
        tool_entry = get_tool(tool_name)
        if tool_entry is None:
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_unknown",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=tool_name,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_invalid_tool_message(
                    tool_name,
                    "This tool does not exist in the registry.",
                    agent_name,
                ),
                "success": False,
                "mode": "invalid_tool",
                "tool_used": tool_used,
            }

        # ── Gate 3: is this agent allowed in the registry? ────────────────
        if not is_agent_allowed(tool_name, agent_key):
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_not_authorized",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=tool_name,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_validation_error_message(
                    tool_name,
                    "This agent is not authorized to use the requested tool.",
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }

        # ── Execute via tool_executor (validate → connect → log → exec) ──
        result = attempt_tool_call(
            tool_name=tool_name,
            agent_key=agent_key,
            workspace_id=workspace_id,
            db=db,
            input_args=tool_params,
            original_input=user_input,
            context_json=workflow_state, # Pass workflow state here
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

        # ── auth_unavailable: return failure without a link ───────────────
        if status == "auth_unavailable":
            toolkit = result.get("toolkit", tool_entry.get("toolkit", ""))
            error_detail = result.get("error") or (
                f"The {toolkit} integration is unavailable, so this request was not executed."
            )

            return {
                "agent": agent_key,
                "name": agent_name,
                "output": (
                    f"⚠️ **{agent_name} cannot access `{tool_name}` right now.**\n\n"
                    f"{error_detail}"
                ),
                "success": False,
                "tool_used": None,
                "connect_required": False,
                "mode": "auth_unavailable",
                "toolkit": toolkit,
                "auth_error": error_detail,
            }

        if status == "invalid_tool":
            error_msg = result.get("error", "Unknown tool error")
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_invalid_tool_message(
                    tool_name,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "invalid_tool",
                "tool_used": tool_used,
            }

        if status == "validation_error":
            error_msg = result.get("error", "Invalid tool request")
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_validation_error_message(
                    tool_name,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }

        # ── failure: stop cleanly instead of falling back to generic text ─
        if status in ("failure", "timeout"):
            error_msg = result.get("error", "Unknown error")
            log_event(
                logger,
                logging.ERROR,
                "agent.tool_failed",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=tool_name,
                error_type=status,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_tool_error_message(
                    tool_name,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "tool_error",
                "tool_used": tool_used,
            }

        # ── success: feed tool output back into the LLM for final answer ──
        if status == "success":
            tool_used = tool_name
            executed_tools.append(tool_name)
            tool_output = result.get("output", {})
            tool_output_payload = (
                tool_output if isinstance(tool_output, dict) else {"data": tool_output}
            )

            if isinstance(tool_output, dict):
                tool_output_str = json.dumps(tool_output, indent=2, default=str)
            else:
                tool_output_str = str(tool_output)

            tool_history.append(
                f"Tool: {tool_name}\nResult:\n{tool_output_str[:2000]}"
            )

            if use_native_tool_calling:
                openai_tool_call = parsed.get("openai_tool_call") or {
                    "id": parsed.get("tool_call_id", ""),
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_params, default=str),
                    },
                }
                messages.append(
                    {
                        "role": "assistant",
                        "content": message or None,
                        "tool_calls": [openai_tool_call],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": openai_tool_call["id"],
                        "content": json.dumps(tool_output_payload, default=str),
                    }
                )
            else:
                current_prompt = (
                    f"The original user request was:\n{user_input}\n\n"
                    f"Tool execution history so far:\n\n"
                    f"{chr(10).join(tool_history)}\n\n"
                    "If another listed tool is still needed to fully satisfy the user's request, "
                    "respond with ONLY the JSON tool_call for the next step. "
                    "Otherwise, provide the final user-facing answer in plain text. "
                    "Never claim a live action happened unless it appears in the tool execution history above."
                )
            continue

        # ── Unknown status — treat as failure ─────────────────────────────
        log_event(
            logger,
            logging.ERROR,
            "agent.tool_unexpected_status",
            workspace_id=workspace_id,
            agent_name=agent_name,
            tool_name=tool_name,
            error_type=status,
        )
        return {
            "agent": agent_key,
            "name": agent_name,
            "output": _format_tool_error_message(
                tool_name,
                f"Unexpected tool status: {status}",
                agent_name,
            ),
            "success": False,
            "mode": "tool_error",
            "tool_used": tool_used,
        }

    # ── Loop exhausted — return whatever we have ──────────────────────────
    log_event(
        logger,
        logging.WARNING,
        "agent.tool_loop_exhausted",
        workspace_id=workspace_id,
        agent_name=agent_name,
        max_iterations=MAX_TOOL_ITERATIONS,
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
