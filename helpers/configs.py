"""
helpers/configs.py  —  Backward-compatible helper definitions.

Source data is split into:
  - persona-only definitions in helpers/agent_personas.py
  - capability/tool policy in helpers/agent_capabilities.py

This module preserves the historical AGENTS shape for existing callers.
"""

from helpers.agent_personas import PERSONAS
from tools.capability_layer import resolve_agent_tool_access


AGENT_PRESENTATION: dict[str, dict[str, object]] = {
    "copywriter": {
        "name": "Penn",
        "use_cases": [
            "Write product descriptions",
            "Write ad copy",
            "Write email subject lines",
            "Write landing page headlines",
            "Write blog post outlines",
        ],
        "icon": "✍️",
        "color": "#6C63FF",
    },
    "seo": {
        "name": "Seomi",
        "use_cases": [
            "Keyword research",
            "Meta title and description optimization",
            "On-page SEO audit",
            "Content gap analysis",
            "Local SEO strategy",
        ],
        "icon": "🔍",
        "color": "#00B4D8",
    },
    "social_media": {
        "name": "Soshie",
        "use_cases": [
            "Write Instagram captions",
            "Create Twitter/X threads",
            "Write LinkedIn posts",
            "Plan a content calendar",
            "Suggest viral content ideas",
        ],
        "icon": "📱",
        "color": "#F72585",
    },
    "support": {
        "name": "Cassie",
        "use_cases": [
            "Draft support email replies",
            "Write FAQ answers",
            "Handle refund request responses",
            "Create support scripts",
            "Respond to negative reviews",
        ],
        "icon": "💬",
        "color": "#4CC9F0",
    },
    "sales": {
        "name": "Milli",
        "use_cases": [
            "Write cold email sequences",
            "Create sales pitch decks outline",
            "Write follow-up messages",
            "Analyze sales objections",
            "Write proposal templates",
        ],
        "icon": "💰",
        "color": "#F4A261",
    },
    "strategist": {
        "name": "Strat",
        "use_cases": [
            "SWOT analysis",
            "Business plan outline",
            "Market entry strategy",
            "Competitive analysis",
            "Growth strategy recommendations",
        ],
        "icon": "🧠",
        "color": "#7209B7",
    },
    "data_analyst": {
        "name": "Dexter",
        "use_cases": [
            "Analyze sales data",
            "Identify customer trends",
            "Interpret survey results",
            "Create data summaries",
            "Suggest KPIs to track",
        ],
        "icon": "📊",
        "color": "#3A86FF",
    },
    "assistant": {
        "name": "Buddy",
        "use_cases": [
            "Draft emails",
            "Summarize documents",
            "Create meeting agendas",
            "Research topics",
            "Set reminders and to-do lists",
        ],
        "icon": "🤖",
        "color": "#06D6A0",
    },
    "recruiter": {
        "name": "Remy",
        "use_cases": [
            "Write job descriptions",
            "Create interview questions",
            "Write LinkedIn recruiting messages",
            "Screen candidate summaries",
            "Write offer letters",
        ],
        "icon": "👥",
        "color": "#FFB703",
    },
    "email_marketer": {
        "name": "Emmie",
        "use_cases": [
            "Write welcome email sequences",
            "Create promotional campaigns",
            "Write cart abandonment emails",
            "Draft re-engagement sequences",
            "Write newsletters",
        ],
        "icon": "📧",
        "color": "#E76F51",
    },
    "designer_advisor": {
        "name": "Vizzy",
        "use_cases": [
            "Write design briefs",
            "Suggest brand color palettes",
            "Create website copy structure",
            "Advise on UI/UX improvements",
            "Write creative direction for campaigns",
        ],
        "icon": "🎨",
        "color": "#FF6B6B",
    },
    "finance_advisor": {
        "name": "Finn",
        "use_cases": [
            "Create budget templates",
            "Explain financial statements",
            "Write investor summary",
            "Suggest cost-cutting strategies",
            "Outline cash flow projections",
        ],
        "icon": "💹",
        "color": "#2DC653",
    },
}


def _merge_agent_config(agent_key: str) -> dict:
    persona = dict(PERSONAS[agent_key])
    presentation = dict(AGENT_PRESENTATION[agent_key])
    capability = resolve_agent_tool_access(agent_key)
    return {
        **presentation,
        **persona,
        "output_format": persona["communication_style"],
        "tool_mode": capability.get("tool_mode", "text_only"),
        "allowed_tools": list(capability.get("allowed_tools", [])),
        "requires_auth": capability.get("requires_auth", False),
        "capability_groups": list(capability.get("capability_groups", [])),
        "tool_policy": capability.get("tool_policy", ""),
        "tool_instructions": capability.get("legacy_tool_instructions", ""),
    }


AGENTS = {
    agent_key: _merge_agent_config(agent_key)
    for agent_key in AGENT_PRESENTATION.keys()
}


def get_agent(name: str) -> dict | None:
    return AGENTS.get((name or "").lower())


def list_agents() -> list:
    return [
        {
            "key": key,
            "name": info["name"],
            "role": info["role"],
            "icon": info["icon"],
            "color": info["color"],
            "use_cases": info["use_cases"],
        }
        for key, info in AGENTS.items()
    ]
