"""
ui/sidebar.py  —  Workspace selector + navigation sidebar
"""
import streamlit as st
from storage.db import SessionLocal
from workspace.manager import list_workspaces, create_workspace, get_workspace_context


def render_sidebar():
    with st.sidebar:
        st.markdown("## 🧠 Sintra Clone")
        st.markdown("---")

        db = SessionLocal()
        try:
            workspaces = list_workspaces(db)

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
                    st.rerun()
            else:
                st.info("No workspaces yet.")

            # ── Create new workspace ──────────────────────────────────────────
            with st.expander("➕ New Workspace"):
                new_name = st.text_input("Workspace name", key="new_ws_name")
                if st.button("Create", key="create_ws_btn"):
                    if new_name.strip():
                        ws = create_workspace(new_name.strip(), db)
                        st.session_state.workspace_id   = ws.id
                        st.session_state.workspace_name = ws.name
                        st.session_state.chat_history   = []
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

        finally:
            db.close()
