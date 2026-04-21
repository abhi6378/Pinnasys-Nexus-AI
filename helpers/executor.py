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
from models.contracts import ConnectorContext, ExecutionConstraint, ToolPlan
from tools.capability_layer import (
    build_capability_request,
    build_tool_usage_guidance,
    prepare_tools_for_prompt,
    resolve_agent_tool_access,
)
from tools.composio_client import get_tool_schemas
from tools.tool_broker import build_tool_plan, get_tool_broker
from tools.tool_registry import (
    get_tools_by_names,
    get_tool,
)
from utils.logging_utils import log_event, log_exception

logger = logging.getLogger(__name__)

# Maximum number of tool calls per single run_agent invocation.
# Prevents runaway loops if the LLM keeps requesting tools.
MAX_TOOL_ITERATIONS = 5
UNVERIFIED_ACTION_PATTERN = re.compile(
    r"\b(sent|emailed|posted|published|scheduled|created|updated|synced|logged|delivered)\b",
    re.IGNORECASE,
)




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
When a live action or live data request is needed, think in terms of the
required capability first (for example: email.read, email.send, calendar.schedule),
then choose a concrete tool only from the provided tool list.
Distinguish clearly between:
- internal text work vs verified live system access
- read/discovery vs draft vs execute
- missing details that block execution vs details that can be inferred later
Prefer read or discovery before write when the target, destination, or record is unclear.
Never simulate inbox, Slack, calendar, CRM, spreadsheet, repository, or social-platform access.
Never claim a live action succeeded unless the verified tool result confirms it.
If execution is blocked, explain what is missing or what needs connection instead of pretending success.
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


def _extract_tool_plan(
    raw_output: str | dict,
    *,
    agent_key: str,
    user_input: str,
    route_context: dict | None = None,
    iteration: int = 1,
    default_capability_hint: dict | None = None,
    execution_constraint: ExecutionConstraint | dict | None = None,
    connector_context: ConnectorContext | dict | None = None,
) -> ToolPlan | None:
    parsed = _extract_tool_call(raw_output)
    if parsed is not None:
        tool_call = parsed.get("tool_call", {})
        return build_tool_plan(
            agent_key,
            user_intent=user_input,
            concrete_tool_name=tool_call.get("name"),
            params=tool_call.get("params", {}),
            llm_message=parsed.get("message", ""),
            route_decision=route_context,
            capability_hint=_merge_capability_hint(
                default_capability_hint,
                tool_call.get("capability_request") or {},
            ),
            iteration=iteration,
            connector_context=connector_context or (route_context or {}).get("connector_context"),
            execution_constraint=execution_constraint,
        )

    if isinstance(raw_output, dict):
        parsed_dict = raw_output
    else:
        text = (raw_output or "").strip()
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            parsed_dict = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    capability_request = parsed_dict.get("capability_request")
    if isinstance(capability_request, dict):
        merged_hint = _merge_capability_hint(default_capability_hint, capability_request)
        capability = build_capability_request(
            agent_key,
            user_input=user_input,
            route_decision=route_context,
            requested_tool_name=str(merged_hint.get("tool_name", "") or ""),
            capability_hint=merged_hint,
            connector_context=connector_context or (route_context or {}).get("connector_context"),
            execution_constraint=execution_constraint,
        )
        return ToolPlan(
            agent_key=agent_key,
            user_intent=user_input,
            llm_message=str(parsed_dict.get("message", "") or ""),
            capability=capability,
            concrete_tool_name=merged_hint.get("tool_name"),
            params=dict(merged_hint.get("params", {}) or capability_request.get("params", {}) or {}),
            raw_request=parsed_dict,
            iteration=iteration,
            execution_constraint=ExecutionConstraint.from_value(execution_constraint),
        )
    return None


def _merge_capability_hint(
    default_hint: dict | None,
    request_hint: dict | None,
) -> dict:
    merged = dict(default_hint or {})
    incoming = dict(request_hint or {})
    incoming_params = dict(incoming.pop("params", {}) or {})
    existing_params = dict(merged.get("params", {}) or {})
    merged.update(incoming)
    if existing_params or incoming_params:
        merged["params"] = {**existing_params, **incoming_params}
    return merged


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


def _build_verified_tool_history(tool_history: list[str]) -> str:
    if not tool_history:
        return "No verified tool executions."
    return "\n\n".join(tool_history)


def _build_followup_prompt(
    *,
    user_input: str,
    tool_history: list[str],
    tool_output_payload: dict | None,
) -> str:
    payload_json = json.dumps(tool_output_payload or {}, indent=2, default=str)[:3000]
    return (
        f"The original user request was:\n{user_input}\n\n"
        "Verified execution history:\n\n"
        f"{_build_verified_tool_history(tool_history)}\n\n"
        "Latest verified tool payload:\n"
        f"```json\n{payload_json}\n```\n\n"
        "Ground the answer only in the verified execution results above. "
        "If another listed tool is still required, respond with ONLY the next JSON tool_call. "
        "Otherwise return the final user-facing answer in plain text. "
        "Do not claim any unverified live action."
    )


def _looks_like_unverified_action_claim(
    text: str,
    *,
    route_context: dict | None = None,
    tool_used: str | None = None,
) -> bool:
    if tool_used or not text:
        return False
    route_operation = str((route_context or {}).get("operation", "") or "").lower()
    route_requires_live_data = bool((route_context or {}).get("requires_live_data"))
    if route_operation not in {"write", "schedule", "publish", "execute"} and not route_requires_live_data:
        return False
    return bool(UNVERIFIED_ACTION_PATTERN.search(text))






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
    route_context: dict | None = None,
    connector_context: ConnectorContext | dict | None = None,
    actor_user_id: str | None = None,
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
        route_context=route_context,
        connector_context=connector_context,
        actor_user_id=actor_user_id,
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
    route_context: dict | None = None,
    connector_context: ConnectorContext | dict | None = None,
    actor_user_id: str | None = None,
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
    current_prompt = user_input
    tool_used = None
    tool_output_payload = None
    tool_history: list[str] = []
    executed_tools: list[str] = []
    # Track (tool_name, params_hash) to prevent identical repeated calls.
    seen_calls: set[tuple[str, str]] = set()
    broker = get_tool_broker()
    connector = ConnectorContext.from_value(connector_context or (route_context or {}).get("connector_context"))
    execution_context = dict(workflow_state or {})
    if actor_user_id and "actor_user_id" not in execution_context:
        execution_context["actor_user_id"] = actor_user_id
    if not connector.is_auto():
        execution_context.setdefault("connector_context", connector.to_dict())
    workflow_capability_hint = dict(execution_context.get("capability_hint", {}) or {})
    step_execution_constraint = ExecutionConstraint.from_value(
        execution_context.get("step_execution_constraint")
    )
    prompt_connector = connector
    if step_execution_constraint.toolkit and step_execution_constraint.toolkit != connector.selected_toolkit:
        prompt_connector = ConnectorContext(
            mode="manual",
            selected_toolkit=step_execution_constraint.toolkit,
            selected_connector_key=step_execution_constraint.toolkit,
            selected_account_id=step_execution_constraint.account_id if step_execution_constraint.toolkit == connector.selected_toolkit else "",
            selected_account_alias=step_execution_constraint.account_alias if step_execution_constraint.toolkit == connector.selected_toolkit else "",
            enforce_toolkit=True,
            enforce_account=bool(step_execution_constraint.account_id and step_execution_constraint.toolkit == connector.selected_toolkit),
            source=step_execution_constraint.source or "workflow_step",
        )
    resolved_access = capability_access or resolve_agent_tool_access(agent_key, agent_config=agent)
    # Build the policy-level allow-set once for fast O(1) membership tests.
    config_allowed: set[str] = set(resolved_access.get("allowed_tools", []))
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_input})
    prompt_tools = available_tools
    workflow_capability_request = None
    if workflow_capability_hint or not step_execution_constraint.is_empty():
        workflow_capability_request = build_capability_request(
            agent_key,
            user_input=user_input,
            route_decision=route_context,
            capability_hint=workflow_capability_hint,
            connector_context=connector,
            execution_constraint=step_execution_constraint,
        )
    try:
        filter_result = prepare_tools_for_prompt(
            agent_key,
            available_tools,
            user_input,
            route_decision=route_context,
            capability_request=workflow_capability_request,
            connector_context=prompt_connector,
        )
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

    requested_schema_tools = [tool["tool_name"] for tool in prompt_tools]
    prompt_schema_cache = execution_context.setdefault("prompt_tool_schema_cache", {})
    cached_schemas = {
        tool_name: dict(prompt_schema_cache[tool_name])
        for tool_name in requested_schema_tools
        if isinstance(prompt_schema_cache.get(tool_name), dict)
    }
    missing_schema_tools = [
        tool_name for tool_name in requested_schema_tools if tool_name not in cached_schemas
    ]
    fetched_schemas = get_tool_schemas(workspace_id, missing_schema_tools) if missing_schema_tools else []
    for schema in fetched_schemas:
        schema_name = str(
            schema.get("function", {}).get("name")
            or schema.get("name")
            or schema.get("slug")
            or ""
        )
        if schema_name:
            prompt_schema_cache[schema_name] = dict(schema)
            cached_schemas[schema_name] = dict(schema)
    composio_tool_schemas = [
        dict(cached_schemas[tool_name])
        for tool_name in requested_schema_tools
        if tool_name in cached_schemas
    ]
    log_event(
        logger,
        logging.DEBUG,
        "agent.schema_cache",
        workspace_id=workspace_id,
        agent_name=agent_name,
        requested_count=len(requested_schema_tools),
        cache_hit_count=len(requested_schema_tools) - len(missing_schema_tools),
        fetched_count=len(fetched_schemas),
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

        # ── Parse: capability-first plan or plain text ────────────────────
        parsed = _extract_tool_call(raw_output)
        tool_plan = _extract_tool_plan(
            raw_output,
            agent_key=agent_key,
            user_input=user_input,
            route_context=route_context,
            iteration=iteration + 1,
            default_capability_hint=workflow_capability_hint,
            execution_constraint=step_execution_constraint,
            connector_context=connector,
        )

        if tool_plan is None:
            # Normal text response — return directly
            # We trust the LLM's intelligence here. If it doesn't emit a tool call,
            # it is either responding conversationally or politely explaining
            # that it lacks the required tools/permissions to fulfill the request.
            if execution_context.get("requires_live_tool") and not tool_used:
                return {
                    "agent": agent_key,
                    "name": agent_name,
                    "output": _format_validation_error_message(
                        str(execution_context.get("current_step") or "live workflow step"),
                        (
                            "This workflow step requires a verified live tool execution. "
                            "The response did not include a confirmed tool result, so the workflow was stopped."
                        ),
                        agent_name,
                    ),
                    "success": False,
                    "mode": "validation_error",
                    "tool_used": tool_used,
                }
            if _looks_like_unverified_action_claim(
                str(raw_output),
                route_context=route_context,
                tool_used=tool_used,
            ):
                return {
                    "agent": agent_key,
                    "name": agent_name,
                    "output": _format_validation_error_message(
                        route_context.get("operation", "live_action") if isinstance(route_context, dict) else "live_action",
                        "The response appeared to claim a live action without a verified tool result.",
                        agent_name,
                    ),
                    "success": False,
                    "mode": "validation_error",
                    "tool_used": tool_used,
                }
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": raw_output,
                "success": True,
                "tool_used": tool_used,
                "tool_output": tool_output_payload,
            }

        # ── Structured tool plan detected ─────────────────────────────────
        tool_name = tool_plan.concrete_tool_name or ""
        tool_params = tool_plan.params
        message = tool_plan.llm_message

        log_event(
            logger,
            logging.INFO,
            "agent.tool_requested",
            workspace_id=workspace_id,
            agent_name=agent_name,
            tool_name=tool_name or "(capability_request)",
            iteration=iteration + 1,
            max_iterations=MAX_TOOL_ITERATIONS,
            capability_group=tool_plan.capability.capability_group,
            action_class=tool_plan.capability.action_class,
            execution_mode=tool_plan.capability.execution_mode,
        )

        # ── Guard: prevent identical duplicate tool calls ─────────────────
        try:
            call_identity = {
                "tool_name": tool_name,
                "capability_group": tool_plan.capability.capability_group,
                "action_class": tool_plan.capability.action_class,
                "params": tool_params,
            }
            call_key = (tool_name or tool_plan.capability.capability_group, json.dumps(call_identity, sort_keys=True))
        except (TypeError, ValueError):
            call_key = (tool_name or tool_plan.capability.capability_group, str(tool_params))

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
                    tool_name or tool_plan.capability.capability_group,
                    "The same tool call was requested more than once with identical parameters.",
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }
        seen_calls.add(call_key)

        resolution = broker.resolve(
            tool_plan,
            workspace_id=workspace_id,
            db=db,
            allowed_tool_names=sorted(config_allowed),
            connector_context=connector,
        )
        resolved_tool_name = resolution.tool_name or tool_name
        tool_entry = get_tool(resolved_tool_name) if resolved_tool_name else None

        if resolution.status == "invalid_tool":
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_not_in_policy",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=resolved_tool_name or tool_name,
                capability_group=tool_plan.capability.capability_group,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_invalid_tool_message(
                    resolved_tool_name or tool_name or tool_plan.capability.capability_group,
                    resolution.reason
                    or (
                        "This tool is not in the agent's allowed tool policy. "
                        f"Allowed tools: {', '.join(sorted(config_allowed)) or 'none'}."
                    ),
                    agent_name,
                ),
                "success": False,
                "mode": "invalid_tool",
                "tool_used": tool_used,
            }

        if resolution.status == "validation_error":
            log_event(
                logger,
                logging.WARNING,
                "agent.tool_not_authorized",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=resolved_tool_name or tool_name,
                capability_group=tool_plan.capability.capability_group,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_validation_error_message(
                    resolved_tool_name or tool_name or tool_plan.capability.capability_group,
                    resolution.reason or "This agent is not authorized to use the requested tool.",
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }

        execution = broker.execute(
            resolution,
            tool_plan,
            workspace_id=workspace_id,
            db=db,
            original_input=user_input,
            context_json=execution_context,
            connector_context=connector,
        )
        result = execution.to_legacy_dict()
        status = execution.status
        log_event(
            logger,
            logging.INFO,
            "agent.tool_resolved",
            workspace_id=workspace_id,
            agent_name=agent_name,
            tool_name=resolved_tool_name or tool_name,
            approval_required=resolution.approval_requirement.required,
            risk_level=resolution.approval_requirement.risk_level,
            execution_mode=resolution.execution_mode,
            idempotency_key=resolution.idempotency_key,
        )

        # ── connect_required: return the Connect Link to the user ─────────
        if status == "connect_required":
            toolkit = execution.toolkit or (tool_entry.get("toolkit", "") if tool_entry else "")
            connect_url = result.get("connect_url")
            resume_token = result.get("resume_token", "")

            output_msg = _format_connect_required_message(
                resolved_tool_name or tool_name, toolkit, connect_url, agent_name,
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
            toolkit = execution.toolkit or (tool_entry.get("toolkit", "") if tool_entry else "")
            error_detail = result.get("error") or (
                f"The {toolkit} integration is unavailable, so this request was not executed."
            )

            return {
                "agent": agent_key,
                "name": agent_name,
                "output": (
                    f"⚠️ **{agent_name} cannot access `{resolved_tool_name or tool_name}` right now.**\n\n"
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
                    resolved_tool_name or tool_name or tool_plan.capability.capability_group,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "invalid_tool",
                "tool_used": tool_used,
            }

        if status == "validation_error":
            error_msg = result.get("error", "Invalid tool request")
            response = {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_validation_error_message(
                    resolved_tool_name or tool_name or tool_plan.capability.capability_group,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "validation_error",
                "tool_used": tool_used,
            }
            for field in ("approval_required", "approval_requirement", "resume_token", "pending_kind"):
                if field in result:
                    response[field] = result.get(field)
            return response

        # ── failure: stop cleanly instead of falling back to generic text ─
        if status in ("failure", "timeout"):
            error_msg = result.get("error", "Unknown error")
            log_event(
                logger,
                logging.ERROR,
                "agent.tool_failed",
                workspace_id=workspace_id,
                agent_name=agent_name,
                tool_name=resolved_tool_name or tool_name,
                error_type=status,
            )
            return {
                "agent": agent_key,
                "name": agent_name,
                "output": _format_tool_error_message(
                    resolved_tool_name or tool_name or tool_plan.capability.capability_group,
                    error_msg,
                    agent_name,
                ),
                "success": False,
                "mode": "tool_error",
                "tool_used": tool_used,
            }

        # ── success: feed tool output back into the LLM for final answer ──
        if status == "success":
            tool_used = resolved_tool_name or tool_name
            executed_tools.append(tool_used)
            tool_output = result.get("output", {})
            tool_output_payload = (
                tool_output if isinstance(tool_output, dict) else {"data": tool_output}
            )

            if isinstance(tool_output, dict):
                tool_output_str = json.dumps(tool_output, indent=2, default=str)
            else:
                tool_output_str = str(tool_output)

            tool_history.append(
                f"Tool: {tool_used}\nResult:\n{tool_output_str[:2000]}"
            )

            if use_native_tool_calling:
                openai_tool_call = parsed.get("openai_tool_call") or {
                    "id": parsed.get("tool_call_id", ""),
                    "type": "function",
                    "function": {
                        "name": tool_used,
                        "arguments": json.dumps(resolution.normalized_params or tool_params, default=str),
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
                current_prompt = _build_followup_prompt(
                    user_input=user_input,
                    tool_history=tool_history,
                    tool_output_payload=tool_output_payload,
                )
            continue

        # ── Unknown status — treat as failure ─────────────────────────────
        log_event(
            logger,
            logging.ERROR,
            "agent.tool_unexpected_status",
            workspace_id=workspace_id,
            agent_name=agent_name,
            tool_name=resolved_tool_name or tool_name,
            error_type=status,
        )
        return {
            "agent": agent_key,
            "name": agent_name,
            "output": _format_tool_error_message(
                resolved_tool_name or tool_name or tool_plan.capability.capability_group,
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
