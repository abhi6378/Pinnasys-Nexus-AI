# ============================================================
# orchestrator/intent_classifier.py  (v6)
# ============================================================
# WHAT THIS IS:
#   Deterministic keyword-based intent classifier.
#   Reads user input → maps to workflow key or agent name.
#   Zero LLM involvement. Pure Python.
#
# v6 IMPROVEMENT (spec requirement):
#   Replace naive single-keyword checks with simple multi-keyword
#   classifier using any() for readability and reliability.
#
# SPEC PATTERN:
#   def detect_workflow(input):
#       keywords = ["campaign", "strategy", "plan", "launch"]
#       if any(k in input.lower() for k in keywords):
#           return "marketing"
#       return None
#
# This file implements that pattern cleanly across all workflows
# and extends it with regex for precision where needed.
#
# PRIORITY ORDER (evaluated top → bottom, first match wins):
#   1. Explicit multi-step workflow patterns
#   2. Single-agent keyword groups
#   3. None → LLM router (last resort)
# ============================================================

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    """Result of intent classification."""
    workflow:    str | None
    confidence:  str            # "high" | "medium" | "low"
    matched_on:  list[str] = field(default_factory=list)
    explanation: str = ""


# ── WORKFLOW KEYWORD GROUPS ───────────────────────────────────
# Each entry: (workflow_key, simple_keywords, regex_patterns)
# simple_keywords — uses any(k in text for k in keywords) pattern
# regex_patterns  — for precision matching
#
# EVALUATION: simple keywords checked first (fast path),
# then regex for ambiguous cases.

WORKFLOW_DEFINITIONS: list[dict] = [

    {
        "key":      "product_launch",
        "keywords": ["product launch", "go-to-market", "gtm plan", "prd",
                     "product roadmap", "launch plan", "launch campaign"],
        "regex":    [r"\blaunch (the |a |our )?(product|feature|app|saas|tool)\b",
                     r"\bannounce (the |a |our )?(product|release|feature)\b"],
    },

    {
        "key":      "full_marketing_blast",
        "keywords": ["full marketing", "end-to-end marketing", "complete marketing",
                     "full campaign", "entire campaign", "marketing package",
                     "from strategy to social", "strategy to content"],
        "regex":    [r"\bfull (marketing|campaign|content) (plan|strategy|package|blast)\b"],
    },

    {
        "key":      "sales_campaign",
        "keywords": ["sales campaign", "sales strategy", "cold outreach",
                     "outreach sequence", "b2b campaign", "lead generation campaign",
                     "sales pipeline", "prospecting strategy"],
        "regex":    [r"\bcold (email|outreach|pitch) (sequence|campaign|strategy)\b"],
    },

    {
        "key":      "support_and_report",
        "keywords": ["support report", "customer complaint analysis",
                     "churn analysis", "support analysis", "ticket analysis",
                     "support insights", "customer feedback analysis"],
        "regex":    [],
    },

    {
        "key":      "content_pipeline",
        "keywords": ["content pipeline", "content strategy", "seo content",
                     "write and seo", "blog with seo", "content plan",
                     "content calendar", "content marketing plan"],
        "regex":    [r"\b(write|create) .{0,20} (then|and) (seo|optimize|social)\b"],
    },

    {
        "key":      "email_campaign",
        "keywords": ["email campaign", "email sequence", "drip campaign",
                     "welcome sequence", "nurture campaign", "email series",
                     "email funnel", "onboarding sequence"],
        "regex":    [],
    },

    {
        "key":      "research_and_write",
        "keywords": ["data to content", "research and write", "data-driven article",
                     "insights into content", "analyze then write",
                     "turn data into", "data to blog", "research to article"],
        "regex":    [r"\b(analyze|analyse) .{0,20} (and|then) (write|create|draft)\b"],
    },

    {
        "key":      "data_to_social",
        "keywords": ["data to social", "metrics to social", "analytics to social",
                     "data insights social", "stats to posts"],
        "regex":    [r"\b(data|metrics|stats|analytics) .{0,20} social\b"],
    },
]

# ── AGENT KEYWORD GROUPS ──────────────────────────────────────
# Simple keyword groups for single-agent routing.
# any(k in text for k in keywords) pattern — spec requirement.

AGENT_DEFINITIONS: list[dict] = [
    {"agent": "Copywriter",           "keywords": ["write copy", "draft copy", "write content",
                                                    "blog post", "headline", "tagline", "ad copy",
                                                    "write an article", "create content"]},
    {"agent": "SEO Specialist",       "keywords": ["seo", "keywords", "search engine", "meta",
                                                    "ranking", "serp", "keyword research",
                                                    "on-page", "backlinks"]},
    {"agent": "Social Media Manager", "keywords": ["instagram", "linkedin post", "twitter",
                                                    "tiktok", "social media", "hashtag", "caption",
                                                    "social post", "reels"]},
    {"agent": "Email Marketer",       "keywords": ["email", "subject line", "newsletter",
                                                    "open rate", "ctr", "email marketing",
                                                    "welcome email"]},
    {"agent": "Sales Strategist",     "keywords": ["sales pitch", "cold email", "crm",
                                                    "close deal", "prospect", "outreach",
                                                    "sales script", "objection"]},
    {"agent": "Business Strategist",  "keywords": ["strategy", "swot", "market analysis",
                                                    "okr", "business plan", "competitive analysis",
                                                    "go to market", "growth strategy"]},
    {"agent": "Data Analyst",         "keywords": ["data", "analytics", "metrics", "kpi",
                                                    "dashboard", "spreadsheet", "sql", "report",
                                                    "trend", "insights"]},
    {"agent": "Customer Support",     "keywords": ["customer complaint", "refund", "ticket",
                                                    "support request", "help desk", "escalation",
                                                    "angry customer", "billing issue"]},
    {"agent": "PR Specialist",        "keywords": ["press release", "media pitch", "journalist",
                                                    "pr campaign", "publicity", "brand story",
                                                    "news angle", "media coverage"]},
    {"agent": "Product Manager",      "keywords": ["prd", "product spec", "roadmap", "feature",
                                                    "user story", "backlog", "sprint", "product plan"]},
    {"agent": "eCom Specialist",      "keywords": ["shopify", "ecommerce", "product listing",
                                                    "roas", "aov", "ltv", "conversion rate",
                                                    "product page"]},
    {"agent": "Recruiter",            "keywords": ["job description", "jd", "hire", "talent",
                                                    "recruit", "candidate", "interview",
                                                    "job post"]},
    {"agent": "Personal Growth",      "keywords": ["habit", "mindset", "productivity",
                                                    "goal setting", "morning routine", "coaching",
                                                    "motivation", "self improvement"]},
    {"agent": "Virtual Assistant",    "keywords": ["schedule", "summarize", "draft email",
                                                    "reminder", "organize", "plan my",
                                                    "help me with", "quick task"]},
]


class IntentClassifier:
    """
    Simple, readable keyword classifier — exactly as described in spec.

    Spec pattern implemented:
        def detect_workflow(input):
            keywords = ["campaign", "strategy", "plan", "launch"]
            if any(k in input.lower() for k in keywords):
                return "marketing"
            return None

    Extended with:
      - Per-workflow keyword groups
      - Optional regex for precision
      - Agent hint detection for single-agent routing
    """

    def classify(self, user_input: str) -> IntentResult:
        """Classify input into a workflow key or None."""
        text = user_input.lower().strip()

        for wf_def in WORKFLOW_DEFINITIONS:
            # Simple keyword check (spec pattern)
            matched_kw = [
                k for k in wf_def["keywords"]
                if k in text
            ]

            # Regex check (precision)
            matched_rx = [
                rx for rx in wf_def.get("regex", [])
                if re.search(rx, text)
            ]

            if matched_kw or matched_rx:
                matched_all = matched_kw + matched_rx
                confidence  = "high" if len(matched_all) >= 2 else "medium"
                explanation = (
                    f"Matched workflow '{wf_def['key']}' on: "
                    f"{matched_all[:3]}"
                )
                logger.info(f"[INTENT] {explanation}")
                return IntentResult(
                    workflow=wf_def["key"],
                    confidence=confidence,
                    matched_on=matched_all[:3],
                    explanation=explanation,
                )

        logger.info(f"[INTENT] No workflow matched for: '{text[:60]}'")
        return IntentResult(
            workflow=None,
            confidence="low",
            matched_on=[],
            explanation="No workflow pattern matched. Will route to single agent.",
        )

    def suggest_agent(self, user_input: str) -> str | None:
        """
        Suggest a single agent based on keyword groups.
        Returns agent name or None (→ LLM router).

        Implements: any(k in input.lower() for k in keywords)
        """
        text = user_input.lower().strip()

        for agent_def in AGENT_DEFINITIONS:
            if any(k in text for k in agent_def["keywords"]):
                agent = agent_def["agent"]
                logger.info(f"[INTENT] Agent hint: '{agent}'")
                return agent

        return None


# ── MODULE-LEVEL CONVENIENCE FUNCTIONS ────────────────────────
# Used by Orchestrator directly.

_classifier = IntentClassifier()


def detect_workflow(user_input: str) -> str | None:
    """
    Returns workflow key or None.

    Implements the spec pattern:
        keywords = ["campaign", "strategy", ...]
        if any(k in input.lower() for k in keywords):
            return "marketing"
    """
    return _classifier.classify(user_input).workflow


def detect_agent_hint(user_input: str) -> str | None:
    """Returns best matching single agent name, or None."""
    return _classifier.suggest_agent(user_input)


def classify_full(user_input: str) -> IntentResult:
    """Returns full IntentResult for UI display."""
    return _classifier.classify(user_input)
