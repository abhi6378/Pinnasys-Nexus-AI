# Database Hardening

This repository now treats PostgreSQL as a production control plane for chat,
tools, connectors, approvals, idempotency, workflows, and memory. Runtime code
should no longer rely on startup-time schema drift fixes.

## Migration Workflow

- Production path: run `alembic upgrade head` before starting the API or Streamlit app.
- Existing legacy databases: stamp the current pre-migration schema first, then upgrade:
  - `alembic stamp 20260417_01`
  - `alembic upgrade head`
- Local/dev fallback: set `SINTRA_ALLOW_SCHEMA_BOOTSTRAP=1` only if you need an explicit
  `create_all()` bootstrap for a disposable environment.
- Normal startup behavior: `init_db()` verifies DB reachability, registers models,
  and warns if the DB is unversioned or behind the Alembic head revision.

## Key Guarantees

- Workspace-owned control-plane tables now declare database ownership through
  `workspace_id -> workspaces.id` foreign keys. The 20260417_03 migration adds
  those constraints as PostgreSQL `NOT VALID` constraints so rollout does not
  block on existing legacy rows, while new writes are protected immediately.
- Idempotency is DB-enforced with a unique key on
  `(workspace_id, tool_name, idempotency_key)`.
- Connector rows are unique per effective runtime identity:
  `(workspace_id, toolkit, connected_account_id)`.
- Only one connected default account is allowed per `(workspace_id, toolkit)`.
- Active memory canonical keys are unique per workspace.
- Memory embeddings are unique per `(memory_record_id, model_name)`.
- Critical control-plane status columns use `TEXT + CHECK` constraints instead of free-form strings.

## Query Hot Paths

The schema now adds indexes for the query patterns used in runtime code:

- Conversations by `workspace_id + created_at`
- Workflow runs by `workspace_id + created_at` and `workspace_id + status + updated_at`
- Memory records by `workspace_id + memory_type + updated_at`
- Memory embeddings by `memory_record_id + model_name`
- Tool connections by `workspace_id + toolkit + status/is_default + updated_at`
- Pending requests by `workspace_id + status + updated_at`
- Tool logs by `workspace_id + status + created_at`
- Idempotency records by their durable uniqueness key

## Connector / Account Storage

- `tool_connections` stores only safe runtime metadata: toolkit, account id, labels,
  freshness timestamps, status, and revocation state.
- Secrets, OAuth payloads, tokens, and raw auth config material must not be stored here.
- `tool_connections.user_id` is nullable and should only contain a real future
  user id. Runtime code must not populate it with `workspace_id` as a pseudo-user.
- `workspace_connector_preferences` remains workspace-default based and
  backward-compatible. It now carries nullable scope fields (`scope_type`,
  `user_id`, `membership_id`, `selected_by_user_id`) so a future auth pass can add
  user or membership overrides without replacing the table.

## Workflow Live-Step Determinism

Workflow steps may declare `requires_live_tool=True`, and steps with
`CapabilityRequest.requires_live_data=True` are treated as requiring a verified
tool result. Those steps cannot be marked successful from free-form text alone.
Text-only workflow steps remain unchanged.

## Auth Readiness

This pass adds forward-looking identity tables without changing current workspace-only runtime behavior:

- `users`
- `external_identities`
- `workspace_memberships`
- nullable `workspaces.owner_user_id`

These tables are scaffolding for future Google sign-in and real user identity. They are
not required by the current request path, connector flow, or workflow runtime yet.
Future Google auth should be server-verified and should map provider identities
through `external_identities` into `users`, then authorize access through
`workspace_memberships`.

## Deferred Work

- Full auth/login implementation
- secret storage / provider token lifecycle management
- archival / retention jobs for high-growth tables
- richer workflow execution-event persistence beyond lightweight request metadata
