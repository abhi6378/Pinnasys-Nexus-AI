"""
tools/tool_registry.py  —  Static registry of tools available to agents.

This is the canonical source of truth for:
  - tool existence and execution metadata
  - human-readable descriptions used in prompts and routing context
  - auth expectations and lightweight schema metadata
  - capability-group tagging used for prompt exposure and policy derivation
  - agent-role availability metadata used by compatibility adapters
"""
from __future__ import annotations

from typing import Optional


TOOLKIT_METADATA: dict[str, dict[str, str]] = {
    "GMAIL": {
        "slug": "gmail",
        "label": "Gmail",
        "app_enum": "GMAIL",
    },
    "GITHUB": {
        "slug": "github",
        "label": "GitHub",
        "app_enum": "GITHUB",
    },
    "SLACK": {
        "slug": "slack",
        "label": "Slack",
        "app_enum": "SLACK",
    },
    "HUBSPOT": {
        "slug": "hubspot",
        "label": "HubSpot",
        "app_enum": "HUBSPOT",
    },
    "GOOGLE_CALENDAR": {
        "slug": "googlecalendar",
        "label": "Google Calendar",
        "app_enum": "GOOGLECALENDAR",
    },
    "GOOGLE_SHEETS": {
        "slug": "googlesheets",
        "label": "Google Sheets",
        "app_enum": "GOOGLESHEETS",
    },
    "TAVILY": {
        "slug": "tavily",
        "label": "Tavily",
        "app_enum": "TAVILY",
    },
    "TWITTER": {
        "slug": "twitter",
        "label": "X / Twitter",
        "app_enum": "TWITTER",
    },
    "LINKEDIN": {
        "slug": "linkedin",
        "label": "LinkedIn",
        "app_enum": "LINKEDIN",
    },
}


def _tool(
    *,
    tool_name: str,
    toolkit: str,
    action: str,
    description: str,
    allowed_agents: list[str],
    requires_auth: bool,
    expected_params: list[str] | None = None,
    param_aliases: dict[str, str] | None = None,
    default_params: dict | None = None,
    tags: tuple[str, ...] = (),
    capability_groups: tuple[str, ...] = (),
    usage_notes: tuple[str, ...] = (),
    safety_notes: tuple[str, ...] = (),
) -> dict:
    expected = list(expected_params or [])
    aliases = dict(param_aliases or {})
    defaults = dict(default_params or {})
    return {
        "tool_name": tool_name,
        "name": tool_name.replace("_", " ").title(),
        "toolkit": toolkit,
        "action": action,
        "description": description,
        "allowed_agents": list(allowed_agents),
        "requires_auth": requires_auth,
        "auth_requirement": "connected_account" if requires_auth else "none",
        "expected_params": expected,
        "param_aliases": aliases,
        "default_params": defaults,
        "tags": tuple(tags),
        "capability_groups": tuple(capability_groups),
        "allowed_capability_groups": tuple(capability_groups),
        "usage_notes": tuple(usage_notes),
        "safety_notes": tuple(safety_notes),
        "schema": {
            "required": expected,
            "aliases": aliases,
            "defaults": defaults,
        },
    }


TOOL_REGISTRY: dict[str, dict] = {
    "GMAIL_SEND_EMAIL": _tool(
        tool_name="GMAIL_SEND_EMAIL",
        toolkit="GMAIL",
        action="Send a Gmail message",
        description="Send a real Gmail email to a recipient once the content is ready.",
        allowed_agents=["assistant", "sales", "email_marketer", "support", "recruiter"],
        requires_auth=True,
        expected_params=["recipient_email", "body"],
        param_aliases={"to": "recipient_email"},
        tags=("email", "send", "gmail", "outreach"),
        capability_groups=("email",),
        safety_notes=("Never claim an email was sent unless this tool ran successfully.",),
    ),
    "GMAIL_CREATE_EMAIL_DRAFT": _tool(
        tool_name="GMAIL_CREATE_EMAIL_DRAFT",
        toolkit="GMAIL",
        action="Create a Gmail draft",
        description="Create a Gmail draft without sending it yet.",
        allowed_agents=["email_marketer", "assistant"],
        requires_auth=True,
        expected_params=["recipient_email", "subject", "body"],
        param_aliases={"to": "recipient_email"},
        tags=("email", "draft", "gmail"),
        capability_groups=("email",),
    ),
    "GMAIL_GET_CONTACTS": _tool(
        tool_name="GMAIL_GET_CONTACTS",
        toolkit="GMAIL",
        action="Read Gmail contacts",
        description="Read saved contacts from the connected Gmail account.",
        allowed_agents=["assistant"],
        requires_auth=True,
        tags=("email", "contacts", "gmail"),
        capability_groups=("email",),
    ),
    "GMAIL_FETCH_EMAILS": _tool(
        tool_name="GMAIL_FETCH_EMAILS",
        toolkit="GMAIL",
        action="Read recent Gmail emails",
        description="Read recent Gmail inbox emails for review, summary, or triage.",
        allowed_agents=["assistant", "sales", "email_marketer", "support"],
        requires_auth=True,
        default_params={"max_results": 10},
        tags=("email", "read", "gmail", "inbox"),
        capability_groups=("email",),
    ),
    "GOOGLECALENDAR_CREATE_EVENT": _tool(
        tool_name="GOOGLECALENDAR_CREATE_EVENT",
        toolkit="GOOGLE_CALENDAR",
        action="Create a Google Calendar event",
        description="Create a real Google Calendar event with the provided time and title.",
        allowed_agents=["assistant", "recruiter"],
        requires_auth=True,
        expected_params=["start_datetime", "summary"],
        param_aliases={
            "start_time": "start_datetime",
            "title": "summary",
            "name": "summary",
        },
        default_params={"calendar_id": "primary", "end_datetime": None},
        tags=("calendar", "schedule", "event", "meeting"),
        capability_groups=("calendar",),
    ),
    "GOOGLECALENDAR_EVENTS_LIST": _tool(
        tool_name="GOOGLECALENDAR_EVENTS_LIST",
        toolkit="GOOGLE_CALENDAR",
        action="List upcoming Google Calendar events",
        description="Read upcoming events from the connected Google Calendar.",
        allowed_agents=["assistant"],
        requires_auth=True,
        param_aliases={"calendar_id": "calendarId"},
        default_params={"calendarId": "primary"},
        tags=("calendar", "read", "schedule", "events"),
        capability_groups=("calendar",),
    ),
    "SLACK_SEND_MESSAGE": _tool(
        tool_name="SLACK_SEND_MESSAGE",
        toolkit="SLACK",
        action="Send a Slack message",
        description="Send a real Slack message to a channel or direct-message target.",
        allowed_agents=["assistant", "support"],
        requires_auth=True,
        expected_params=["channel", "text"],
        tags=("slack", "message", "send", "chat"),
        capability_groups=("slack",),
        safety_notes=("Never imply a Slack message was delivered unless this tool succeeded.",),
    ),
    "SLACK_FETCH_CONVERSATION_HISTORY": _tool(
        tool_name="SLACK_FETCH_CONVERSATION_HISTORY",
        toolkit="SLACK",
        action="Read Slack conversation history",
        description="Read recent Slack conversation history from a channel.",
        allowed_agents=["assistant", "support"],
        requires_auth=True,
        default_params={"limit": 20},
        tags=("slack", "read", "history", "channel"),
        capability_groups=("slack",),
        usage_notes=("If the channel is unclear, resolve it with SLACK_LIST_ALL_CHANNELS first.",),
    ),
    "SLACK_LIST_ALL_CHANNELS": _tool(
        tool_name="SLACK_LIST_ALL_CHANNELS",
        toolkit="SLACK",
        action="List Slack channels",
        description="List available Slack channels before sending or summarizing messages.",
        allowed_agents=["assistant", "support"],
        requires_auth=True,
        tags=("slack", "list", "channels", "discovery"),
        capability_groups=("slack",),
    ),
    "HUBSPOT_CREATE_CONTACT": _tool(
        tool_name="HUBSPOT_CREATE_CONTACT",
        toolkit="HUBSPOT",
        action="Create a HubSpot contact",
        description="Create a real contact record in HubSpot CRM.",
        allowed_agents=["sales"],
        requires_auth=True,
        expected_params=["email"],
        param_aliases={"contact_email": "email"},
        tags=("crm", "hubspot", "contact", "create"),
        capability_groups=("crm",),
    ),
    "HUBSPOT_LIST_CONTACTS": _tool(
        tool_name="HUBSPOT_LIST_CONTACTS",
        toolkit="HUBSPOT",
        action="Read HubSpot contacts",
        description="Read contact records from HubSpot CRM.",
        allowed_agents=["sales", "data_analyst"],
        requires_auth=True,
        tags=("crm", "hubspot", "contacts", "read"),
        capability_groups=("crm",),
    ),
    "HUBSPOT_CREATE_DEAL": _tool(
        tool_name="HUBSPOT_CREATE_DEAL",
        toolkit="HUBSPOT",
        action="Create a HubSpot deal",
        description="Create a real deal record in HubSpot CRM.",
        allowed_agents=["sales"],
        requires_auth=True,
        expected_params=["dealname"],
        tags=("crm", "hubspot", "deal", "create"),
        capability_groups=("crm",),
    ),
    "GOOGLESHEETS_CREATE_SPREADSHEET_ROW": _tool(
        tool_name="GOOGLESHEETS_CREATE_SPREADSHEET_ROW",
        toolkit="GOOGLE_SHEETS",
        action="Append a Google Sheets row",
        description="Append a new row of data to a Google Sheet.",
        allowed_agents=["data_analyst", "assistant"],
        requires_auth=True,
        expected_params=["spreadsheet_id"],
        param_aliases={
            "sheet_id": "spreadsheet_id",
            "spreadsheetId": "spreadsheet_id",
        },
        tags=("sheets", "spreadsheet", "append", "row"),
        capability_groups=("sheets",),
    ),
    "GOOGLESHEETS_BATCH_UPDATE_VALUES": _tool(
        tool_name="GOOGLESHEETS_BATCH_UPDATE_VALUES",
        toolkit="GOOGLE_SHEETS",
        action="Update Google Sheets cells",
        description="Update existing Google Sheet cells with structured data.",
        allowed_agents=["data_analyst", "assistant"],
        requires_auth=True,
        expected_params=["spreadsheet_id", "data"],
        tags=("sheets", "spreadsheet", "update", "cells"),
        capability_groups=("sheets",),
    ),
    "GOOGLESHEETS_GET_SPREADSHEET": _tool(
        tool_name="GOOGLESHEETS_GET_SPREADSHEET",
        toolkit="GOOGLE_SHEETS",
        action="Read Google Spreadsheet details",
        description="Read Google Spreadsheet metadata, including its URL.",
        allowed_agents=["data_analyst", "assistant"],
        requires_auth=True,
        expected_params=["spreadsheet_id"],
        tags=("sheets", "spreadsheet", "read", "metadata"),
        capability_groups=("sheets",),
        usage_notes=("Ask for a spreadsheet_id if the user has not provided one.",),
    ),
    "GITHUB_CREATE_AN_ISSUE": _tool(
        tool_name="GITHUB_CREATE_AN_ISSUE",
        toolkit="GITHUB",
        action="Create a GitHub issue",
        description="Create a real GitHub issue in a repository.",
        allowed_agents=["assistant"],
        requires_auth=True,
        expected_params=["owner", "repo", "title"],
        tags=("github", "issue", "create", "repo"),
        capability_groups=("github",),
    ),
    "GITHUB_LIST_REPOSITORY_ISSUES": _tool(
        tool_name="GITHUB_LIST_REPOSITORY_ISSUES",
        toolkit="GITHUB",
        action="Read GitHub issues",
        description="Read issues from a GitHub repository.",
        allowed_agents=["assistant"],
        requires_auth=True,
        expected_params=["owner", "repo"],
        tags=("github", "issue", "read", "repo"),
        capability_groups=("github",),
    ),
    "TAVILY_SEARCH": _tool(
        tool_name="TAVILY_SEARCH",
        toolkit="TAVILY",
        action="Search the web with Tavily",
        description="Search the web for current information and cited results.",
        allowed_agents=["assistant", "seo", "strategist", "social_media"],
        requires_auth=True,
        expected_params=["query"],
        tags=("research", "web", "search", "live_data"),
        capability_groups=("research",),
    ),
    "TWITTER_CREATION_OF_A_POST": _tool(
        tool_name="TWITTER_CREATION_OF_A_POST",
        toolkit="TWITTER",
        action="Publish an X / Twitter post",
        description="Publish a real post on X / Twitter.",
        allowed_agents=["assistant", "social_media"],
        requires_auth=True,
        expected_params=["text"],
        tags=("social", "twitter", "publish", "post"),
        capability_groups=("social",),
        safety_notes=("Never claim a post was published unless this tool succeeded.",),
    ),
    "TWITTER_RECENT_SEARCH": _tool(
        tool_name="TWITTER_RECENT_SEARCH",
        toolkit="TWITTER",
        action="Search recent X / Twitter posts",
        description="Search recent public posts on X / Twitter.",
        allowed_agents=["assistant", "social_media"],
        requires_auth=True,
        expected_params=["query"],
        default_params={"max_results": 10},
        tags=("social", "twitter", "search", "read"),
        capability_groups=("social",),
    ),
    "LINKEDIN_GET_MY_INFO": _tool(
        tool_name="LINKEDIN_GET_MY_INFO",
        toolkit="LINKEDIN",
        action="Read LinkedIn profile details",
        description="Read the authenticated LinkedIn profile details.",
        allowed_agents=["assistant", "social_media"],
        requires_auth=True,
        tags=("social", "linkedin", "profile", "read"),
        capability_groups=("social",),
    ),
    "LINKEDIN_CREATE_LINKED_IN_POST": _tool(
        tool_name="LINKEDIN_CREATE_LINKED_IN_POST",
        toolkit="LINKEDIN",
        action="Publish a LinkedIn post",
        description="Publish a real LinkedIn post from the connected account.",
        allowed_agents=["assistant", "social_media"],
        requires_auth=True,
        expected_params=["author", "commentary"],
        param_aliases={"text": "commentary", "body": "commentary"},
        default_params={"visibility": "PUBLIC", "lifecycleState": "PUBLISHED"},
        tags=("social", "linkedin", "publish", "post"),
        capability_groups=("social",),
        usage_notes=("Use LINKEDIN_GET_MY_INFO first when you need the author identifier.",),
        safety_notes=("Never claim a post was published unless this tool succeeded.",),
    ),
}


VALID_TOOL_STATUSES = {
    "success",
    "connect_required",
    "auth_unavailable",
    "invalid_tool",
    "validation_error",
    "failure",
    "timeout",
}


def get_tool(tool_name: str) -> Optional[dict]:
    return TOOL_REGISTRY.get(tool_name)


def get_tools_for_agent(agent_key: str) -> list[dict]:
    result = []
    for entry in TOOL_REGISTRY.values():
        allowed = entry.get("allowed_agents", [])
        if not allowed or agent_key in allowed:
            result.append(entry)
    return result


def get_capability_groups_for_agent(agent_key: str) -> list[str]:
    return sorted({
        group
        for entry in get_tools_for_agent(agent_key)
        for group in entry.get("capability_groups", ())
        if group
    })


def get_tools_for_capability_groups(
    agent_key: str,
    capability_groups: list[str] | tuple[str, ...],
) -> list[dict]:
    requested_groups = set(capability_groups or [])
    if not requested_groups:
        return []
    result = []
    for entry in get_tools_for_agent(agent_key):
        entry_groups = set(entry.get("capability_groups", ()))
        if entry_groups & requested_groups:
            result.append(entry)
    return result


def get_tool_names_for_capability_groups(
    agent_key: str,
    capability_groups: list[str] | tuple[str, ...],
) -> list[str]:
    return [entry["tool_name"] for entry in get_tools_for_capability_groups(agent_key, capability_groups)]


def get_tools_by_names(tool_names: list[str]) -> list[dict]:
    result = []
    for name in tool_names:
        entry = TOOL_REGISTRY.get(name)
        if entry is not None:
            result.append(entry)
    return result


def split_valid_tool_names(tool_names: list[str]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for name in tool_names:
        if name in TOOL_REGISTRY:
            valid.append(name)
        else:
            invalid.append(name)
    return valid, invalid


def get_tools_for_toolkit(toolkit: str) -> list[dict]:
    return [
        entry for entry in TOOL_REGISTRY.values()
        if entry.get("toolkit") == toolkit
    ]


def list_all_tools() -> list[dict]:
    return list(TOOL_REGISTRY.values())


def list_toolkits() -> list[str]:
    return sorted({entry["toolkit"] for entry in TOOL_REGISTRY.values()})


def get_toolkit_slug(toolkit: str) -> Optional[str]:
    if not toolkit:
        return None
    meta = TOOLKIT_METADATA.get(toolkit.upper())
    if meta is None:
        return None
    return meta["slug"]


def get_toolkit_label(toolkit: str) -> str:
    if not toolkit:
        return ""
    meta = TOOLKIT_METADATA.get(toolkit.upper())
    if meta is None:
        return toolkit.replace("_", " ").title()
    return meta["label"]


def get_toolkit_app_enum(toolkit: str) -> Optional[str]:
    if not toolkit:
        return None
    meta = TOOLKIT_METADATA.get(toolkit.upper())
    if meta is None:
        return None
    return meta.get("app_enum")


def list_toolkit_slugs() -> list[str]:
    slugs = set()
    for toolkit in list_toolkits():
        slug = get_toolkit_slug(toolkit)
        if slug:
            slugs.add(slug)
    return sorted(slugs)


def get_enabled_tool_map() -> dict[str, dict[str, list[str]]]:
    tool_map: dict[str, dict[str, list[str]]] = {}
    for entry in TOOL_REGISTRY.values():
        toolkit_slug = get_toolkit_slug(entry["toolkit"])
        if not toolkit_slug:
            continue
        tool_map.setdefault(toolkit_slug, {"enable": []})
        tool_map[toolkit_slug]["enable"].append(entry["tool_name"])
    return tool_map


def get_tool_metadata_gaps(tool_names: list[str] | None = None) -> dict[str, list[str]]:
    gaps: dict[str, list[str]] = {}
    names = tool_names or list(TOOL_REGISTRY.keys())
    required_fields = ("tool_name", "description", "auth_requirement", "schema")
    for name in names:
        entry = TOOL_REGISTRY.get(name)
        if entry is None:
            gaps[name] = ["missing_registry_entry"]
            continue
        entry_gaps: list[str] = []
        for field in required_fields:
            if not entry.get(field):
                entry_gaps.append(f"missing_{field}")
        schema = entry.get("schema", {})
        if not isinstance(schema, dict):
            entry_gaps.append("invalid_schema")
        if not entry.get("capability_groups"):
            entry_gaps.append("missing_capability_groups")
        if entry_gaps:
            gaps[name] = entry_gaps
    return gaps


def get_tools_missing_capability_groups(agent_key: str) -> list[str]:
    return [
        entry["tool_name"]
        for entry in get_tools_for_agent(agent_key)
        if not entry.get("capability_groups")
    ]


def format_tool_for_prompt(tool: dict) -> str:
    toolkit_label = get_toolkit_label(tool.get("toolkit", "")) or tool.get("toolkit", "")
    required_params = tool.get("schema", {}).get("required") or tool.get("expected_params", [])
    required = ", ".join(required_params) if required_params else "none"
    details: list[str] = [f"toolkit: {toolkit_label}", f"required_params: {required}"]
    usage_notes = [note for note in tool.get("usage_notes", ()) if note]
    if usage_notes:
        details.append(f"notes: {' '.join(usage_notes)}")
    return (
        f"  - {tool['tool_name']}: "
        f"{tool.get('description') or tool.get('action') or tool['tool_name']} "
        f"({'; '.join(details)})"
    )


def build_prompt_tool_catalog(tool_entries: list[dict]) -> str:
    return "\n".join(format_tool_for_prompt(tool) for tool in tool_entries)


def list_toolkit_labels_for_tools(tool_entries: list[dict]) -> list[str]:
    return sorted({
        get_toolkit_label(tool.get("toolkit", ""))
        for tool in tool_entries
        if tool.get("toolkit")
    })


def is_agent_allowed(tool_name: str, agent_key: str) -> bool:
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return False
    allowed = entry.get("allowed_agents", [])
    return not allowed or agent_key in allowed
