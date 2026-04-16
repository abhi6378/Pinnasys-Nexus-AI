"""
tools/capability_layer.py  —  Backward-compatible capability derivation helpers.

This module keeps tool execution conservative:
  - Agent tool access is derived from the canonical tool registry wherever possible.
  - Explicit legacy allowlists passed by older callers still work.
  - If capability-group metadata is incomplete or ambiguous, callers fall back
    to the broader registry-derived per-agent tool set.
"""
from __future__ import annotations

from models.contracts import ApprovalRequirement, CapabilityRequest, ConnectorContext, RouteDecision, RouteStepSkeleton
from helpers.agent_capabilities import get_capability_policy
from tools.tool_registry import (
    get_capability_group_metadata,
    get_capability_groups_for_agent,
    get_tool_execution_mode,
    get_tool_metadata_gaps,
    get_tool_names_for_capability_groups,
    get_tool,
    get_tools_for_capability_request,
    get_toolkit_metadata,
    get_toolkit_label,
    normalize_toolkit_key,
    get_tools_by_names,
    get_tools_for_agent,
    get_tools_missing_capability_groups,
    split_valid_tool_names,
)


CAPABILITY_GROUPS: dict[str, dict[str, object]] = {
    "email": {
        "label": "email operations",
        "keywords": ("email", "mail", "gmail", "inbox", "reply", "draft"),
        "operations": ("read", "write", "draft"),
    },
    "calendar": {
        "label": "calendar scheduling",
        "keywords": ("calendar", "meeting", "schedule", "availability", "invite"),
        "operations": ("read", "schedule", "write"),
    },
    "slack": {
        "label": "Slack messaging",
        "keywords": ("slack", "channel", "dm", "workspace message"),
        "operations": ("read", "write"),
    },
    "crm": {
        "label": "CRM updates",
        "keywords": ("hubspot", "crm", "contact", "deal", "lead"),
        "operations": ("read", "write"),
    },
    "sheets": {
        "label": "spreadsheet updates",
        "keywords": ("sheet", "sheets", "spreadsheet", "row", "cells"),
        "operations": ("read", "write"),
    },
    "github": {
        "label": "GitHub issue work",
        "keywords": ("github", "repo", "repository", "issue", "bug"),
        "operations": ("read", "write"),
    },
    "research": {
        "label": "live web research",
        "keywords": ("research", "search", "lookup", "find information", "web"),
        "operations": ("research", "read"),
    },
    "social": {
        "label": "social publishing and discovery",
        "keywords": ("twitter", "tweet", "linkedin", "social post", "post on x", "post on linkedin"),
        "operations": ("read", "write", "publish"),
    },
}


def _clone_tool_for_prompt(tool: dict) -> dict:
    prompt_tool = dict(tool)
    action = tool.get("description") or tool.get("action", "") or tool.get("tool_name", "")
    toolkit_label = get_toolkit_label(tool.get("toolkit", ""))
    if toolkit_label and toolkit_label.lower() not in action.lower():
        action = f"{action} via {toolkit_label}"
    prompt_tool["action"] = action
    prompt_tool["description"] = action
    return prompt_tool


def _infer_relevant_groups(user_input: str) -> list[str]:
    lowered = (user_input or "").strip().lower()
    if not lowered:
        return []
    matches: list[str] = []
    for group_name, meta in CAPABILITY_GROUPS.items():
        keywords = meta.get("keywords", ())
        if any(keyword in lowered for keyword in keywords):
            matches.append(group_name)
    return matches


def _infer_action_class(user_input: str = "", operation: str = "", tool_name: str = "") -> str:
    lowered = f"{user_input} {operation} {tool_name}".lower()
    if any(token in lowered for token in ("send", "reply", "message", "post", "publish")):
        return "send" if "publish" not in lowered and "post" not in lowered else "publish"
    if "draft" in lowered:
        return "draft"
    if any(token in lowered for token in ("create", "append", "update", "schedule")):
        return "create"
    if any(token in lowered for token in ("research", "search", "lookup")):
        return "search"
    return "read"


def _coerce_route_decision(route_decision: RouteDecision | dict | None) -> RouteDecision | None:
    if route_decision is None:
        return None
    if isinstance(route_decision, RouteDecision):
        return route_decision
    if isinstance(route_decision, dict):
        return RouteDecision(
            route_type=route_decision.get("route_type", "single_agent"),
            confidence=route_decision.get("confidence", 0.5),
            intent=route_decision.get("intent") or route_decision.get("primary_intent") or "",
            domain=route_decision.get("domain", ""),
            system_family=route_decision.get("system_family", ""),
            operation=route_decision.get("operation", ""),
            requires_live_data=route_decision.get("requires_live_data", False),
            approval_required=ApprovalRequirement.from_value(
                route_decision.get("approval_requirement")
                or {
                    "required": route_decision.get("approval_required", False),
                    "risk_level": "medium" if route_decision.get("approval_required") else "low",
                }
            ),
            selected_agent=route_decision.get("selected_agent"),
            selected_workflow=route_decision.get("selected_workflow"),
            missing_info=list(route_decision.get("missing_info", [])),
            reason=route_decision.get("reason", ""),
            ordered_steps=[step for step in (RouteStepSkeleton.from_value(item) for item in route_decision.get("steps", [])) if step],
            clarification_question=route_decision.get("clarification_question", ""),
            risk_flags=list(route_decision.get("risk_flags", [])),
            route_method=route_decision.get("route_method", "llm_router"),
        )
    return None


def _derive_registry_access(agent_key: str) -> dict:
    tool_entries = get_tools_for_agent(agent_key)
    tool_names = [tool["tool_name"] for tool in tool_entries]
    capability_groups = get_capability_groups_for_agent(agent_key)
    missing_groups = get_tools_missing_capability_groups(agent_key)
    metadata_gaps = get_tool_metadata_gaps(tool_names)
    return {
        "tool_entries": tool_entries,
        "tool_names": tool_names,
        "capability_groups": capability_groups,
        "missing_group_tools": missing_groups,
        "metadata_gaps": metadata_gaps,
    }


def resolve_agent_tool_access(agent_key: str, agent_config: dict | None = None) -> dict:
    """
    Resolve the effective tool policy for an agent.

    Resolution order:
      1. Explicit allowlist from a legacy caller-provided agent config.
      2. Registry-derived capability-group mapping.
      3. Broader per-agent registry fallback when groups are incomplete/ambiguous.
    """
    policy = get_capability_policy(agent_key) or {}
    merged_agent = agent_config or {}
    registry_access = _derive_registry_access(agent_key)

    explicit_allowed_tools = None
    invalid_legacy_tools: list[str] = []
    if "allowed_tools" in merged_agent:
        valid_tools, invalid_legacy_tools = split_valid_tool_names(list(merged_agent.get("allowed_tools") or []))
        explicit_allowed_tools = valid_tools

    derived_capability_groups = list(
        merged_agent.get("capability_groups")
        or policy.get("capability_groups")
        or registry_access["capability_groups"]
    )
    grouped_tool_names = get_tool_names_for_capability_groups(agent_key, derived_capability_groups)
    registry_tool_names = list(registry_access["tool_names"])
    missing_group_tools = [
        tool_name for tool_name in registry_tool_names
        if tool_name not in grouped_tool_names
    ]

    if explicit_allowed_tools is not None:
        allowed_tools = explicit_allowed_tools
        resolution_source = "legacy_explicit_allowlist"
        resolution_reason = "explicit_agent_config"
        fallback_used = bool(invalid_legacy_tools)
    elif missing_group_tools or registry_access["missing_group_tools"]:
        allowed_tools = registry_tool_names
        resolution_source = "registry_agent_fallback"
        resolution_reason = "incomplete_capability_metadata"
        fallback_used = True
    else:
        allowed_tools = grouped_tool_names
        resolution_source = "capability_groups"
        resolution_reason = "registry_tags"
        fallback_used = False

    if explicit_allowed_tools is None and not allowed_tools:
        allowed_tools = registry_tool_names
        resolution_source = "registry_agent_fallback" if registry_tool_names else "none"
        resolution_reason = "no_capability_match" if registry_tool_names else "no_tool_access"
        fallback_used = bool(registry_tool_names)

    requires_auth = bool(
        merged_agent.get("requires_auth")
        if "requires_auth" in merged_agent
        else policy.get("requires_auth")
        if "requires_auth" in policy
        else any(tool.get("requires_auth") for tool in registry_access["tool_entries"])
    )
    tool_mode = str(
        merged_agent.get("tool_mode")
        or policy.get("tool_mode")
        or ("tool_enabled" if allowed_tools else "text_only")
    )

    return {
        "tool_mode": tool_mode,
        "requires_auth": requires_auth,
        "capability_groups": list(derived_capability_groups),
        "allowed_tools": list(allowed_tools),
        "legacy_allowed_tools": list(explicit_allowed_tools or []),
        "resolution_source": resolution_source,
        "resolution_reason": resolution_reason,
        "fallback_used": fallback_used,
        "invalid_legacy_tools": list(invalid_legacy_tools),
        "tool_policy": str(merged_agent.get("tool_policy") or policy.get("tool_policy") or "").strip(),
        "legacy_tool_instructions": str(merged_agent.get("tool_instructions") or "").strip(),
        "metadata_gaps": registry_access["metadata_gaps"],
        "missing_group_tools": list(registry_access["missing_group_tools"]),
    }


def build_capability_request(
    agent_key: str,
    user_input: str = "",
    *,
    route_decision: RouteDecision | dict | None = None,
    requested_tool_name: str = "",
    capability_hint: dict | None = None,
    connector_context: ConnectorContext | dict | None = None,
) -> CapabilityRequest:
    route = _coerce_route_decision(route_decision)
    connector = ConnectorContext.from_value(connector_context)
    tool_entry = get_tool(requested_tool_name) if requested_tool_name else None
    hint = dict(capability_hint or {})

    capability_group = (
        hint.get("capability_group")
        or ((tool_entry.get("capability_profile", {}) or {}).get("capability_group") if tool_entry else "")
        or (route.system_family if route and route.system_family in CAPABILITY_GROUPS else "")
        or next(iter(_infer_relevant_groups(user_input)), "")
    )
    operation = (
        hint.get("operation")
        or (route.operation if route else "")
        or (tool_entry.get("operation_types", [""]) or [""])[0]
    )
    route_toolkit_family = ""
    if route and get_toolkit_metadata(route.system_family):
        route_toolkit_family = route.system_family
    toolkit_family = (
        hint.get("toolkit_family")
        or (tool_entry or {}).get("toolkit", "")
        or route_toolkit_family
    )
    if not connector.is_auto() and connector.selected_toolkit:
        toolkit_family = normalize_toolkit_key(connector.selected_toolkit) or connector.selected_toolkit
    action_class = (
        hint.get("action_class")
        or (tool_entry.get("action_classes", [""]) if tool_entry else [""])[0]
        or _infer_action_class(user_input=user_input, operation=operation, tool_name=requested_tool_name)
    )
    capability_meta = get_capability_group_metadata(capability_group)
    risk_level = (
        hint.get("risk_level")
        or (tool_entry or {}).get("risk_level")
        or capability_meta.get("default_risk_level")
        or ("medium" if route and route.approval_required.required else "low")
    )
    requires_live_data = bool(
        hint.get("requires_live_data")
        if "requires_live_data" in hint
        else route.requires_live_data if route else capability_group in {"research"}
    )
    preferred_tools = list(
        hint.get("preferred_tools")
        or ([requested_tool_name] if requested_tool_name else [])
        or (((tool_entry.get("capability_profile", {}) or {}).get("preferred_tools")) if tool_entry else [])
        or []
    )
    fallback_tools = list(
        hint.get("fallback_tools")
        or (((tool_entry.get("capability_profile", {}) or {}).get("fallback_tools")) if tool_entry else [])
        or []
    )
    execution_mode = (
        hint.get("execution_mode")
        or (get_tool_execution_mode(requested_tool_name) if requested_tool_name else "")
        or ("draft" if operation == "draft" else "execute" if operation in {"write", "schedule"} else "read")
    )

    return CapabilityRequest(
        capability_group=capability_group,
        toolkit_family=toolkit_family,
        action_class=action_class,
        operation=operation,
        risk_level=str(risk_level or "low"),
        requires_live_data=requires_live_data,
        execution_mode=execution_mode,
        preferred_tools=preferred_tools,
        fallback_tools=fallback_tools,
        metadata={
            "agent_key": agent_key,
            "route_intent": route.intent if route else "",
            "connector_context": connector.to_dict(),
        },
    )


def resolve_capability_request(
    agent_key: str,
    capability_request: CapabilityRequest,
    *,
    allowed_tool_names: list[str] | None = None,
    connector_context: ConnectorContext | dict | None = None,
) -> dict:
    connector = ConnectorContext.from_value(connector_context or capability_request.metadata.get("connector_context"))
    toolkit_family = capability_request.toolkit_family
    if not connector.is_auto() and connector.selected_toolkit:
        toolkit_family = normalize_toolkit_key(connector.selected_toolkit) or connector.selected_toolkit
    allowed_entries = get_tools_for_capability_request(
        agent_key,
        capability_group=capability_request.capability_group,
        toolkit_family=toolkit_family,
        action_class=capability_request.action_class,
    )
    allowed_set = set(allowed_tool_names or [])
    if allowed_set:
        allowed_entries = [
            entry for entry in allowed_entries
            if entry["tool_name"] in allowed_set
        ]

    preferred = [
        entry for entry in allowed_entries
        if entry["tool_name"] in set(capability_request.preferred_tools)
    ]
    candidates = preferred or allowed_entries
    fallback = [
        entry for entry in get_tools_by_names(capability_request.fallback_tools)
        if not allowed_set or entry["tool_name"] in allowed_set
    ]
    if not candidates and fallback:
        candidates = fallback

    resolution_reason = "capability_metadata" if candidates else "no_candidate_match"
    if (
        not connector.is_auto()
        and connector.selected_toolkit
        and not candidates
    ):
        resolution_reason = "connector_constraint_no_match"

    return {
        "capability_request": capability_request.to_dict(),
        "candidate_tools": [entry["tool_name"] for entry in candidates],
        "tool_entries": candidates,
        "preferred_tools": list(capability_request.preferred_tools),
        "fallback_tools": list(capability_request.fallback_tools),
        "resolution_reason": resolution_reason,
        "toolkits": [
            get_toolkit_metadata(entry.get("toolkit", "")).get("label", entry.get("toolkit", ""))
            for entry in candidates
        ],
    }


def summarize_agent_capabilities(agent_key: str, agent_config: dict | None = None) -> dict:
    access = resolve_agent_tool_access(agent_key, agent_config=agent_config)
    tool_entries = get_tools_by_names(access["allowed_tools"])
    toolkits = sorted({get_toolkit_label(tool.get("toolkit", "")) for tool in tool_entries if tool.get("toolkit")})
    return {
        **access,
        "tool_count": len(tool_entries),
        "toolkits": toolkits,
        "capability_requests": [
            build_capability_request(
                agent_key,
                requested_tool_name=tool["tool_name"],
            ).to_dict()
            for tool in tool_entries
        ],
    }


def build_tool_usage_guidance(
    agent_key: str,
    agent_config: dict | None = None,
) -> str:
    """
    Generate prompt-time tool guidance from registry metadata.
    """
    access = resolve_agent_tool_access(agent_key, agent_config=agent_config)
    tool_entries = get_tools_by_names(access["allowed_tools"])
    fallback = access.get("legacy_tool_instructions", "")

    if access.get("tool_mode") != "tool_enabled" or not tool_entries:
        return fallback

    lines: list[str] = []
    tool_policy = access.get("tool_policy", "")
    if tool_policy:
        lines.append(tool_policy)
    lines.append("Use a tool only for verified live data access or a real external action that the user clearly wants.")
    lines.append("Treat capabilities first and tools second: decide the needed read, discovery, draft, or execute capability before choosing a concrete tool.")
    lines.append("Never simulate inbox, email, calendar, Slack, CRM, spreadsheet, GitHub, or social-platform access.")
    lines.append("Never claim a send, post, update, sync, schedule, or creation succeeded unless the verified tool result confirms it.")
    lines.append("If the task is ambiguous, prefer read or discovery before write. Prefer drafts over sends when the user has not clearly asked to execute.")
    lines.append("Available tools:")
    for tool in tool_entries:
        required = ", ".join(tool.get("schema", {}).get("required", [])) or "none"
        note_chunks = [note for note in (*tool.get("usage_notes", ()), *tool.get("safety_notes", ())) if note]
        note_text = f" Notes: {' '.join(note_chunks)}." if note_chunks else ""
        lines.append(
            f"- {tool['tool_name']} ({get_toolkit_label(tool.get('toolkit', ''))}): "
            f"{tool.get('description', tool.get('action', ''))}. "
            f"Mode: {tool.get('execution_mode', 'read')}. Risk: {tool.get('risk_level', 'low')}. "
            f"Required params: {required}.{note_text}"
        )
    if access.get("fallback_used") and access.get("missing_group_tools"):
        lines.append("Some tools were exposed through the broader compatibility fallback because capability metadata was incomplete.")
    lines.append("Never invent tool names. If no listed tool is needed, respond with text.")
    generated = "\n".join(line for line in lines if line).strip()
    return generated or fallback


def prepare_tools_for_prompt(
    agent_key: str,
    available_tools: list[dict],
    user_input: str,
    *,
    route_decision: RouteDecision | dict | None = None,
    capability_request: CapabilityRequest | None = None,
    connector_context: ConnectorContext | dict | None = None,
) -> dict:
    """
    Return a prompt-safe tool list plus lightweight filtering metadata.

    Filtering is intentionally conservative:
      - Never broadens the available tool set.
      - Skips filtering when no clear intent match exists.
      - Falls back to the original tool list when narrowing would remove
        everything or when capability metadata is incomplete.
    """
    prompt_tools = [_clone_tool_for_prompt(tool) for tool in available_tools]
    connector = ConnectorContext.from_value(connector_context)
    if not connector.is_auto() and connector.selected_toolkit:
        normalized_toolkit = normalize_toolkit_key(connector.selected_toolkit) or connector.selected_toolkit
        constrained_tools = [
            tool for tool in prompt_tools
            if str(tool.get("toolkit", "")).upper() == normalized_toolkit
        ]
        if constrained_tools:
            prompt_tools = constrained_tools
    if len(prompt_tools) <= 1:
        return {
            "tools": prompt_tools,
            "filter_applied": False,
            "groups": [],
            "reason": "single_tool",
        }

    if any(not tool.get("capability_groups") for tool in available_tools):
        return {
            "tools": prompt_tools,
            "filter_applied": False,
            "groups": [],
            "reason": "metadata_incomplete",
        }

    access = resolve_agent_tool_access(agent_key)
    allowed_groups = set(access.get("capability_groups", []))
    if not allowed_groups or access.get("fallback_used") and access.get("missing_group_tools"):
        return {
            "tools": prompt_tools,
            "filter_applied": False,
            "groups": [],
            "reason": "ambiguous_capability_mapping",
        }

    route = _coerce_route_decision(route_decision)
    matched_groups: list[str] = []
    if capability_request and capability_request.capability_group:
        matched_groups.append(capability_request.capability_group)
    if route:
        if route.system_family in CAPABILITY_GROUPS:
            matched_groups.append(route.system_family)
        if route.domain in CAPABILITY_GROUPS:
            matched_groups.append(route.domain)
    matched_groups.extend(_infer_relevant_groups(user_input))
    matched_groups = list(dict.fromkeys(group for group in matched_groups if group))
    relevant_groups = [group for group in matched_groups if group in allowed_groups]
    if not relevant_groups:
        return {
            "tools": prompt_tools,
            "filter_applied": False,
            "groups": [],
            "reason": "no_intent_match",
        }

    filtered_tools = [
        tool for tool in prompt_tools
        if set(tool.get("capability_groups", ())) & set(relevant_groups)
    ]
    if not filtered_tools or len(filtered_tools) >= len(prompt_tools):
        return {
            "tools": prompt_tools,
            "filter_applied": False,
            "groups": relevant_groups,
            "reason": "not_narrowed",
        }

    return {
        "tools": filtered_tools,
        "filter_applied": True,
        "groups": relevant_groups,
        "reason": "intent_match",
    }
