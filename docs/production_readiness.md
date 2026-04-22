# Production Readiness Notes

This app treats FastAPI as the production boundary, Streamlit as a local/dev shell, and Alembic as the canonical schema path.

## Startup

- Run `alembic upgrade head` before starting FastAPI, scheduler, or worker services.
- Keep `SINTRA_ALLOW_SCHEMA_BOOTSTRAP=0` in production. `create_all()` bootstrap is only for explicit local development.
- With `SINTRA_AUTH_REQUIRED=1`, Alembic revision drift fails startup instead of warning.

## Secure Auth Config

- Set a strong `SINTRA_SESSION_SECRET` with at least 32 characters.
- Use explicit `SINTRA_ALLOWED_ORIGINS`; wildcard origins are rejected with auth/cookies unless `SINTRA_ALLOW_INSECURE_DEV_AUTH=1`.
- Keep `SINTRA_SESSION_COOKIE_SECURE=true` outside local HTTP development.
- Browser Google sign-in should use strict CSRF with `SINTRA_STRICT_GOOGLE_CSRF=1`; bearer/API clients retain backward-compatible absent-CSRF behavior.

## Compatibility Shims

- Canonical production paths no longer silently use reflected-table fallbacks for conversations, workflow runs, pending requests, connector preferences, or idempotency records.
- Auth-disabled local/dev mode can still use legacy schema fallbacks unless explicitly tightened.
- Workspace owner compatibility shims remain to reduce local upgrade pain.

## Connector And Tool Runtime

- Connector status is local-first for speed, with selected-connector remote refresh and explicit stale/revoked status reasons.
- Runtime logs include cache hit/miss, refresh decisions, remote refresh duration, account counts, and revocation counts.
- Composio execution uses `connected_account_id` for version resolution by default. The risky SDK bypass is only enabled with `COMPOSIO_ALLOW_VERSION_CHECK_BYPASS=1` and logs a warning event.

## Secret Hygiene

- Durable metadata/results are sanitized before persistence. Raw session tokens, Google credentials, connect URLs, resume tokens, cookies, and OAuth payloads should not be written into history metadata.
- User-facing API responses can still return live `resume_token` values where needed for auth/approval continuation.

## Rollback

This hardening pass is code-only. Roll back by reverting the code changes. No schema/data rewrite is introduced.
