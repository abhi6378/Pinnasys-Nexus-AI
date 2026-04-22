# Backend Ownership And Connector Scope

FastAPI is the production boundary for a future React frontend. Streamlit remains
a local/dev compatibility shell; this pass intentionally does not add Streamlit
Google login UI.

## Ownership Boundaries

- `users` represent verified application users, currently created through
  backend-verified Google sign-in.
- `workspaces` remain the data-isolation boundary and the current Composio
  entity/cache key.
- `workspace_memberships` authorize a user inside a workspace and carry the
  role used by API responses.
- Runtime control-plane rows can now record both `actor_user_id` and
  `membership_id` for conversations, workflow runs, pending tool requests,
  tool logs, and idempotency rows.
- Auth-disabled local/dev mode may still create workspace-only records with
  nullable actor and membership fields.

## Connector Preferences

Connector OAuth/account cache in `tool_connections` remains workspace-owned for
the current Composio integration. User-specific behavior is modeled as
preference and selected-account state, not as connection ownership.

Effective connector preference precedence is:

1. Explicit request `connector_context`
2. Membership-scoped preference
3. User-scoped preference
4. Workspace default preference
5. Auto mode

The `GET /workspace/{workspace_id}/connector-preference` response includes the
winning scope so React can explain why a connector/account is selected. The
`PUT` endpoint writes membership scope for authenticated workspace members, user
scope when only a user is present, and workspace scope in auth-disabled
compatibility mode.

## Typed Backend Contracts

`connector_context` is accepted through the shared `ConnectorContextRequest`
Pydantic contract and normalized before reaching the runtime. Connector list,
account list, refresh, auth state, and connector preference APIs now return
stable typed shapes suitable for a React client.

## Deferred

- Streamlit Google login UX
- Per-user connector visibility policies
- Moving Composio entity identity away from `workspace_id`
- Admin/member management UI
