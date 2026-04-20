from __future__ import annotations

import os
from typing import Any

import requests

from auth.service import AuthenticatedUser, get_current_user_from_token, is_auth_required
from storage import repositories as repo


AUTH_STATE_KEYS = ("auth_user", "auth_token", "auth_checked", "auth_required")


def ensure_auth_state(state: Any) -> None:
    state.setdefault("auth_user", None)
    state.setdefault("auth_token", "")
    state.setdefault("auth_checked", False)
    state["auth_required"] = is_auth_required()


def resolve_streamlit_user(db: Any, state: Any) -> AuthenticatedUser | None:
    ensure_auth_state(state)
    if not is_auth_required():
        state["auth_user"] = None
        state["auth_checked"] = True
        return None

    token = str(state.get("auth_token", "") or "").strip()
    if not token:
        state["auth_user"] = None
        state["auth_checked"] = True
        return None

    user = get_current_user_from_token(db, token)
    state["auth_user"] = user.to_dict() if user else None
    state["auth_checked"] = True
    if not user:
        state["auth_token"] = ""
    return user


def get_state_user(state: Any) -> AuthenticatedUser | None:
    data = state.get("auth_user")
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return AuthenticatedUser(
        id=str(data.get("id", "") or ""),
        email=str(data.get("email", "") or ""),
        display_name=str(data.get("display_name", "") or ""),
        avatar_url=str(data.get("avatar_url", "") or ""),
    )


def get_state_user_id(state: Any) -> str | None:
    user = get_state_user(state)
    return user.id if user else None


def get_state_membership_id(db: Any, state: Any, workspace_id: str) -> str | None:
    user_id = get_state_user_id(state)
    if not user_id or not workspace_id:
        return None
    membership = repo.get_workspace_membership(db, workspace_id, user_id)
    return getattr(membership, "id", None) if membership else None


def list_visible_workspaces(db: Any, state: Any):
    user_id = get_state_user_id(state)
    if is_auth_required() and user_id:
        return repo.list_workspaces_for_user(db, user_id)
    return repo.list_workspaces(db)


def clear_auth_state(state: Any) -> None:
    for key in AUTH_STATE_KEYS:
        if key in state:
            del state[key]
    ensure_auth_state(state)


def logout_streamlit_session(state: Any) -> None:
    token = str(state.get("auth_token", "") or "").strip()
    api_base_url = os.getenv("SINTRA_API_BASE_URL", "").strip().rstrip("/")
    if token and api_base_url:
        try:
            requests.post(
                f"{api_base_url}/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
        except Exception:
            pass
    clear_auth_state(state)
    state["workspace_id"] = None
    state["workspace_name"] = None
    state["chat_history"] = []


def render_auth_gate(state: Any) -> None:
    import streamlit as st

    ensure_auth_state(state)
    st.markdown("# Sign in required")
    st.info(
        "Authentication is enabled for this deployment. Sign in through the "
        "FastAPI Google auth flow, then paste the backend-issued app session "
        "token here to continue in Streamlit."
    )
    token = st.text_input("App session token", type="password", key="streamlit_auth_token_input")
    if st.button("Use session token", type="primary", use_container_width=True):
        if token.strip():
            state["auth_token"] = token.strip()
            state["auth_checked"] = False
            st.rerun()
        st.warning("Paste a valid backend-issued session token.")

    api_base_url = os.getenv("SINTRA_API_BASE_URL", "http://localhost:8000").strip()
    st.caption(
        f"Backend auth endpoint: `{api_base_url.rstrip('/')}/auth/google`. "
        "A native Streamlit Google button is intentionally deferred."
    )
