"""
tools/tool_registry.py  —  Static registry of Composio tools available to agents.

This file is the SINGLE SOURCE OF TRUTH for which tools exist, which toolkit
they belong to, which agents may use them, and whether they require auth.

The registry is intentionally decoupled from the agent configs in
helpers/configs.py so that:
  - Adding a new tool never touches agent definitions.
  - Removing a tool mapping is a one-line change here, not scattered
    across helpers/configs.py + orchestrator + UI.
  - The tool_executor can validate "is agent X allowed to call tool Y?"
    by looking here alone.

The registry does NOT know anything about Composio sessions, auth status,
or execution — those responsibilities live in composio_client.py and
tool_executor.py respectively.
"""
from __future__ import annotations
from typing import Optional


# ── Registry ──────────────────────────────────────────────────────────────────
#
# Each entry maps a unique tool_name (= Composio action slug) to its metadata.
#
# Fields:
#   tool_name      — Composio action slug (primary key of this dict)
#   toolkit        — Composio app/toolkit name (used for connection checks)
#   action         — human-readable label shown in logs and UI
#   allowed_agents — list of agent_keys from helpers/configs.py that may use it;
#                    empty list = ALL agents may use it
#   requires_auth  — True when the tool needs a connected account (OAuth/key)

TOOL_REGISTRY: dict[str, dict] = {
    # ── Gmail ─────────────────────────────────────────────────────────────
    "GMAIL_SEND_EMAIL": {
        "tool_name":      "GMAIL_SEND_EMAIL",
        "toolkit":        "GMAIL",
        "action":         "Send an email via Gmail",
        "allowed_agents": ["assistant", "sales", "email_marketer", "support", "recruiter"],
        "requires_auth":  True,
    },
    "GMAIL_GET_PROFILE": {
        "tool_name":      "GMAIL_GET_PROFILE",
        "toolkit":        "GMAIL",
        "action":         "Get Gmail profile info",
        "allowed_agents": ["assistant"],
        "requires_auth":  True,
    },
    "GMAIL_LIST_EMAILS": {
        "tool_name":      "GMAIL_LIST_EMAILS",
        "toolkit":        "GMAIL",
        "action":         "List recent emails",
        "allowed_agents": ["assistant", "sales", "email_marketer", "support"],
        "requires_auth":  True,
    },

    # ── Google Calendar ───────────────────────────────────────────────────
    "GOOGLE_CALENDAR_CREATE_EVENT": {
        "tool_name":      "GOOGLE_CALENDAR_CREATE_EVENT",
        "toolkit":        "GOOGLE_CALENDAR",
        "action":         "Create a calendar event",
        "allowed_agents": ["assistant", "recruiter"],
        "requires_auth":  True,
    },
    "GOOGLE_CALENDAR_LIST_EVENTS": {
        "tool_name":      "GOOGLE_CALENDAR_LIST_EVENTS",
        "toolkit":        "GOOGLE_CALENDAR",
        "action":         "List upcoming calendar events",
        "allowed_agents": ["assistant"],
        "requires_auth":  True,
    },

    # ── Slack ─────────────────────────────────────────────────────────────
    "SLACK_SEND_MESSAGE": {
        "tool_name":      "SLACK_SEND_MESSAGE",
        "toolkit":        "SLACK",
        "action":         "Send a Slack message",
        "allowed_agents": ["assistant", "support"],
        "requires_auth":  True,
    },

    # ── HubSpot ───────────────────────────────────────────────────────────
    "HUBSPOT_CREATE_CONTACT": {
        "tool_name":      "HUBSPOT_CREATE_CONTACT",
        "toolkit":        "HUBSPOT",
        "action":         "Create a HubSpot contact",
        "allowed_agents": ["sales"],
        "requires_auth":  True,
    },
    "HUBSPOT_GET_CONTACTS": {
        "tool_name":      "HUBSPOT_GET_CONTACTS",
        "toolkit":        "HUBSPOT",
        "action":         "List HubSpot contacts",
        "allowed_agents": ["sales", "data_analyst"],
        "requires_auth":  True,
    },
}


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_tool(tool_name: str) -> Optional[dict]:
    """Return the registry entry for a tool, or None if not found."""
    return TOOL_REGISTRY.get(tool_name)


def get_tools_for_agent(agent_key: str) -> list[dict]:
    """
    Return all registry entries that a given agent is allowed to use.

    An entry with an empty allowed_agents list means "any agent".
    """
    result = []
    for entry in TOOL_REGISTRY.values():
        allowed = entry.get("allowed_agents", [])
        if not allowed or agent_key in allowed:
            result.append(entry)
    return result


def get_tools_by_names(tool_names: list[str]) -> list[dict]:
    """
    Resolve a list of tool names (from agent config's ``allowed_tools``)
    into their full registry entries.

    Only returns tools that actually exist in the registry — silently
    skips invalid or unknown names so callers never crash on stale config.

    This is the primary bridge between the config-driven ``allowed_tools``
    list in helpers/configs.py and the registry's full metadata.
    """
    result = []
    for name in tool_names:
        entry = TOOL_REGISTRY.get(name)
        if entry is not None:
            result.append(entry)
    return result


def get_tools_for_toolkit(toolkit: str) -> list[dict]:
    """Return all registry entries belonging to a specific toolkit."""
    return [
        entry for entry in TOOL_REGISTRY.values()
        if entry.get("toolkit") == toolkit
    ]


def list_all_tools() -> list[dict]:
    """Return the full registry as a list (for API / UI consumption)."""
    return list(TOOL_REGISTRY.values())


def list_toolkits() -> list[str]:
    """Return a deduplicated list of all toolkit names."""
    return sorted({entry["toolkit"] for entry in TOOL_REGISTRY.values()})


def is_agent_allowed(tool_name: str, agent_key: str) -> bool:
    """
    Check whether an agent is permitted to use a specific tool.

    Returns False if the tool doesn't exist in the registry.
    Returns True if allowed_agents is empty (= all agents allowed).
    """
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return False
    allowed = entry.get("allowed_agents", [])
    return not allowed or agent_key in allowed
