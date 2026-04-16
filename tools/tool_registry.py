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

from models.contracts import ApprovalRequirement


TOOLKIT_METADATA: dict[str, dict[str, str]] = {
    "GMAIL": {
        "slug": "gmail",
        "label": "Gmail",
        "app_enum": "GMAIL",
        "aliases": ("gmail", "google mail"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "GITHUB": {
        "slug": "github",
        "label": "GitHub",
        "app_enum": "GITHUB",
        "aliases": ("github", "git hub"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "SLACK": {
        "slug": "slack",
        "label": "Slack",
        "app_enum": "SLACK",
        "aliases": ("slack",),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "HUBSPOT": {
        "slug": "hubspot",
        "label": "HubSpot",
        "app_enum": "HUBSPOT",
        "aliases": ("hubspot", "hub spot"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "GOOGLE_CALENDAR": {
        "slug": "googlecalendar",
        "label": "Google Calendar",
        "app_enum": "GOOGLECALENDAR",
        "aliases": ("google calendar", "calendar"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "GOOGLE_SHEETS": {
        "slug": "googlesheets",
        "label": "Google Sheets",
        "app_enum": "GOOGLESHEETS",
        "aliases": ("google sheets", "sheets", "spreadsheet"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
    "TAVILY": {
        "slug": "tavily",
        "label": "Tavily",
        "app_enum": "TAVILY",
        "aliases": ("tavily", "web search"),
        "auth_mode": "api_key",
        "schema_source": "composio_live",
        "connection_mode": "custom_key",
        "setup_message": "Add the Tavily API key in Composio before connecting.",
    },
    "TWITTER": {
        "slug": "twitter",
        "label": "X / Twitter",
        "app_enum": "TWITTER",
        "aliases": ("twitter", "x", "x twitter"),
        "auth_mode": "api_key",
        "schema_source": "composio_live",
        "connection_mode": "custom_key",
        "setup_message": "Configure Twitter developer credentials in Composio before connecting.",
    },
    "LINKEDIN": {
        "slug": "linkedin",
        "label": "LinkedIn",
        "app_enum": "LINKEDIN",
        "aliases": ("linkedin", "linked in"),
        "auth_mode": "oauth2",
        "schema_source": "composio_live",
        "connection_mode": "managed_account",
    },
}


CAPABILITY_GROUP_METADATA: dict[str, dict[str, object]] = {
    "email": {
        "label": "email operations",
        "toolkit_families": ("GMAIL",),
        "action_classes": ("read", "draft", "send"),
        "default_risk_level": "medium",
    },
    "calendar": {
        "label": "calendar scheduling",
        "toolkit_families": ("GOOGLE_CALENDAR",),
        "action_classes": ("read", "create", "schedule"),
        "default_risk_level": "medium",
    },
    "slack": {
        "label": "Slack messaging",
        "toolkit_families": ("SLACK",),
        "action_classes": ("read", "send", "discover"),
        "default_risk_level": "medium",
    },
    "crm": {
        "label": "CRM operations",
        "toolkit_families": ("HUBSPOT",),
        "action_classes": ("read", "create", "update"),
        "default_risk_level": "high",
    },
    "sheets": {
        "label": "spreadsheet operations",
        "toolkit_families": ("GOOGLE_SHEETS",),
        "action_classes": ("read", "append", "update"),
        "default_risk_level": "medium",
    },
    "github": {
        "label": "GitHub repository operations",
        "toolkit_families": ("GITHUB",),
        "action_classes": ("read", "create"),
        "default_risk_level": "medium",
    },
    "research": {
        "label": "live research",
        "toolkit_families": ("TAVILY",),
        "action_classes": ("search", "analyze"),
        "default_risk_level": "low",
    },
    "social": {
        "label": "social publishing",
        "toolkit_families": ("TWITTER", "LINKEDIN"),
        "action_classes": ("read", "publish"),
        "default_risk_level": "high",
    },
}


TOOL_POLICY_OVERLAY: dict[str, dict[str, object]] = {
    "GMAIL_SEND_EMAIL": {
        "action_classes": ("send", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("recipient_email", "subject", "body"),
        "tool_aliases": ("gmail_send", "send_email"),
    },
    "GMAIL_CREATE_EMAIL_DRAFT": {
        "action_classes": ("draft", "write"),
        "operation_types": ("draft", "write"),
        "risk_level": "medium",
        "approval_mode": "draft_safe",
        "execution_mode": "draft",
        "idempotency_fields": ("recipient_email", "subject", "body"),
        "tool_aliases": ("gmail_draft", "create_email_draft"),
    },
    "GMAIL_GET_CONTACTS": {
        "action_classes": ("read", "discover"),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "GMAIL_FETCH_EMAILS": {
        "action_classes": ("read", "triage"),
        "operation_types": ("read",),
        "risk_level": "low",
        "tool_aliases": ("gmail_fetch", "fetch_emails"),
    },
    "GOOGLECALENDAR_CREATE_EVENT": {
        "action_classes": ("create", "schedule"),
        "operation_types": ("write", "schedule"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("calendar_id", "summary", "start_datetime", "end_datetime"),
    },
    "GOOGLECALENDAR_EVENTS_LIST": {
        "action_classes": ("read", "schedule"),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "SLACK_SEND_MESSAGE": {
        "action_classes": ("send", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("channel", "text"),
    },
    "SLACK_FETCH_CONVERSATION_HISTORY": {
        "action_classes": ("read",),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "SLACK_LIST_ALL_CHANNELS": {
        "action_classes": ("discover", "read"),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "HUBSPOT_CREATE_CONTACT": {
        "action_classes": ("create", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("email",),
    },
    "HUBSPOT_LIST_CONTACTS": {
        "action_classes": ("read",),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "HUBSPOT_LIST_DEALS": {
        "action_classes": ("read", "search"),
        "operation_types": ("read",),
        "risk_level": "low",
        "tool_aliases": ("hubspot_deals", "list_deals", "search_deals"),
    },
    "HUBSPOT_CREATE_DEAL": {
        "action_classes": ("create", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("dealname",),
    },
    "GOOGLESHEETS_CREATE_SPREADSHEET_ROW": {
        "action_classes": ("append", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "medium",
        "approval_mode": "execute",
        "execution_mode": "execute",
        "idempotency_fields": ("spreadsheet_id", "sheet_name", "row_values", "values"),
    },
    "GOOGLESHEETS_BATCH_UPDATE_VALUES": {
        "action_classes": ("update", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "medium",
        "approval_mode": "execute",
        "execution_mode": "execute",
        "idempotency_fields": ("spreadsheet_id", "data"),
    },
    "GOOGLESHEETS_GET_SPREADSHEET": {
        "action_classes": ("read",),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "GITHUB_CREATE_AN_ISSUE": {
        "action_classes": ("create", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "medium",
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("owner", "repo", "title", "body"),
    },
    "GITHUB_LIST_REPOSITORY_ISSUES": {
        "action_classes": ("read",),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "TAVILY_SEARCH": {
        "action_classes": ("search", "research"),
        "operation_types": ("research", "read"),
        "risk_level": "low",
        "tool_aliases": ("web_search", "live_search"),
    },
    "TWITTER_CREATION_OF_A_POST": {
        "action_classes": ("publish", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("text",),
    },
    "TWITTER_RECENT_SEARCH": {
        "action_classes": ("search", "read"),
        "operation_types": ("research", "read"),
        "risk_level": "low",
    },
    "LINKEDIN_GET_MY_INFO": {
        "action_classes": ("read", "discover"),
        "operation_types": ("read",),
        "risk_level": "low",
    },
    "LINKEDIN_CREATE_LINKED_IN_POST": {
        "action_classes": ("publish", "write"),
        "operation_types": ("write", "execute"),
        "risk_level": "high",
        "approval_required": True,
        "approval_mode": "confirm_or_explicit_execute",
        "execution_mode": "execute",
        "idempotency_fields": ("author", "commentary"),
    },
}


TOOL_INPUT_SCHEMAS: dict[str, dict[str, object]] = {
    "GMAIL_SEND_EMAIL": {
        "properties": {
            "recipient_email": {"type": "string", "format": "email"},
            "subject": {"type": "string"},
            "body": {"type": "string", "min_length": 1},
        }
    },
    "GMAIL_CREATE_EMAIL_DRAFT": {
        "properties": {
            "recipient_email": {"type": "string", "format": "email"},
            "subject": {"type": "string", "min_length": 1},
            "body": {"type": "string", "min_length": 1},
        }
    },
    "GMAIL_FETCH_EMAILS": {
        "properties": {
            "max_results": {"type": "integer", "minimum": 1},
            "query": {"type": "string"},
        }
    },
    "GOOGLECALENDAR_CREATE_EVENT": {
        "properties": {
            "start_datetime": {"type": "string", "min_length": 3},
            "end_datetime": {"type": "string", "nullable": True},
            "summary": {"type": "string", "min_length": 1},
            "calendar_id": {"type": "string"},
        }
    },
    "GOOGLECALENDAR_EVENTS_LIST": {
        "properties": {
            "calendarId": {"type": "string"},
            "calendar_id": {"type": "string"},
            "timeMin": {"type": "string"},
            "timeMax": {"type": "string"},
        }
    },
    "SLACK_SEND_MESSAGE": {
        "properties": {
            "channel": {"type": "string", "min_length": 1},
            "text": {"type": "string", "min_length": 1},
        }
    },
    "SLACK_FETCH_CONVERSATION_HISTORY": {
        "properties": {
            "channel": {"type": "string", "min_length": 1},
            "limit": {"type": "integer", "minimum": 1},
        }
    },
    "HUBSPOT_CREATE_CONTACT": {
        "properties": {
            "email": {"type": "string", "format": "email"},
            "firstname": {"type": "string"},
            "lastname": {"type": "string"},
        }
    },
    "HUBSPOT_CREATE_DEAL": {
        "properties": {
            "dealname": {"type": "string", "min_length": 1},
        }
    },
    "GOOGLESHEETS_CREATE_SPREADSHEET_ROW": {
        "properties": {
            "spreadsheet_id": {"type": "string", "min_length": 1},
            "sheet_name": {"type": "string"},
            "row_values": {"type": "array"},
            "values": {"type": "array"},
        }
    },
    "GOOGLESHEETS_BATCH_UPDATE_VALUES": {
        "properties": {
            "spreadsheet_id": {"type": "string", "min_length": 1},
            "data": {"type": "array"},
        }
    },
    "GOOGLESHEETS_GET_SPREADSHEET": {
        "properties": {
            "spreadsheet_id": {"type": "string", "min_length": 1},
        }
    },
    "GITHUB_CREATE_AN_ISSUE": {
        "properties": {
            "owner": {"type": "string", "min_length": 1},
            "repo": {"type": "string", "min_length": 1},
            "title": {"type": "string", "min_length": 1},
            "body": {"type": "string"},
        }
    },
    "GITHUB_LIST_REPOSITORY_ISSUES": {
        "properties": {
            "owner": {"type": "string", "min_length": 1},
            "repo": {"type": "string", "min_length": 1},
        }
    },
    "TAVILY_SEARCH": {
        "properties": {
            "query": {"type": "string", "min_length": 1},
            "max_results": {"type": "integer", "minimum": 1},
        }
    },
    "TWITTER_CREATION_OF_A_POST": {
        "properties": {
            "text": {"type": "string", "min_length": 1},
        }
    },
    "TWITTER_RECENT_SEARCH": {
        "properties": {
            "query": {"type": "string", "min_length": 1},
            "max_results": {"type": "integer", "minimum": 1},
        }
    },
    "LINKEDIN_GET_MY_INFO": {
        "properties": {}
    },
    "LINKEDIN_CREATE_LINKED_IN_POST": {
        "properties": {
            "author": {"type": "string", "min_length": 1},
            "commentary": {"type": "string", "min_length": 1},
            "visibility": {"type": "string"},
            "lifecycleState": {"type": "string"},
        }
    },
}


def _infer_action_classes(tool_name: str, entry: dict) -> tuple[str, ...]:
    lowered = f"{tool_name} {entry.get('action', '')} {' '.join(entry.get('tags', ()))}`".replace("`", "").lower()
    if any(token in lowered for token in ("send", "publish")):
        return ("send", "write")
    if "draft" in lowered:
        return ("draft", "write")
    if any(token in lowered for token in ("create", "append", "update")):
        return ("create", "write")
    if any(token in lowered for token in ("search", "research")):
        return ("search", "research")
    if any(token in lowered for token in ("list", "get", "read", "fetch")):
        return ("read",)
    return ("analyze",)


def _infer_operation_types(action_classes: tuple[str, ...]) -> tuple[str, ...]:
    if any(action in action_classes for action in ("send", "publish", "create", "append", "update")):
        return ("write", "execute")
    if "draft" in action_classes:
        return ("draft", "write")
    if any(action in action_classes for action in ("search", "research")):
        return ("research", "read")
    return ("read",)


def _infer_risk_level(action_classes: tuple[str, ...]) -> str:
    if any(action in action_classes for action in ("send", "publish")):
        return "high"
    if any(action in action_classes for action in ("create", "append", "update", "draft")):
        return "medium"
    return "low"


def _merge_schema(base_schema: dict, local_schema: dict | None) -> dict:
    merged = dict(base_schema or {})
    if not local_schema:
        return merged
    merged.setdefault("properties", {})
    merged["properties"] = {
        **dict(merged.get("properties") or {}),
        **dict(local_schema.get("properties") or {}),
    }
    if local_schema.get("required"):
        merged["required"] = list(dict.fromkeys([*(merged.get("required") or []), *list(local_schema.get("required") or [])]))
    for key, value in local_schema.items():
        if key not in {"properties", "required"}:
            merged[key] = value
    return merged


def _apply_tool_overlay(tool_name: str, entry: dict) -> dict:
    overlay = dict(TOOL_POLICY_OVERLAY.get(tool_name, {}))
    merged = dict(entry)
    action_classes = tuple(overlay.get("action_classes") or _infer_action_classes(tool_name, entry))
    operation_types = tuple(overlay.get("operation_types") or _infer_operation_types(action_classes))
    risk_level = str(overlay.get("risk_level") or _infer_risk_level(action_classes))
    approval_required = bool(overlay.get("approval_required", risk_level == "high"))
    tool_aliases = tuple(overlay.get("tool_aliases") or ())
    local_schema = TOOL_INPUT_SCHEMAS.get(tool_name, {})
    approval_mode = str(overlay.get("approval_mode") or ("confirm_or_explicit_execute" if approval_required else "auto"))
    execution_mode = str(overlay.get("execution_mode") or ("draft" if "draft" in action_classes else "execute" if approval_required else "read"))
    idempotency_fields = tuple(overlay.get("idempotency_fields") or ())

    merged.update(
        {
            **overlay,
            "action_classes": action_classes,
            "operation_types": operation_types,
            "risk_level": risk_level,
            "tool_aliases": tool_aliases,
            "approval_required": approval_required,
            "approval_mode": approval_mode,
            "execution_mode": execution_mode,
            "idempotency_fields": idempotency_fields,
            "write_action": any(action in action_classes for action in ("send", "publish", "create", "append", "update")),
            "capability_profile": {
                "capability_group": next(iter(entry.get("capability_groups", ())), ""),
                "toolkit_family": entry.get("toolkit", ""),
                "action_class": action_classes[0] if action_classes else "",
                "risk_level": risk_level,
                "preferred_tools": [tool_name],
                "fallback_tools": [],
            },
            "schema": _merge_schema(entry.get("schema", {}), local_schema),
        }
    )
    return merged


def _build_tool_alias_map(entries: dict[str, dict]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for tool_name, entry in entries.items():
        alias_map[tool_name] = tool_name
        alias_map[tool_name.lower()] = tool_name
        for alias in entry.get("tool_aliases", ()):
            alias_map[str(alias)] = tool_name
            alias_map[str(alias).lower()] = tool_name
    return alias_map


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


_RAW_TOOL_REGISTRY: dict[str, dict] = {
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
        allowed_agents=["sales", "data_analyst", "assistant"],
        requires_auth=True,
        tags=("crm", "hubspot", "contacts", "read"),
        capability_groups=("crm",),
    ),
    "HUBSPOT_LIST_DEALS": _tool(
        tool_name="HUBSPOT_LIST_DEALS",
        toolkit="HUBSPOT",
        action="Read HubSpot deals",
        description="Read deal records from HubSpot CRM, including ongoing pipeline deals.",
        allowed_agents=["sales", "data_analyst", "assistant"],
        requires_auth=True,
        tags=("crm", "hubspot", "deals", "pipeline", "read"),
        capability_groups=("crm",),
        usage_notes=("Use this for ongoing deals, active pipeline review, and deal summaries.",),
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


TOOL_REGISTRY: dict[str, dict] = {
    tool_name: _apply_tool_overlay(tool_name, entry)
    for tool_name, entry in _RAW_TOOL_REGISTRY.items()
}

TOOL_ALIASES = _build_tool_alias_map(TOOL_REGISTRY)


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
    canonical = resolve_tool_name(tool_name)
    if not canonical:
        return None
    return TOOL_REGISTRY.get(canonical)


def resolve_tool_name(tool_name: str) -> Optional[str]:
    if not tool_name:
        return None
    return TOOL_ALIASES.get(tool_name) or TOOL_ALIASES.get(str(tool_name).upper()) or TOOL_ALIASES.get(str(tool_name).lower())


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
        entry = get_tool(name)
        if entry is not None:
            result.append(entry)
    return result


def split_valid_tool_names(tool_names: list[str]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    invalid: list[str] = []
    for name in tool_names:
        canonical = resolve_tool_name(name)
        if canonical in TOOL_REGISTRY:
            valid.append(canonical)
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


def _build_toolkit_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for toolkit, meta in TOOLKIT_METADATA.items():
        values = {
            toolkit,
            toolkit.lower(),
            str(meta.get("label", "") or ""),
            str(meta.get("slug", "") or ""),
            str(meta.get("app_enum", "") or ""),
            *(str(alias or "") for alias in tuple(meta.get("aliases", ()) or ())),
        }
        for value in values:
            cleaned = str(value or "").strip()
            if not cleaned:
                continue
            alias_map[cleaned] = toolkit
            alias_map[cleaned.upper()] = toolkit
            alias_map[cleaned.lower()] = toolkit
            alias_map[cleaned.replace(" ", "").lower()] = toolkit
    return alias_map


TOOLKIT_ALIASES = _build_toolkit_alias_map()


def normalize_toolkit_key(toolkit: str) -> str:
    if not toolkit:
        return ""
    cleaned = str(toolkit or "").strip()
    return (
        TOOLKIT_ALIASES.get(cleaned)
        or TOOLKIT_ALIASES.get(cleaned.upper())
        or TOOLKIT_ALIASES.get(cleaned.lower())
        or TOOLKIT_ALIASES.get(cleaned.replace(" ", "").lower())
        or ""
    )


def get_toolkit_slug(toolkit: str) -> Optional[str]:
    toolkit_key = normalize_toolkit_key(toolkit)
    if not toolkit_key:
        return None
    meta = TOOLKIT_METADATA.get(toolkit_key)
    if meta is None:
        return None
    return meta["slug"]


def get_toolkit_label(toolkit: str) -> str:
    toolkit_key = normalize_toolkit_key(toolkit)
    if not toolkit_key:
        return ""
    meta = TOOLKIT_METADATA.get(toolkit_key)
    if meta is None:
        return toolkit.replace("_", " ").title()
    return meta["label"]


def get_toolkit_app_enum(toolkit: str) -> Optional[str]:
    toolkit_key = normalize_toolkit_key(toolkit)
    if not toolkit_key:
        return None
    meta = TOOLKIT_METADATA.get(toolkit_key)
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
    entry = get_tool(tool_name)
    if not entry:
        return False
    allowed = entry.get("allowed_agents", [])
    return not allowed or agent_key in allowed


def get_toolkit_metadata(toolkit: str) -> dict[str, object]:
    toolkit_key = normalize_toolkit_key(toolkit)
    if not toolkit_key:
        return {}
    return dict(TOOLKIT_METADATA.get(toolkit_key, {}))


def get_toolkit_runtime_config(toolkit: str) -> dict[str, object]:
    return get_toolkit_metadata(toolkit)


def get_capability_group_metadata(group_name: str) -> dict[str, object]:
    if not group_name:
        return {}
    return dict(CAPABILITY_GROUP_METADATA.get(group_name, {}))


def get_tool_policy_overlay(tool_name: str) -> dict[str, object]:
    canonical = resolve_tool_name(tool_name)
    if not canonical:
        return {}
    return dict(TOOL_POLICY_OVERLAY.get(canonical, {}))


def normalize_tool_input(tool_name: str, input_args: dict | None) -> dict:
    entry = get_tool(tool_name)
    normalized = dict(input_args or {})
    if not entry:
        return normalized

    for source_key, target_key in entry.get("param_aliases", {}).items():
        if source_key in normalized and target_key not in normalized:
            normalized[target_key] = normalized.pop(source_key)

    for key, value in entry.get("default_params", {}).items():
        normalized.setdefault(key, value)

    return normalized


def get_tool_schema(tool_name: str) -> dict:
    entry = get_tool(tool_name) or {}
    return dict(entry.get("schema", {}))


def get_tool_execution_mode(tool_name: str) -> str:
    entry = get_tool(tool_name) or {}
    return str(entry.get("execution_mode", "") or "")


def get_tool_idempotency_fields(tool_name: str) -> tuple[str, ...]:
    entry = get_tool(tool_name) or {}
    return tuple(entry.get("idempotency_fields", ()) or ())


def validate_tool_input(tool_name: str, input_args: dict | None) -> tuple[dict, list[str]]:
    normalized = normalize_tool_input(tool_name, input_args)
    entry = get_tool(tool_name) or {}
    schema = entry.get("schema", {}) or {}
    properties = dict(schema.get("properties") or {})
    errors: list[str] = []

    for key in schema.get("required", []) or entry.get("expected_params", []):
        if key not in normalized:
            errors.append(f"Missing required parameter: {key}")

    for key, value in normalized.items():
        spec = properties.get(key)
        if not spec:
            continue
        expected_type = spec.get("type")
        if value is None and spec.get("nullable"):
            continue
        if expected_type == "string":
            if not isinstance(value, str):
                errors.append(f"Parameter '{key}' must be a string.")
                continue
            if spec.get("min_length") and len(value.strip()) < int(spec["min_length"]):
                errors.append(f"Parameter '{key}' must not be empty.")
            if spec.get("format") == "email" and ("@" not in value or "." not in value.split("@")[-1]):
                errors.append(f"Parameter '{key}' must be a valid email address.")
        elif expected_type == "integer":
            if isinstance(value, bool):
                errors.append(f"Parameter '{key}' must be an integer.")
                continue
            if isinstance(value, str) and value.isdigit():
                normalized[key] = int(value)
                value = normalized[key]
            if not isinstance(value, int):
                errors.append(f"Parameter '{key}' must be an integer.")
                continue
            if spec.get("minimum") is not None and value < int(spec["minimum"]):
                errors.append(f"Parameter '{key}' must be at least {spec['minimum']}.")
        elif expected_type == "array":
            if not isinstance(value, list):
                errors.append(f"Parameter '{key}' must be a list.")
        elif expected_type == "object":
            if not isinstance(value, dict):
                errors.append(f"Parameter '{key}' must be an object.")
        enum_values = spec.get("enum")
        if enum_values and value not in enum_values:
            errors.append(f"Parameter '{key}' must be one of: {', '.join(map(str, enum_values))}.")

    return normalized, errors


def get_tool_approval_requirement(tool_name: str) -> ApprovalRequirement:
    entry = get_tool(tool_name) or {}
    risk_level = str(entry.get("risk_level", "low") or "low")
    categories = list(entry.get("capability_groups", ()))
    if entry.get("toolkit"):
        categories.append(str(entry["toolkit"]).lower())
    return ApprovalRequirement(
        required=bool(entry.get("approval_required", False)),
        risk_level=risk_level,
        reason=str(entry.get("approval_reason") or entry.get("description") or ""),
        categories=categories,
        mode=str(entry.get("approval_mode", "auto") or "auto"),
    )


def get_tools_for_capability_request(
    agent_key: str,
    capability_group: str = "",
    toolkit_family: str = "",
    action_class: str = "",
) -> list[dict]:
    tools = get_tools_for_agent(agent_key)
    if capability_group:
        tools = [
            entry for entry in tools
            if capability_group in entry.get("capability_groups", ())
        ]
    if toolkit_family:
        normalized_toolkit = normalize_toolkit_key(toolkit_family) or str(toolkit_family).upper()
        tools = [
            entry for entry in tools
            if str(entry.get("toolkit", "")).upper() == normalized_toolkit
        ]
    if action_class:
        narrowed = [
            entry for entry in tools
            if action_class in entry.get("action_classes", ())
        ]
        if narrowed:
            tools = narrowed
    return tools
