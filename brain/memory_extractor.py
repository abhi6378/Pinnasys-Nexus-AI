"""
brain/memory_extractor.py  —  Selective memory ingestion for BrainAI.

Backward compatibility:
  - ``extract_and_save(workspace_id, content, db)`` remains the public entry point.
  - Existing callers can keep passing only the legacy three arguments.
  - Ingestion is still best-effort and never crashes the main request flow.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from brain.embedding_service import embedding_content_hash, get_embedding_service
from brain.memory_sanitizer import (
    sanitize_structure_for_memory,
    sanitize_text_for_memory,
    summarize_safe_tool_outcome,
)
from models.contracts import MemoryRecordInput, WorkingMemoryUpdate
from llm.client import generate_json
from storage import repositories as repo
from utils.logging_utils import log_event, log_exception


logger = logging.getLogger(__name__)

PROFILE_FIELDS = {
    "company_name",
    "brand_context",
    "tone",
    "audience",
    "goals",
    "services",
    "pricing",
    "competitors",
    "support_style",
}

LONG_TERM_MEMORY_TYPES = {
    "profile",
    "preference",
    "semantic_fact",
    "episodic",
    "procedural",
    "workflow_summary",
    "tool_outcome",
    "project_context",
}

EMBEDDABLE_MEMORY_TYPES = {
    "preference",
    "semantic_fact",
    "episodic",
    "procedural",
    "workflow_summary",
    "tool_outcome",
    "project_context",
}

NOISY_OUTPUT_PATTERN = re.compile(
    r"(needs access to|connect now|requested an invalid tool|tried to use|integration unavailable|^error:)",
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """
You are a selective business memory extraction system.

Extract only durable, useful, non-sensitive memory for future tasks.
Do not store raw transcripts. Do not store secrets, tokens, passwords, OAuth payloads,
auth headers, API keys, cookies, or credential material.

Return JSON only with this shape:
{{
  "memory_worthy": true,
  "profile_updates": {{
    "company_name": "",
    "brand_context": "",
    "tone": "",
    "audience": "",
    "goals": "",
    "services": "",
    "pricing": "",
    "competitors": "",
    "support_style": ""
  }},
  "memory_records": [
    {{
      "memory_type": "preference | semantic_fact | episodic | procedural | workflow_summary | project_context",
      "title": "short title",
      "content": "durable memory content",
      "summary": "short summary",
      "tags": ["tag"],
      "entity_tags": ["entity"],
      "tool_tags": [],
      "importance_score": 0.0,
      "confidence_score": 0.0,
      "pinned": false,
      "canonical_key": "stable_dedupe_key_or_empty"
    }}
  ],
  "working_memory": {{
    "current_goal": "",
    "active_tasks": [],
    "open_questions": [],
    "current_draft_summary": "",
    "project_focus": ""
  }}
}}

Rules:
- Prefer profile, preferences, project context, procedures, and distilled episodic summaries.
- Only extract memories that would help future tasks.
- Ignore generic pleasantries and transient filler.
- If the content is not memory-worthy, return memory_worthy=false with empty records.

Context bundle:
{content}
"""


def _repo_call(name: str):
    return getattr(repo, name, None)


def _safe_list(value, *, limit: int = 8) -> list[str]:
    result: list[str] = []
    for item in value or []:
        text = sanitize_text_for_memory(str(item), max_length=160)
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _clamp_score(value, default: float = 0.5) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    return max(0.0, min(1.0, numeric))


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _canonical_key(memory_type: str, title: str = "", content: str = "") -> str:
    base = _slugify(title) or _slugify(content[:80])
    if not base:
        return ""
    return f"{memory_type}:{base[:120]}"


def _is_memory_worthy(bundle: dict) -> bool:
    content = str(bundle.get("assistant_output", "") or "")
    user_input = str(bundle.get("user_input", "") or "")
    tool_events = bundle.get("tool_events") or []
    workflow_name = str(bundle.get("workflow_name", "") or "")
    if tool_events or workflow_name:
        return True
    combined = f"{user_input}\n{content}".strip()
    if len(combined) < 80:
        return False
    if NOISY_OUTPUT_PATTERN.search(content) and len(user_input) < 40:
        return False
    return True


def _summarize_workflow(workflow_name: str, assistant_output: str, workflow_steps: list[dict]) -> tuple[str, dict]:
    if not workflow_name:
        return "", {}
    step_labels = [str(step.get("step", "")).strip() for step in workflow_steps if step.get("step")]
    step_text = " -> ".join(step_labels[:6])
    summary = f"Workflow '{workflow_name}' completed."
    if step_text:
        summary += f" Steps: {step_text}."
    output_excerpt = sanitize_text_for_memory(assistant_output, max_length=500)
    if output_excerpt:
        summary += f" Outcome: {output_excerpt}"
    return sanitize_text_for_memory(summary, max_length=700), {"step_labels": step_labels}


def _build_tool_event_records(bundle: dict) -> tuple[list[MemoryRecordInput], str]:
    records: list[MemoryRecordInput] = []
    summaries: list[str] = []
    seen_keys: set[str] = set()

    direct_tool_name = str(bundle.get("tool_used", "") or "")
    if direct_tool_name:
        direct_summary = summarize_safe_tool_outcome(
            tool_name=direct_tool_name,
            toolkit=str(bundle.get("toolkit", "") or ""),
            status=str(bundle.get("tool_status", "success") or "success"),
            verified=bool(bundle.get("tool_verified", True)),
            output=bundle.get("tool_output"),
            error=str(bundle.get("tool_error", "") or ""),
        )
        if direct_summary:
            key = f"tool_outcome:{_slugify(direct_tool_name)}"
            seen_keys.add(key)
            summaries.append(direct_summary["summary"])
            records.append(
                MemoryRecordInput(
                    memory_type="tool_outcome",
                    title=f"{direct_tool_name} outcome",
                    content=direct_summary["summary"],
                    summary=direct_summary["summary"],
                    source_kind="tool_execution",
                    tags=["tool", "verified_outcome"],
                    tool_tags=[direct_tool_name],
                    importance_score=0.55,
                    confidence_score=0.9,
                    canonical_key=key,
                    metadata_json={"safe_output": direct_summary["safe_output"]},
                )
            )

    for step in bundle.get("workflow_steps") or []:
        tool_name = str(step.get("tool_used", "") or "")
        if not tool_name:
            continue
        key = f"tool_outcome:{_slugify(tool_name)}"
        if key in seen_keys:
            continue
        safe_summary = summarize_safe_tool_outcome(
            tool_name=tool_name,
            status="success",
            verified=True,
            output=step.get("tool_output"),
        )
        if not safe_summary:
            continue
        seen_keys.add(key)
        summaries.append(safe_summary["summary"])
        records.append(
            MemoryRecordInput(
                memory_type="tool_outcome",
                title=f"{tool_name} outcome",
                content=safe_summary["summary"],
                summary=safe_summary["summary"],
                source_kind="workflow_step",
                source_reference_id=str(step.get("step", "") or ""),
                tags=["tool", "workflow"],
                tool_tags=[tool_name],
                importance_score=0.5,
                confidence_score=0.9,
                canonical_key=key,
                metadata_json={"safe_output": safe_summary["safe_output"]},
            )
        )

    return records, sanitize_text_for_memory(" ".join(summaries), max_length=600)


def _parse_memory_record_item(item: dict, *, source_kind: str, source_reference_id: str = "") -> MemoryRecordInput | None:
    if not isinstance(item, dict):
        return None
    memory_type = str(item.get("memory_type", "") or "").strip()
    if memory_type not in LONG_TERM_MEMORY_TYPES:
        return None
    title = sanitize_text_for_memory(item.get("title", ""), max_length=200)
    content = sanitize_text_for_memory(item.get("content", ""), max_length=1800)
    summary = sanitize_text_for_memory(item.get("summary", content[:300]), max_length=600)
    if not content and not summary:
        return None
    canonical_key = sanitize_text_for_memory(
        item.get("canonical_key") or _canonical_key(memory_type, title, summary or content),
        max_length=160,
    )
    return MemoryRecordInput(
        memory_type=memory_type,
        title=title,
        content=content or summary,
        summary=summary or content[:300],
        source_kind=source_kind,
        source_reference_id=source_reference_id,
        tags=_safe_list(item.get("tags")),
        entity_tags=_safe_list(item.get("entity_tags")),
        tool_tags=_safe_list(item.get("tool_tags")),
        importance_score=_clamp_score(item.get("importance_score"), default=0.5),
        confidence_score=_clamp_score(item.get("confidence_score"), default=0.7),
        pinned=bool(item.get("pinned", False)),
        canonical_key=canonical_key,
        metadata_json={},
    )


def _parse_legacy_fact_payload(data: dict) -> tuple[list[MemoryRecordInput], dict]:
    records: list[MemoryRecordInput] = []
    for fact in data.get("facts", []) or []:
        content = sanitize_text_for_memory(fact.get("content", ""), max_length=1200)
        if not content:
            continue
        title = sanitize_text_for_memory(fact.get("title", "Extracted fact"), max_length=200)
        records.append(
            MemoryRecordInput(
                memory_type="semantic_fact",
                title=title,
                content=content,
                summary=content[:300],
                source_kind="legacy_extractor",
                tags=_safe_list(fact.get("tags") or ["auto-extracted"]),
                importance_score=0.55,
                confidence_score=0.75,
                canonical_key=_canonical_key("semantic_fact", title, content),
            )
        )
    profile_updates = {
        key: sanitize_text_for_memory(value, max_length=500)
        for key, value in (data.get("profile_updates") or {}).items()
        if key in PROFILE_FIELDS and str(value or "").strip()
    }
    return records, profile_updates


def _normalize_llm_response(data: dict, bundle: dict) -> tuple[list[MemoryRecordInput], dict, WorkingMemoryUpdate]:
    if not isinstance(data, dict):
        return [], {}, WorkingMemoryUpdate()

    if "memory_records" not in data and "facts" in data:
        records, profile_updates = _parse_legacy_fact_payload(data)
        return records, profile_updates, WorkingMemoryUpdate()

    profile_updates = {
        key: sanitize_text_for_memory(value, max_length=500)
        for key, value in (data.get("profile_updates") or {}).items()
        if key in PROFILE_FIELDS and str(value or "").strip()
    }
    records = [
        parsed for parsed in (
            _parse_memory_record_item(
                item,
                source_kind=str(bundle.get("source_kind", "assistant_output") or "assistant_output"),
                source_reference_id=str(bundle.get("source_reference_id", "") or ""),
            )
            for item in (data.get("memory_records") or [])
        )
        if parsed is not None
    ]
    working_raw = data.get("working_memory") or {}
    working_update = WorkingMemoryUpdate(
        current_goal=sanitize_text_for_memory(working_raw.get("current_goal", ""), max_length=300),
        active_tasks=_safe_list(working_raw.get("active_tasks"), limit=6),
        open_questions=_safe_list(working_raw.get("open_questions"), limit=6),
        current_draft_summary=sanitize_text_for_memory(working_raw.get("current_draft_summary", ""), max_length=500),
        project_focus=sanitize_text_for_memory(working_raw.get("project_focus", ""), max_length=300),
        state_json={},
    )
    return records, profile_updates, working_update


def _should_call_llm(bundle: dict) -> bool:
    combined = "\n".join(
        part for part in (
            bundle.get("user_input", ""),
            bundle.get("assistant_output", ""),
            bundle.get("workflow_summary", ""),
        ) if str(part or "").strip()
    ).strip()
    return len(combined) >= 80


def _build_extraction_bundle(workspace_id: str, content: str, **kwargs) -> dict:
    user_input = sanitize_text_for_memory(kwargs.get("user_input", ""), max_length=1200)
    assistant_output = sanitize_text_for_memory(kwargs.get("assistant_output", content), max_length=1800)
    workflow_steps = sanitize_structure_for_memory(kwargs.get("workflow_steps") or [])
    workflow_name = sanitize_text_for_memory(kwargs.get("workflow_name", ""), max_length=120)
    source_kind = sanitize_text_for_memory(kwargs.get("source_kind", "assistant_output"), max_length=80) or "assistant_output"
    source_reference_id = sanitize_text_for_memory(kwargs.get("source_reference_id", ""), max_length=120)

    workflow_summary, workflow_meta = _summarize_workflow(workflow_name, assistant_output, workflow_steps)
    tool_records, tool_summary = _build_tool_event_records(
        {
            "tool_used": kwargs.get("tool_used", ""),
            "tool_output": sanitize_structure_for_memory(kwargs.get("tool_output")),
            "toolkit": kwargs.get("toolkit", ""),
            "tool_status": kwargs.get("tool_status", "success"),
            "tool_verified": kwargs.get("tool_verified", True),
            "tool_error": kwargs.get("tool_error", ""),
            "workflow_steps": workflow_steps,
        }
    )

    return {
        "workspace_id": workspace_id,
        "user_input": user_input,
        "assistant_output": assistant_output,
        "workflow_name": workflow_name,
        "workflow_steps": workflow_steps,
        "workflow_summary": workflow_summary,
        "workflow_meta": workflow_meta,
        "tool_records": tool_records,
        "tool_summary": tool_summary,
        "source_kind": source_kind,
        "source_reference_id": source_reference_id,
        "agent_key": sanitize_text_for_memory(kwargs.get("agent_key", ""), max_length=80),
        "route_context": sanitize_structure_for_memory(kwargs.get("route_context") or {}),
    }


def _persist_profile_updates(workspace_id: str, db: Session, profile_updates: dict) -> None:
    update_brain = _repo_call("update_brain")
    if callable(update_brain) and profile_updates:
        update_brain(db, workspace_id, profile_updates)


def _persist_legacy_knowledge(workspace_id: str, db: Session, record: MemoryRecordInput) -> None:
    add_knowledge = _repo_call("add_knowledge")
    if not callable(add_knowledge):
        return
    if record.memory_type not in {"semantic_fact", "project_context", "procedural", "preference"}:
        return
    add_knowledge(
        db,
        workspace_id,
        type_="text",
        title=record.title or "Extracted memory",
        content=record.content,
        tags=record.tags or ["memory"],
    )


def _persist_memory_records(workspace_id: str, db: Session, records: list[MemoryRecordInput]) -> int:
    upsert_memory_record = _repo_call("upsert_memory_record")
    get_memory_embedding = _repo_call("get_memory_embedding")
    upsert_memory_embedding = _repo_call("upsert_memory_embedding")
    embedding_service = get_embedding_service()
    persisted = 0

    for record in records:
        if record.memory_type not in LONG_TERM_MEMORY_TYPES:
            continue
        stored_record = None
        if callable(upsert_memory_record):
            stored_record = upsert_memory_record(
                db,
                workspace_id,
                memory_type=record.memory_type,
                title=record.title,
                content=record.content,
                summary=record.summary,
                source_kind=record.source_kind,
                source_reference_id=record.source_reference_id,
                tags=record.tags,
                entity_tags=record.entity_tags,
                tool_tags=record.tool_tags,
                importance_score=record.importance_score,
                confidence_score=record.confidence_score,
                pinned=record.pinned,
                canonical_key=record.canonical_key,
                metadata_json=record.metadata_json,
            )
            persisted += 1
        _persist_legacy_knowledge(workspace_id, db, record)

        if (
            stored_record is None
            or record.memory_type not in EMBEDDABLE_MEMORY_TYPES
            or not callable(upsert_memory_embedding)
            or not callable(get_memory_embedding)
        ):
            continue

        source_text = (record.summary or record.content or "").strip()
        if not source_text:
            continue
        model_name = embedding_service.model_name
        content_hash = embedding_content_hash(source_text, model_name=model_name)
        existing_embedding = get_memory_embedding(db, stored_record.id, model_name=model_name)
        if existing_embedding and getattr(existing_embedding, "content_hash", "") == content_hash:
            continue
        vector = embedding_service.embed_text(source_text)
        if not vector:
            continue
        upsert_memory_embedding(
            db,
            workspace_id,
            stored_record.id,
            model_name=model_name,
            content_hash=content_hash,
            vector_json=vector,
        )
    return persisted


def _persist_working_memory(workspace_id: str, db: Session, update: WorkingMemoryUpdate) -> None:
    upsert_working_memory = _repo_call("upsert_working_memory")
    if not callable(upsert_working_memory):
        return
    payload = update.to_dict()
    if not any(payload.values()):
        return
    upsert_working_memory(db, workspace_id, **payload)


def extract_and_save(workspace_id: str, content: str, db: Session, **kwargs):
    """
    Runs selective memory extraction and saves durable memory back into BrainAI.

    Compatibility:
      - ``content`` remains the legacy assistant-output argument.
      - Optional kwargs may provide richer, safe context:
        ``user_input``, ``assistant_output``, ``workflow_name``, ``workflow_steps``,
        ``tool_used``, ``tool_output``, ``agent_key``, ``route_context``.
    """
    bundle = _build_extraction_bundle(workspace_id, content, **kwargs)
    log_event(
        logger,
        logging.INFO,
        "memory.extract.start",
        workspace_id=workspace_id,
        content_length=len(content or ""),
        source_kind=bundle["source_kind"],
        has_workflow=bool(bundle["workflow_name"]),
        has_tool_summary=bool(bundle["tool_summary"]),
    )

    try:
        if not _is_memory_worthy(bundle):
            log_event(
                logger,
                logging.INFO,
                "memory.extract.skip",
                workspace_id=workspace_id,
                reason="not_memory_worthy",
            )
            return

        llm_records: list[MemoryRecordInput] = []
        profile_updates: dict = {}
        working_update = WorkingMemoryUpdate()

        extraction_payload = {
            "user_input": bundle["user_input"],
            "assistant_output": bundle["assistant_output"],
            "workflow_name": bundle["workflow_name"],
            "workflow_summary": bundle["workflow_summary"],
            "tool_summary": bundle["tool_summary"],
            "agent_key": bundle["agent_key"],
        }
        if _should_call_llm(bundle):
            raw = generate_json(EXTRACTION_PROMPT.format(content=json.dumps(extraction_payload, indent=2)))
            llm_records, profile_updates, working_update = _normalize_llm_response(json.loads(raw), bundle)

        heuristic_records = list(bundle["tool_records"])
        if bundle["workflow_summary"]:
            heuristic_records.append(
                MemoryRecordInput(
                    memory_type="workflow_summary",
                    title=f"{bundle['workflow_name']} workflow summary",
                    content=bundle["workflow_summary"],
                    summary=bundle["workflow_summary"],
                    source_kind="workflow_run",
                    source_reference_id=bundle["workflow_name"],
                    tags=["workflow"],
                    importance_score=0.65,
                    confidence_score=0.9,
                    canonical_key=f"workflow_summary:{_slugify(bundle['workflow_name'])}" if bundle["workflow_name"] else "",
                    metadata_json=bundle["workflow_meta"],
                )
            )
            if not working_update.latest_workflow_summary:
                working_update.latest_workflow_summary = bundle["workflow_summary"]

        if bundle["tool_summary"] and not working_update.recent_tool_summary:
            working_update.recent_tool_summary = bundle["tool_summary"]
        if bundle["user_input"] and not working_update.current_goal:
            working_update.current_goal = sanitize_text_for_memory(bundle["user_input"], max_length=240)

        combined_records: list[MemoryRecordInput] = []
        seen_keys: set[str] = set()
        for record in [*heuristic_records, *llm_records]:
            key = record.canonical_key or _canonical_key(record.memory_type, record.title, record.summary or record.content)
            record.canonical_key = key
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            combined_records.append(record)

        _persist_profile_updates(workspace_id, db, profile_updates)
        persisted_count = _persist_memory_records(workspace_id, db, combined_records)
        _persist_working_memory(workspace_id, db, working_update)

        log_event(
            logger,
            logging.INFO,
            "memory.extract.finish",
            workspace_id=workspace_id,
            memory_record_count=persisted_count,
            profile_update_count=len(profile_updates),
            working_memory_updated=bool(working_update.to_dict()),
        )
    except Exception as exc:
        log_exception(
            logger,
            "memory.extract.failed",
            exc,
            workspace_id=workspace_id,
        )
        pass
