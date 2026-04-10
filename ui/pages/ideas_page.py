"""
ui/pages/ideas_page.py  —  Ideas Inbox: accept triggers workflow, reject dismisses
"""
import streamlit as st
from storage.db import SessionLocal
from storage import repositories as repo
from orchestrator.handler import handle_request


def render_ideas():
    ws_id = st.session_state.workspace_id
    st.markdown(f"## 💡 Ideas Inbox — {st.session_state.workspace_name}")
    st.markdown("Your AI helpers proactively surface opportunities here. Accept to trigger a workflow.")
    st.markdown("---")

    db = SessionLocal()
    try:
        filter_status = st.radio(
            "Filter", ["pending", "accepted", "rejected", "all"],
            horizontal=True
        )

        status_filter = None if filter_status == "all" else filter_status
        ideas = repo.get_ideas(db, ws_id, status=status_filter)

        if not ideas:
            st.info("📭 No ideas yet. Start chatting with your helpers — they'll surface opportunities here!")
        else:
            for idea in ideas:
                status_icon = {"pending": "🟡", "accepted": "✅", "rejected": "❌"}.get(idea.status, "⚪")
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"### {status_icon} {idea.title}")
                        st.markdown(idea.description)
                        st.caption(
                            f"From: **{idea.source_agent}** | "
                            f"Status: **{idea.status}** | "
                            f"Workflow hint: `{idea.workflow_hint or 'none'}` | "
                            f"{str(idea.created_at)[:16]}"
                        )

                    with col2:
                        if idea.status == "pending":
                            if st.button("✅ Accept", key=f"accept_{idea.id}", type="primary",
                                         use_container_width=True):
                                repo.update_idea_status(db, idea.id, "accepted")
                                # Trigger linked workflow
                                if idea.workflow_hint and idea.workflow_hint not in ("none", ""):
                                    with st.spinner(f"Running {idea.workflow_hint} workflow..."):
                                        result = handle_request(
                                            idea.description, ws_id, db
                                        )
                                    st.success("✅ Workflow triggered!")
                                    st.session_state.chat_history.append({
                                        "role": "assistant",
                                        "content": result["output"],
                                        "label": f"Workflow: {idea.workflow_hint}",
                                        "icon": "⚙️",
                                        "steps": result.get("steps", []),
                                        "idea": None,
                                    })
                                    st.session_state.page = "chat"
                                    st.rerun()
                                else:
                                    st.rerun()

                            if st.button("❌ Reject", key=f"reject_{idea.id}",
                                         use_container_width=True):
                                repo.update_idea_status(db, idea.id, "rejected")
                                st.rerun()

    finally:
        db.close()
