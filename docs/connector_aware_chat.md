# Connector-Aware Chat

This repository now supports both:

- `Auto` chat execution, which preserves the existing capability-first routing behavior.
- Manual connector scoping, where chat can be constrained to a toolkit and optionally to a connected account.

Connector selection is now a first-class runtime constraint, not just a UI hint.

## UX Model

- The chat page keeps `Auto` as the default mode.
- A compact selector above the chat input lets the user choose `Auto` or a connector such as Gmail, HubSpot, Slack, GitHub, or Sheets.
- When a connector is selected, the chat UI shows account selection if multiple accounts are available.
- The left sidebar shows a persistent connector panel with connection status, quick connector selection, and account selection.
- If a connector is not connected, both the chat controls and sidebar can surface a connect action.

## Context Model

Chat execution accepts an optional `connector_context`:

```json
{
  "mode": "auto",
  "selected_toolkit": "",
  "selected_connector_key": "",
  "selected_account_alias": "",
  "selected_account_id": "",
  "enforce_toolkit": false,
  "enforce_account": false,
  "source": "chat_input | sidebar | persisted_default | system_inferred"
}
```

Behavior:

- `mode=auto`: no manual connector constraint is applied.
- `mode=manual` + `selected_toolkit`: broker and capability resolution are constrained to that toolkit.
- `selected_account_id`: execution prefers that connected account when checking readiness and executing tools.
- If exactly one account is connected, the backend can auto-select it.
- If multiple accounts exist and a live action needs a specific account, the broker returns a minimal validation error instead of silently picking one.

## Runtime Flow

The connector context flows through the existing architecture:

1. UI or API request builds `connector_context`.
2. `orchestrator.handler.handle_request()` normalizes and validates it.
3. `orchestrator.router.route_request()` receives the context for routing awareness.
4. `helpers.executor.run_agent()` and `_run_with_tools()` pass it into capability planning.
5. `tools.tool_broker.ComposioDirectBroker` treats manual toolkit selection as a strong resolution constraint.
6. `tools.tool_executor.attempt_tool_call()` receives the selected account id and uses it during connection checks and tool execution.
7. Pending auth resumes persist `connector_context` so reconnect flows keep the original execution scope.
8. `handle_request()` and `POST /chat` return normalized `connector_context` plus `connector_status` so the UI can reconcile stale or auto-selected account state.

## Connection and Account Data

Connection state is sourced from:

- `tools/composio_client.py` for live connected-account discovery
- `models/tool_connections.py` and `storage/repositories.py` for local cached connection metadata
- `storage.db.WorkspaceConnectorPreferenceModel` for workspace-scoped last-used connector/account persistence
- `tools/connector_service.py` for normalized connector and account listing

`tools/connector_service.py` is the main backend service for:

- listing workspace connectors
- listing accounts for a connector
- validating manual connector/account selection
- normalizing connector context
- persisting and hydrating workspace defaults
- applying local-first cache rules and lazy remote refresh

## Persistence Scope

- Connector and account selection can be persisted per workspace.
- Streamlit session state hydrates from the stored workspace preference on load.
- Switching workspaces clears the in-memory selection first, then hydrates the target workspace preference if one exists.
- Auto mode remains the fallback when no saved manual preference exists.

## Local Cache vs Remote Verification

- The UI and most preflight logic use local `tool_connections` data first.
- Each cached connection can carry freshness metadata such as `last_verified_at` and `status_updated_at`.
- Remote Composio account discovery is used only when needed:
  - explicit refresh
  - cache miss
  - stale cache beyond TTL
  - selected account missing from local cache
  - execution retry after auth
- Connect URLs are generated lazily for the selected toolkit instead of every toolkit on every render.
- Broker resolution no longer performs redundant remote connection checks; the executor is the final verification point before a live tool call.

## API Surface

The FastAPI layer exposes:

- `POST /chat` with optional `connector_context`
- `GET /workspace/{workspace_id}/connectors`
- `GET /workspace/{workspace_id}/connectors/{toolkit}/accounts`
- `GET /workspace/{workspace_id}/connectors/{toolkit}/connect-link`

`POST /chat` responses can include:

- `connector_context`: normalized effective connector scope
- `connector_status`: resolved status, account availability, and reconnect hints

Older callers that do not send `connector_context` remain fully compatible.

## UI Files

Primary files for this feature:

- `ui/pages/chat_page.py`
- `ui/sidebar.py`
- `ui/connector_state.py`
- `tools/connector_service.py`
- `api/routes.py`

## Adding a New Connector

For most new connectors, do not add custom chat UI code.

Typical onboarding path:

1. Add toolkit metadata in `tools/tool_registry.py`.
2. Add tool registry entries and capability metadata for the toolkit.
3. Add aliases in toolkit metadata if the connector has user-facing label variants.
4. Ensure `tools/composio_client.py` can discover connection state/accounts for that toolkit.
5. If needed, add policy overlay metadata for aliases, approval, or auth details.

The UI selector and sidebar render from connector metadata and account listings, so new connectors should appear automatically once the toolkit metadata and connection discovery are available.

## Compatibility Notes

- Auto mode remains the default everywhere.
- Existing chat behavior still works when no connector is selected.
- Existing workflows, routing, broker execution, and memory flows are preserved.
- Connector selection is reset on workspace changes to avoid confusing cross-workspace sticky state.
- No secrets, OAuth payloads, API keys, or raw Composio configs are surfaced through connector APIs, logs, or memory.
