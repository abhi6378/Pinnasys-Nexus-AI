"""
brain/brain_ai.py  —  BrainAI class: hybrid memory retrieval + injection
"""
from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt
from typing import Optional

from sqlalchemy.orm import Session

from brain.embedding_service import get_embedding_service
from models.contracts import MemoryContextPack
from storage import repositories as repo


def _repo_call(name: str):
    return getattr(repo, name, None)


def _coerce_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _compact_text(value: str | None, limit: int = 260) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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

    # ── Internal retrieval helpers ───────────────────────────────────────────

    def _get_working_memory(self) -> dict:
        getter = _repo_call("get_working_memory")
        if not callable(getter):
            return {}
        state = getter(self.db, self.workspace_id)
        if not state:
            return {}
        return {
            "current_goal": getattr(state, "current_goal", "") or "",
            "active_tasks": list(getattr(state, "active_tasks", []) or []),
            "open_questions": list(getattr(state, "open_questions", []) or []),
            "current_draft_summary": getattr(state, "current_draft_summary", "") or "",
            "recent_tool_summary": getattr(state, "recent_tool_summary", "") or "",
            "latest_workflow_summary": getattr(state, "latest_workflow_summary", "") or "",
            "project_focus": getattr(state, "project_focus", "") or "",
            "state_json": dict(getattr(state, "state_json", {}) or {}),
        }

    def _legacy_knowledge(self, query: str, limit: int = 2) -> list[dict]:
        try:
            items = repo.get_knowledge(self.db, self.workspace_id, query, limit)
        except Exception:
            return []
        result = []
        for item in items:
            result.append(
                {
                    "title": getattr(item, "title", ""),
                    "content": getattr(item, "content", ""),
                    "type": getattr(item, "type", "text"),
                }
            )
        return result

    def _semantic_memory_candidates(self, query: str, limit: int = 8) -> list[tuple[str, float]]:
        list_memory_embeddings = _repo_call("list_memory_embeddings")
        if not callable(list_memory_embeddings):
            return []
        query_text = str(query or "").strip()
        if len(query_text) < 8:
            return []
        service = get_embedding_service()
        query_vector = service.embed_text(query_text)
        if not query_vector:
            return []
        embeddings = list_memory_embeddings(
            self.db,
            self.workspace_id,
            model_name=service.model_name,
            limit=200,
        )
        scored: list[tuple[str, float]] = []
        for row in embeddings:
            vector = list(getattr(row, "vector_json", []) or [])
            score = _cosine_similarity(query_vector, vector)
            if score > 0.15:
                scored.append((getattr(row, "memory_record_id", ""), score))
        scored.sort(key=lambda pair: -pair[1])
        return scored[:limit]

    def _memory_type_boost(self, memory_type: str) -> float:
        boosts = {
            "preference": 0.8,
            "project_context": 0.8,
            "procedural": 0.7,
            "workflow_summary": 0.5,
            "tool_outcome": 0.4,
            "semantic_fact": 0.5,
            "episodic": 0.3,
        }
        return boosts.get(memory_type, 0.2)

    def _rank_memories(self, query: str, limit: int) -> list[dict]:
        list_memory_records = _repo_call("list_memory_records")
        search_memory_records = _repo_call("search_memory_records")
        get_memory_records_by_ids = _repo_call("get_memory_records_by_ids")
        if not callable(list_memory_records):
            return []

        lexical_candidates = []
        if callable(search_memory_records):
            lexical_candidates = search_memory_records(self.db, self.workspace_id, query, limit=20)
        pinned_candidates = list_memory_records(self.db, self.workspace_id, limit=8, pinned_only=True)
        recent_candidates = list_memory_records(self.db, self.workspace_id, limit=20)

        semantic_score_map = dict(self._semantic_memory_candidates(query, limit=12))
        semantic_records = []
        if semantic_score_map and callable(get_memory_records_by_ids):
            semantic_records = get_memory_records_by_ids(
                self.db,
                self.workspace_id,
                list(semantic_score_map.keys()),
            )

        combined = {}
        lexical_ids = {getattr(item, "id", ""): 1.0 + index * -0.03 for index, item in enumerate(lexical_candidates)}
        for record in [*pinned_candidates, *lexical_candidates, *semantic_records, *recent_candidates]:
            record_id = getattr(record, "id", "")
            if not record_id:
                continue
            combined[record_id] = record

        ranked: list[tuple[float, object]] = []
        now = datetime.now(UTC)
        for record_id, record in combined.items():
            importance = float(getattr(record, "importance_score", 0.0) or 0.0)
            confidence = float(getattr(record, "confidence_score", 0.0) or 0.0)
            memory_type = str(getattr(record, "memory_type", "") or "")
            is_pinned = bool(getattr(record, "pinned", False))
            updated_at = _coerce_datetime(getattr(record, "updated_at", None) or getattr(record, "created_at", None))
            days_old = max((now - updated_at).total_seconds() / 86400.0, 0.0)
            recency = 0.5 if days_old < 2 else 0.25 if days_old < 14 else 0.0
            lexical = lexical_ids.get(record_id, 0.0)
            semantic = semantic_score_map.get(record_id, 0.0) * 2.0
            score = (
                semantic
                + lexical
                + importance * 1.4
                + confidence * 0.8
                + self._memory_type_boost(memory_type)
                + recency
                + (2.0 if is_pinned else 0.0)
            )
            ranked.append((score, record))

        ranked.sort(key=lambda pair: -pair[0])
        result: list[dict] = []
        for _, record in ranked[:limit]:
            result.append(
                {
                    "id": getattr(record, "id", ""),
                    "memory_type": getattr(record, "memory_type", ""),
                    "title": getattr(record, "title", ""),
                    "summary": getattr(record, "summary", "") or getattr(record, "content", ""),
                    "content": getattr(record, "content", ""),
                    "tags": list(getattr(record, "tags", []) or []),
                    "tool_tags": list(getattr(record, "tool_tags", []) or []),
                    "entity_tags": list(getattr(record, "entity_tags", []) or []),
                    "importance_score": float(getattr(record, "importance_score", 0.0) or 0.0),
                    "pinned": bool(getattr(record, "pinned", False)),
                }
            )
        return result

    def get_memory_pack(self, query: str, limit: int = 6) -> MemoryContextPack:
        return MemoryContextPack(
            profile=self.get_profile(),
            working_memory=self._get_working_memory(),
            memories=self._rank_memories(query, limit),
            legacy_knowledge=self._legacy_knowledge(query, limit=2),
        )

    def _format_memory_pack(self, pack: MemoryContextPack) -> str:
        parts: list[str] = []
        profile = pack.profile or {}
        working = pack.working_memory or {}

        if any(profile.values()):
            parts.append("=== BUSINESS PROFILE ===")
            if profile.get("company_name"):
                parts.append(f"Company: {profile['company_name']}")
            if profile.get("brand_context"):
                parts.append(f"About: {_compact_text(profile['brand_context'], 300)}")
            if profile.get("tone"):
                parts.append(f"Tone: {profile['tone']}")
            if profile.get("audience"):
                parts.append(f"Target Audience: {_compact_text(profile['audience'], 220)}")
            if profile.get("goals"):
                parts.append(f"Goals: {_compact_text(profile['goals'], 220)}")
            if profile.get("services"):
                parts.append(f"Services/Products: {_compact_text(profile['services'], 220)}")
            if profile.get("pricing"):
                parts.append(f"Pricing: {_compact_text(profile['pricing'], 180)}")

        working_lines = []
        if working.get("current_goal"):
            working_lines.append(f"Current Goal: {_compact_text(working['current_goal'], 220)}")
        if working.get("project_focus"):
            working_lines.append(f"Project Focus: {_compact_text(working['project_focus'], 220)}")
        if working.get("active_tasks"):
            working_lines.append(f"Active Tasks: {', '.join(list(working['active_tasks'])[:4])}")
        if working.get("open_questions"):
            working_lines.append(f"Open Questions: {', '.join(list(working['open_questions'])[:4])}")
        if working.get("current_draft_summary"):
            working_lines.append(f"Current Draft: {_compact_text(working['current_draft_summary'], 220)}")
        if working.get("recent_tool_summary"):
            working_lines.append(f"Recent Tool Outcome: {_compact_text(working['recent_tool_summary'], 220)}")
        if working.get("latest_workflow_summary"):
            working_lines.append(f"Latest Workflow: {_compact_text(working['latest_workflow_summary'], 220)}")
        if working_lines:
            parts.append("\n=== WORKING MEMORY ===")
            parts.extend(working_lines)

        if pack.memories:
            parts.append("\n=== KEY MEMORIES ===")
            for item in pack.memories[:6]:
                summary = _compact_text(item.get("summary") or item.get("content"), 220)
                label = str(item.get("memory_type", "memory")).upper()
                title = item.get("title") or "Memory"
                parts.append(f"[{label}] {title}: {summary}")

        if pack.legacy_knowledge:
            parts.append("\n=== LEGACY KNOWLEDGE ===")
            for item in pack.legacy_knowledge[:2]:
                parts.append(
                    f"[{str(item.get('type', 'text')).upper()}] {item.get('title', 'Knowledge')}: "
                    f"{_compact_text(item.get('content', ''), 220)}"
                )

        return "\n".join(parts) if parts else "No business context available yet."

    # ── Knowledge / Memory retrieval ────────────────────────────────────────

    def get_relevant_context(self, query: str, limit: int = 6) -> str:
        """
        Returns a formatted string of relevant memory to inject into a helper's prompt.
        """
        pack = self.get_memory_pack(query, limit=limit)
        return self._format_memory_pack(pack)

    def save_to_knowledge(self, title: str, content: str,
                          type_: str = "text", tags: list = None):
        item = repo.add_knowledge(
            self.db, self.workspace_id,
            type_=type_, title=title, content=content, tags=tags or []
        )
        upsert_memory_record = _repo_call("upsert_memory_record")
        if callable(upsert_memory_record):
            upsert_memory_record(
                self.db,
                self.workspace_id,
                memory_type="semantic_fact",
                title=title,
                content=content,
                summary=_compact_text(content, 300),
                source_kind="knowledge_item",
                source_reference_id=getattr(item, "id", ""),
                tags=tags or [],
                importance_score=0.55,
                confidence_score=0.85,
                canonical_key=f"semantic_fact:{title.lower().strip().replace(' ', '_')}" if title else "",
                metadata_json={"legacy_knowledge_type": type_},
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
