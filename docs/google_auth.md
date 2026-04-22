# Google Authentication

FastAPI is now the production auth boundary. A future React frontend should sign
in through the FastAPI `/auth/*` endpoints and use `/auth/me` as the source of
truth for current user, workspace, membership, and role state. Streamlit remains
a workspace-first local/dev compatibility shell; this pass intentionally does
not productize Streamlit Google login UI.

## Flow

1. The frontend uses Google Identity Services and receives a Google ID token in
   the `credential` field.
2. The frontend posts `{ "credential": "...", "g_csrf_token": "..." }` to
   `POST /auth/google`.
3. FastAPI verifies the token server-side with `google-auth`, checking signature,
   audience, issuer, and expiry.
4. The backend creates or links:
   - `users`
   - `external_identities(provider='google', provider_subject=sub)`
   - an owner `workspace_membership`
   - a default workspace if the user has none
5. The backend creates an `auth_sessions` row and returns a backend-issued session
   token. Browser clients also receive an HTTP-only session cookie.

Only the Google `sub` claim is used as the stable external identity. Email is
stored as profile/contact metadata and is not used as the external identity key.

## Environment

- `GOOGLE_CLIENT_ID`: required for Google credential verification.
- `SINTRA_SESSION_SECRET`: required when `SINTRA_AUTH_REQUIRED=1`; used to hash app sessions.
- `SINTRA_AUTH_REQUIRED`: set to `1` to require auth on workspace-sensitive FastAPI routes.
- `SINTRA_ALLOWED_ORIGINS`: comma-separated CORS origins for credentialed requests.
- `SINTRA_SESSION_COOKIE_SECURE`: set to `true` behind HTTPS.
- `SINTRA_API_BASE_URL`: FastAPI base URL used by Streamlit for logout/help text.
- `GOOGLE_ALLOWED_HOSTED_DOMAIN`: optional hosted-domain restriction.

## Ownership Model

- Workspaces remain the primary isolation boundary for chat, memory, workflows,
  and connector state.
- Users authenticate through Google and access workspaces through active
  `workspace_memberships`.
- Connector preference resolution is explicit: request connector selection wins
  for that request, then membership-scoped preference, then user-scoped
  preference, then workspace default, then Auto mode.
- Streamlit persists connector choices at membership scope when an authenticated
  membership is available, at user scope when only a user is known, and at
  workspace scope in local/dev compatibility mode.
- Tool connections remain workspace-owned for current Composio entity behavior.
  `tool_connections.user_id` is reserved for a real future user id and must not be
  populated with `workspace_id`.

## Streamlit Compatibility Mode

When `SINTRA_AUTH_REQUIRED=0`, Streamlit keeps the legacy local workflow: it can
list all workspaces and create anonymous workspaces. When
`SINTRA_AUTH_REQUIRED=1`, Streamlit fails closed until `st.session_state` has a
valid backend app session token. It stores only the backend-issued app session
token and resolved safe user metadata; it does not store Google credentials,
Google ID tokens, OAuth refresh tokens, or connector secrets.

The production login surface should be the React client using FastAPI auth
contracts. A native Streamlit Google button/custom component is intentionally
deferred and should not become a second production auth boundary.

## Backward Compatibility

When `SINTRA_AUTH_REQUIRED=0`, existing workspace-id API calls and Streamlit local
usage continue to work. Authenticated callers are restricted to workspaces where
they have active membership.

## Deferred

- Google API authorization scopes and refresh-token storage
- user invitation/admin UI
- per-user connector visibility policies
- Streamlit-native Google login UI
