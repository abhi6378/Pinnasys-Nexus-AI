# Memory Architecture

This repository now uses a three-layer memory design that preserves the existing conversation archive and `BrainAI` public API while adding selective working memory and distilled long-term memory.

## Layers

### 1. Transcript Archive

- Stored in `conversations`
- Historical, audit, and debug storage
- Not the primary retrieval source for prompts

### 2. Working Memory

- Stored in `working_memory_states`
- Compact current-state snapshot per workspace
- Holds:
  - current goal
  - active tasks
  - open questions
  - current draft summary
  - recent verified tool summary
  - latest workflow summary
  - project focus

### 3. Long-Term Memory

- Stored in `memory_records`
- Distilled reusable memory only
- Supported memory types:
  - `profile`
  - `preference`
  - `semantic_fact`
  - `episodic`
  - `procedural`
  - `workflow_summary`
  - `tool_outcome`
  - `project_context`

Optional semantic vectors are stored in `memory_embeddings`.

## Ingestion Flow

Entry point remains:

- `brain.memory_extractor.extract_and_save(workspace_id, content, db, **kwargs)`

Legacy callers can keep using the original 3-argument signature.

When richer context is available, the extractor can also ingest:

- `user_input`
- `assistant_output`
- `workflow_name`
- `workflow_steps`
- `tool_used`
- `tool_output`
- `agent_key`
- `route_context`

Pipeline:

1. Build a safe ingestion bundle
2. Sanitize tool and text payloads before any persistence
3. Skip obviously non-memory-worthy content
4. Derive safe tool/workflow summaries heuristically
5. Use one best-effort LLM extraction call only when content is rich enough
6. Merge/dedupe memory records by canonical key
7. Update profile, long-term memory, and working memory
8. Generate embeddings only for retained distilled memory when available

## Retrieval Flow

`BrainAI.get_relevant_context(query, limit=6)` is still the public method used by the handler.

Internally retrieval now combines:

- structured profile data
- working memory
- pinned long-term memory
- lexical memory matches
- semantic memory matches when embeddings are available
- recent/important long-term memory
- small legacy knowledge fallback

Results are reranked using:

- pinned status
- lexical relevance
- semantic similarity
- importance score
- confidence score
- recency
- memory-type boosts

The final output is a compact prompt-safe memory pack, not a raw record dump.

## Embeddings and Fallback

Embeddings are optional.

- Provider abstraction: `brain/embedding_service.py`
- Default model env var: `OPENAI_EMBEDDING_MODEL`
- Default value: `text-embedding-3-small`

Important rules:

- raw transcripts are not embedded by default
- only retained distilled memories are considered for embedding
- unchanged memory content is not re-embedded

If embeddings are unavailable:

- the app still works normally
- retrieval falls back to lexical/tag/entity matching plus recency and importance
- no request path fails because embeddings are missing

No pgvector setup is required for the current fallback-safe implementation.

## Sanitization Rules

Tool-derived memory is sanitized before persistence.

Never store:

- API keys
- tokens
- OAuth payloads
- auth headers
- cookie/session material
- credential blobs
- raw sensitive Composio configuration

May store safe summaries such as:

- verified tool outcome summaries
- stable user action preferences
- workflow blockers that are safe to remember
- non-sensitive connected-system observations

Sanitization logic lives in `brain/memory_sanitizer.py`.

## Cost Controls

- memory extraction is selective
- trivial outputs are skipped
- only distilled memory is embedded
- embeddings are skipped when unchanged
- large content is capped and summarized
- memory extraction remains best-effort and non-fatal
- transcript replay is not the main retrieval strategy

## Compatibility

Preserved APIs:

- `BrainAI.get_profile()`
- `BrainAI.update_profile()`
- `BrainAI.get_relevant_context(query, limit=6)`
- `BrainAI.save_to_knowledge(...)`
- `BrainAI.get_missing_fields()`
- `extract_and_save(workspace_id, content, db)`

Existing archive/profile/knowledge flows remain in place and the new memory layer is additive rather than destructive.
