# Selective Async And Latency Boundary

This repo intentionally does not use a blanket async rewrite.

## Stays Synchronous

- SQLAlchemy repositories and transaction-sensitive control-plane writes.
- `handle_request()`, workflows, approval/idempotency resume, and tool execution
  orchestration.
- Scheduler scans and worker run claiming.

These paths share one sync SQLAlchemy session and depend on ordered writes for
approval, idempotency, pending requests, workflow history, and automation runs.

## Async / Offload Boundary

External SDK calls are isolated behind `tools.composio_client` async wrappers:

- `async_list_connected_accounts`
- `async_get_connect_link`
- `async_get_tool_schemas`
- `async_validate_tool_slug`
- `async_execute_tool`

The wrappers offload blocking SDK calls to a thread and never receive a DB
session. Callers apply DB updates synchronously after the remote result returns.

## Latency Improvements

- Connector rendering uses a request/render cache so repeated connector cards
  share local/account status work.
- Connect-link auth config lookup, tool schema fetches, and catalog validation
  use process-local TTL caches.
- Agent tool planning reuses prompt schema lookups within a single request.
- API hot routes, scheduler scans, and worker runs log `duration_ms`.
- Connector refresh logs local hit/miss, stale decisions, remote refresh timing,
  account counts, and revocation counts.

## Deferred

Future work can add bounded worker concurrency and async-native provider clients.
Async SQLAlchemy is intentionally deferred until there is a clear migration plan
for session lifetime and transaction ownership.
