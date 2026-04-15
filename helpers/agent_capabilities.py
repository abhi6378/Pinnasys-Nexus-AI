"""
helpers/agent_capabilities.py  —  Agent-level capability policy overlays.

This file no longer carries tool inventories. Tool access is derived from the
canonical registry in tools/tool_registry.py, while these policies provide
agent-specific execution posture and communication guidance.
"""

AGENT_CAPABILITY_POLICIES: dict[str, dict[str, object]] = {
    "copywriter": {},
    "seo": {
        "tool_policy": "Use live web research when current SEO context would materially improve the answer.",
    },
    "social_media": {
        "tool_policy": (
            "Use live tools only for real publishing or current public social research. "
            "For drafting captions, threads, and plans, respond with text."
        ),
    },
    "support": {
        "tool_policy": (
            "Use live tools only for real email or Slack actions. "
            "For scripts, FAQs, and drafted replies, respond with text."
        ),
    },
    "sales": {
        "tool_policy": (
            "Use live tools for real outreach or CRM actions. "
            "For sales copy, strategy, and pitch help, respond with text."
        ),
    },
    "strategist": {
        "tool_policy": "Use live web research when current market or competitor context would materially improve the answer.",
    },
    "data_analyst": {
        "tool_policy": (
            "Use live CRM or spreadsheet tools for real data access and updates. "
            "For analysis on user-provided data, respond with text."
        ),
    },
    "assistant": {
        "tool_policy": (
            "Use tools only for real external actions or live data access that the user clearly wants. "
            "For drafting, summarizing, planning, or explanation, respond with text."
        ),
    },
    "recruiter": {
        "tool_policy": (
            "Use live tools only to send recruiting emails or schedule interviews. "
            "For job descriptions, screening, and interview prep, respond with text."
        ),
    },
    "email_marketer": {
        "tool_policy": (
            "Use live email tools only to send, draft, or inspect campaign emails. "
            "For email copywriting, respond with text."
        ),
    },
    "designer_advisor": {},
    "finance_advisor": {},
}


def get_capability_policy(agent_key: str) -> dict | None:
    return AGENT_CAPABILITY_POLICIES.get((agent_key or "").lower())
