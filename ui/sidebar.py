"""
ui/sidebar.py  —  Workspace selector + navigation sidebar
"""
import streamlit as st
from storage.db import SessionLocal
from storage import repositories as repo
from tools.connector_service import list_workspace_connectors, persist_connector_context, refresh_connector_status
from ui.auth_state import get_state_membership_id, get_state_user_id, is_auth_required, logout_streamlit_session
from ui.connector_state import default_connector_context, ensure_connector_state, set_connector_selection
from workspace.manager import list_workspaces, create_workspace, get_workspace_context


def _workspace_options(db):
    if is_auth_required():
        user_id = get_state_user_id(st.session_state)
        if not user_id:
            return []
        return [
            {"id": ws.id, "name": ws.name, "created_at": str(getattr(ws, "created_at", "") or "")}
            for ws in repo.list_workspaces_for_user(db, user_id)
        ]
    return list_workspaces(db)


def _connector_scope_kwargs(db, workspace_id: str) -> dict:
    user_id = get_state_user_id(st.session_state)
    membership_id = get_state_membership_id(db, st.session_state, workspace_id) if user_id else None
    if membership_id:
        return {
            "scope_type": "membership",
            "user_id": user_id,
            "membership_id": membership_id,
            "selected_by_user_id": user_id,
        }
    if user_id:
        return {"scope_type": "user", "user_id": user_id, "selected_by_user_id": user_id}
    return {}


def render_sidebar(auth_user=None):
    ensure_connector_state(st.session_state)
    with st.sidebar:
        st.markdown("## 🧠 Sintra Clone")
        if auth_user:
            st.caption(f"Signed in as {auth_user.email or auth_user.display_name or auth_user.id}")
            if st.button("Sign out", use_container_width=True):
                logout_streamlit_session(st.session_state)
                st.rerun()
        st.markdown("---")

        db = SessionLocal()
        try:
            workspaces = _workspace_options(db)

            # ── Workspace selector ────────────────────────────────────────────
            st.markdown("### 🏢 Workspace")

            if workspaces:
                ws_names = [f"{w['name']}" for w in workspaces]
                ws_ids   = [w["id"] for w in workspaces]

                current_idx = 0
                if st.session_state.workspace_id in ws_ids:
                    current_idx = ws_ids.index(st.session_state.workspace_id)

                selected = st.selectbox(
                    "Select workspace",
                    options=range(len(ws_names)),
                    format_func=lambda i: ws_names[i],
                    index=current_idx,
                    label_visibility="collapsed"
                )
                chosen_id = ws_ids[selected]
                if chosen_id != st.session_state.workspace_id:
                    st.session_state.workspace_id   = chosen_id
                    st.session_state.workspace_name = ws_names[selected]
                    st.session_state.chat_history   = []
                    st.session_state.connector_context = default_connector_context()
                    st.session_state.connector_context_workspace_id = None
                    st.rerun()
            else:
                st.info("No workspaces yet.")

            # ── Create new workspace ──────────────────────────────────────────
            with st.expander("➕ New Workspace"):
                new_name = st.text_input("Workspace name", key="new_ws_name")
                if st.button("Create", key="create_ws_btn"):
                    if new_name.strip():
                        owner_user_id = getattr(auth_user, "id", None) if auth_user else None
                        if is_auth_required() and not owner_user_id:
                            st.warning("Sign in before creating a workspace.")
                            st.stop()
                        ws = create_workspace(new_name.strip(), db, owner_user_id=owner_user_id)
                        st.session_state.workspace_id   = ws.id
                        st.session_state.workspace_name = ws.name
                        st.session_state.chat_history   = []
                        st.session_state.connector_context = default_connector_context()
                        st.session_state.connector_context_workspace_id = None
                        st.success(f"Created: {ws.name}")
                        st.rerun()
                    else:
                        st.warning("Enter a workspace name.")

            st.markdown("---")

            # ── Navigation ────────────────────────────────────────────────────
            if st.session_state.workspace_id:
                ctx = get_workspace_context(st.session_state.workspace_id, db)

                st.markdown("### 🗂️ Navigation")

                pages = [
                    ("💬 Chat",         "chat"),
                    ("🧠 Brain AI",     "brain"),
                    ("🤖 Helpers",      "helpers"),
                    ("💡 Ideas Inbox",  "ideas"),
                    ("⚙️ Workflows",    "workflows"),
                    ("⏱️ Automations", "automations"),
                ]

                for label, key in pages:
                    badge = ""
                    if key == "ideas" and ctx and ctx.idea_count > 0:
                        badge = f" 🔴 {ctx.idea_count}"
                    active = "→ " if st.session_state.page == key else "   "
                    if st.button(f"{active}{label}{badge}", key=f"nav_{key}",
                                 use_container_width=True):
                        st.session_state.page = key
                        st.rerun()

                st.markdown("---")

                # ── Workspace stats ───────────────────────────────────────────
                if ctx:
                    st.markdown("### 📊 Stats")
                    col1, col2 = st.columns(2)
                    col1.metric("Knowledge", ctx.knowledge_count)
                    col2.metric("Chats", ctx.conversation_count)
                    st.metric("Pending Ideas", ctx.idea_count)

                st.markdown("---")
                st.markdown("### 🔌 Connectors")
                connector_rows = list_workspace_connectors(
                    st.session_state.workspace_id,
                    db,
                    selected_toolkit=str(st.session_state.connector_context.get("selected_toolkit", "") or ""),
                    include_connect_url=True,
                )
                current_connector = st.session_state.connector_context
                current_toolkit = str(current_connector.get("selected_toolkit", "") or "")
                current_account_id = str(current_connector.get("selected_account_id", "") or "")
                connector_scope = _connector_scope_kwargs(db, st.session_state.workspace_id)

                if st.button(
                    "Auto Mode",
                    use_container_width=True,
                    type="primary" if not current_toolkit else "secondary",
                    key="sidebar_connector_auto",
                ):
                    set_connector_selection(st.session_state, mode="auto", source="sidebar")
                    persist_connector_context(
                        st.session_state.workspace_id,
                        st.session_state.connector_context,
                        db,
                        **connector_scope,
                    )
                    st.rerun()

                for connector in connector_rows:
                    toolkit = connector["toolkit"]
                    is_active = toolkit == current_toolkit
                    state_badge = "Connected" if connector["connected"] else "Not connected"
                    if st.button(
                        f"{connector['label']} · {state_badge}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                        key=f"sidebar_connector_{toolkit}",
                    ):
                        first_account = connector["accounts"][0] if connector["accounts"] else {}
                        set_connector_selection(
                            st.session_state,
                            mode="manual",
                            selected_toolkit=toolkit,
                            selected_account_id=str(first_account.get("connected_account_id", "") or ""),
                            selected_account_alias=str(first_account.get("account_alias", "") or ""),
                            source="sidebar",
                        )
                        persist_connector_context(
                            st.session_state.workspace_id,
                            st.session_state.connector_context,
                            db,
                            **connector_scope,
                        )
                        st.rerun()

                    if is_active and connector["accounts"]:
                        options = connector["accounts"]
                        labels = [account["account_alias"] for account in options]
                        index = 0
                        for idx, account in enumerate(options):
                            if account["connected_account_id"] == current_account_id:
                                index = idx
                                break
                        selected_index = st.selectbox(
                            f"{connector['label']} account",
                            options=range(len(options)),
                            format_func=lambda i: labels[i],
                            index=index,
                            key=f"sidebar_account_{toolkit}",
                        )
                        selected_account = options[selected_index]
                        if selected_account["connected_account_id"] != current_account_id:
                            set_connector_selection(
                                st.session_state,
                                mode="manual",
                                selected_toolkit=toolkit,
                                selected_account_id=selected_account["connected_account_id"],
                                selected_account_alias=selected_account["account_alias"],
                                source="sidebar",
                            )
                            persist_connector_context(
                                st.session_state.workspace_id,
                                st.session_state.connector_context,
                                db,
                                **connector_scope,
                            )
                            st.rerun()
                        if st.button(
                            f"Refresh {connector['label']}",
                            key=f"sidebar_refresh_{toolkit}",
                            use_container_width=True,
                        ):
                            refreshed = refresh_connector_status(st.session_state.workspace_id, toolkit, db, request_cache={})
                            set_connector_selection(
                                st.session_state,
                                mode="manual",
                                selected_toolkit=toolkit,
                                selected_account_id=str(refreshed.effective_account_id or current_account_id or ""),
                                selected_account_alias=str(refreshed.effective_account_alias or ""),
                                source="sidebar",
                            )
                            persist_connector_context(
                                st.session_state.workspace_id,
                                st.session_state.connector_context,
                                db,
                                **connector_scope,
                            )
                            st.rerun()
                    elif is_active and connector["connect_url"]:
                        st.link_button(
                            f"Connect {connector['label']}",
                            connector["connect_url"],
                            use_container_width=True,
                        )

        finally:
            db.close()
