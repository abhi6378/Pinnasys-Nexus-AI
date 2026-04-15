"""
tools/capability_layer.py  —  Backward-compatible capability derivation helpers.

This module keeps tool execution conservative:
  - Agent tool access is derived from the canonical tool registry wherever possible.
  - Explicit legacy allowlists passed by older callers still work.
  - If capability-group metadata is incomplete or ambiguous, callers fall back
    to the broader registry-derived per-agent tool set.
"""
from __future__ import annotations

from helpers.agent_capabilities import get_capability_policy
from tools.tool_registry import (
    get_capability_groups_for_agent,
    get_tool_metadata_gaps,
    get_tool_names_for_capability_groups,
    get_toolkit_label,
    get_tools_by_names,
    get_tools_for_agent,
    get_tools_missing_capability_groups,
    split_valid_tool_names,
)


CAPABILITY_GROUPS: dict[str, dict[str, object]] = {
    "email": {
        "label": "email operations",
        "keywords": ("email", "mail", "gmail", "inbox", "reply", "draft"),
    },
    "calendar": {
        "label": "calendar scheduling",
        "keywords": ("calendar", "meeting", "schedule", "availability", "invite"),
    },
    "slack": {
        "label": "Slack messaging",
        "keywords": ("slack", "channel", "dm", "workspace message"),
    },
    "crm": {
        "label": "CRM updates",
        "keywords": ("hubspot", "crm", "contact", "deal", "lead"),
    },
    "sheets": {
        "label": "spreadsheet updates",
        "keywords": ("sheet", "sheets", "spreadsheet", "row", "cells"),
    },
    "github": {
        "label": "GitHub issue work",
        "keywords": ("github", "repo", "repository", "issue", "bug"),
    },
    "research": {
        "label": "live web research",
        "keywords": ("research", "search", "lookup", "find information", "web"),
    },
    "social": {
        "label": "social publishing and discovery",
        "keywords": ("twitter", "tweet", "linkedin", "social post", "post on x", "post on linkedin"),
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


def summarize_agent_capabilities(agent_key: str, agent_config: dict | None = None) -> dict:
    access = resolve_agent_tool_access(agent_key, agent_config=agent_config)
    tool_entries = get_tools_by_names(access["allowed_tools"])
    toolkits = sorted({get_toolkit_label(tool.get("toolkit", "")) for tool in tool_entries if tool.get("toolkit")})
    return {
        **access,
        "tool_count": len(tool_entries),
        "toolkits": toolkits,
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
    lines.append("Use a tool only for real external actions or live data that the user clearly wants.")
    lines.append("Available tools:")
    for tool in tool_entries:
        required = ", ".join(tool.get("schema", {}).get("required", [])) or "none"
        note_chunks = [note for note in (*tool.get("usage_notes", ()), *tool.get("safety_notes", ())) if note]
        note_text = f" Notes: {' '.join(note_chunks)}." if note_chunks else ""
        lines.append(
            f"- {tool['tool_name']} ({get_toolkit_label(tool.get('toolkit', ''))}): "
            f"{tool.get('description', tool.get('action', ''))}. Required params: {required}.{note_text}"
        )
    if access.get("fallback_used") and access.get("missing_group_tools"):
        lines.append("Some tools were exposed through the broader compatibility fallback because capability metadata was incomplete.")
    lines.append("Never invent tool names. If no listed tool is needed, respond with text.")
    generated = "\n".join(line for line in lines if line).strip()
    return generated or fallback


def prepare_tools_for_prompt(agent_key: str, available_tools: list[dict], user_input: str) -> dict:
    """
    Return a prompt-safe tool list plus lightweight filtering metadata.

    Filtering is intentionally conservative:
      - Never broadens the available tool set.
      - Skips filtering when no clear intent match exists.
      - Falls back to the original tool list when narrowing would remove
        everything or when capability metadata is incomplete.
    """
    prompt_tools = [_clone_tool_for_prompt(tool) for tool in available_tools]
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

    matched_groups = _infer_relevant_groups(user_input)
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
