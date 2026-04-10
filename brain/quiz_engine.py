"""
brain/quiz_engine.py  —  Adaptive onboarding quiz for Brain AI
"""
from sqlalchemy.orm import Session
from brain.brain_ai import BrainAI
from storage import repositories as repo


CATEGORY_MAP = {
    "company_name":  "identity",
    "brand_context": "identity",
    "tone":          "brand",
    "audience":      "marketing",
    "goals":         "strategy",
    "services":      "product",
    "pricing":       "product",
    "competitors":   "strategy",
    "support_style": "support",
}


def get_next_question(workspace_id: str, db: Session) -> dict | None:
    """
    Returns the next unanswered question as {field, question, category}
    or None if Brain AI is fully populated.
    """
    brain = BrainAI(workspace_id, db)
    missing = brain.get_missing_fields()
    if not missing:
        return None
    return {**missing[0], "category": CATEGORY_MAP.get(missing[0]["field"], "general")}


def save_answer(workspace_id: str, field: str,
                question: str, answer: str, db: Session):
    """
    Saves quiz answer to both quiz_answers table and brain_profiles.
    """
    category = CATEGORY_MAP.get(field, "general")
    repo.save_quiz_answer(db, workspace_id, question, answer, category)
    repo.update_brain(db, workspace_id, {field: answer})


def quiz_progress(workspace_id: str, db: Session) -> dict:
    """
    Returns progress info: how many fields filled vs total.
    """
    brain = BrainAI(workspace_id, db)
    profile = brain.get_profile()
    total = len(profile)
    filled = sum(1 for v in profile.values() if v)
    return {
        "total": total,
        "filled": filled,
        "percent": int((filled / total) * 100) if total else 0,
        "complete": filled == total
    }
