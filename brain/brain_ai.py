"""
brain/brain_ai.py  —  BrainAI class: context retrieval + injection
"""
from typing import Optional
from sqlalchemy.orm import Session
from storage import repositories as repo


class BrainAI:
    """
    The shared memory layer for a workspace.
    Every helper gets context from here before executing.
    """

    def __init__(self, workspace_id: str, db: Session):
        self.workspace_id = workspace_id
        self.db = db

    # ── Profile ──────────────────────────────────────────────────────────────

    def get_profile(self) -> dict:
        brain = repo.get_brain(self.db, self.workspace_id)
        if not brain:
            return {}
        return {
            "company_name":  brain.company_name,
            "brand_context": brain.brand_context,
            "tone":          brain.tone,
            "audience":      brain.audience,
            "goals":         brain.goals,
            "services":      brain.services,
            "pricing":       brain.pricing,
            "competitors":   brain.competitors,
            "support_style": brain.support_style,
        }

    def update_profile(self, updates: dict):
        repo.update_brain(self.db, self.workspace_id, updates)

    # ── Knowledge ────────────────────────────────────────────────────────────

    def get_relevant_context(self, query: str, limit: int = 6) -> str:
        """
        Returns a formatted string of relevant knowledge items
        to inject into a helper's prompt.
        """
        profile = self.get_profile()
        knowledge_items = repo.get_knowledge(self.db, self.workspace_id, query, limit)

        parts = []

        # 1. Business profile summary
        if any(profile.values()):
            parts.append("=== BUSINESS PROFILE ===")
            if profile.get("company_name"):
                parts.append(f"Company: {profile['company_name']}")
            if profile.get("brand_context"):
                parts.append(f"About: {profile['brand_context']}")
            if profile.get("tone"):
                parts.append(f"Tone: {profile['tone']}")
            if profile.get("audience"):
                parts.append(f"Target Audience: {profile['audience']}")
            if profile.get("goals"):
                parts.append(f"Goals: {profile['goals']}")
            if profile.get("services"):
                parts.append(f"Services/Products: {profile['services']}")
            if profile.get("pricing"):
                parts.append(f"Pricing: {profile['pricing']}")

        # 2. Relevant knowledge items
        if knowledge_items:
            parts.append("\n=== RELEVANT KNOWLEDGE ===")
            for item in knowledge_items:
                parts.append(f"[{item.type.upper()}] {item.title}: {item.content[:400]}")

        return "\n".join(parts) if parts else "No business context available yet."

    def save_to_knowledge(self, title: str, content: str,
                          type_: str = "text", tags: list = None):
        repo.add_knowledge(
            self.db, self.workspace_id,
            type_=type_, title=title, content=content, tags=tags or []
        )

    # ── Missing fields detector (for quiz engine) ─────────────────────────────

    def get_missing_fields(self) -> list:
        profile = self.get_profile()
        missing = []
        field_map = {
            "company_name":  "What is your company name?",
            "brand_context": "Briefly describe your business.",
            "tone":          "What tone should your brand use? (e.g. professional, friendly, bold)",
            "audience":      "Who is your target audience?",
            "goals":         "What are your main business goals?",
            "services":      "What products or services do you offer?",
            "pricing":       "What is your pricing structure?",
            "competitors":   "Who are your main competitors?",
            "support_style": "How do you handle customer support? (e.g. empathetic, fast, formal)",
        }
        for field, question in field_map.items():
            if not profile.get(field):
                missing.append({"field": field, "question": question})
        return missing
