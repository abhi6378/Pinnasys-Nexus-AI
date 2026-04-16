"""
helpers/agent_capabilities.py  —  Agent-level capability policy overlays.

This file no longer carries tool inventories. Tool access is derived from the
canonical registry in tools/tool_registry.py, while these policies provide
agent-specific execution posture and communication guidance.
"""

AGENT_CAPABILITY_POLICIES: dict[str, dict[str, object]] = {
    "copywriter": {
        "tool_policy": (
            "Prefer text-only output. If live system access would be required, do not simulate it. "
            "Hand off live reads and writes to the appropriate tool-enabled helper."
        ),
    },
    "seo": {
        "tool_policy": (
            "Use live research only when current search or market context would materially improve the answer. "
            "Do not pretend to have current rankings or live web findings without verified tool output."
        ),
    },
    "social_media": {
        "tool_policy": (
            "Use live tools only for verified public social research or real publishing that the user clearly wants. "
            "Prefer drafting before publishing when intent is ambiguous, and never claim a post is live without a confirmed tool result."
        ),
    },
    "support": {
        "tool_policy": (
            "Use live tools only for verified inbox or Slack reads and real support actions. "
            "For scripts, FAQs, and drafted replies, respond with text. Never simulate inbox or Slack access."
        ),
    },
    "sales": {
        "tool_policy": (
            "Use live tools for verified CRM reads/writes and explicit outreach execution. "
            "Prefer read or discovery before write when records or targets are unclear. For sales copy, strategy, and pitch help, respond with text."
        ),
    },
    "strategist": {
        "tool_policy": (
            "Use live research when current market or competitor context would materially improve the answer. "
            "If live data is unavailable, say so clearly instead of fabricating it."
        ),
    },
    "data_analyst": {
        "tool_policy": (
            "Use live CRM or spreadsheet tools for verified data access and updates. "
            "Prefer reads before writes when the destination or target is unclear. For analysis on user-provided data, respond with text."
        ),
    },
    "assistant": {
        "tool_policy": (
            "Use tools only for real external actions or verified live data access that the user clearly wants. "
            "Prefer discovery or read actions before writes when needed. Prefer drafts over sends when the user has not explicitly asked to execute. "
            "For drafting, summarizing, planning, or explanation, respond with text."
        ),
    },
    "recruiter": {
        "tool_policy": (
            "Use live tools only for verified recruiting outreach or interview scheduling that the user clearly wants. "
            "Prefer draft or scheduling prep before execution when details are missing."
        ),
    },
    "email_marketer": {
        "tool_policy": (
            "Use live email tools only to inspect, draft, or send campaign emails when the request clearly calls for it. "
            "Never claim delivery without verified tool output, and prefer drafts when execution intent is unclear."
        ),
    },
    "designer_advisor": {
        "tool_policy": "Stay text-only. Do not simulate external design tools or publishing actions.",
    },
    "finance_advisor": {
        "tool_policy": "Stay text-only unless a future verified finance integration is explicitly available.",
    },
}


def get_capability_policy(agent_key: str) -> dict | None:
    return AGENT_CAPABILITY_POLICIES.get((agent_key or "").lower())
