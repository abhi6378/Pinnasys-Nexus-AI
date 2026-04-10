"""
ui/pages/onboarding_page.py  —  First-run workspace creation screen
"""
import streamlit as st
from storage.db import SessionLocal
from workspace.manager import create_workspace


def render_onboarding():
    st.markdown("# 🧠 Welcome to Sintra Clone")
    st.markdown("#### Your AI workforce platform — 12 agents, one workspace.")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🏢 Create your first Workspace")
        st.markdown("A workspace is your isolated AI environment with its own Brain AI, agents, and knowledge base.")

        ws_name = st.text_input("Business or project name", placeholder="e.g. My Startup, Client X, Personal Brand")

        if st.button("🚀 Create Workspace & Get Started", use_container_width=True, type="primary"):
            if ws_name.strip():
                db = SessionLocal()
                try:
                    ws = create_workspace(ws_name.strip(), db)
                    st.session_state.workspace_id   = ws.id
                    st.session_state.workspace_name = ws.name
                    st.session_state.page           = "brain"
                    st.success(f"✅ Workspace '{ws.name}' created!")
                    st.rerun()
                finally:
                    db.close()
            else:
                st.warning("Please enter a name for your workspace.")

        st.markdown("---")
        st.markdown("""
        **What you get:**
        - 🤖 **12 AI Helpers** — copywriter, SEO, sales, support, social media & more
        - 🧠 **Brain AI** — shared memory that all helpers use
        - ⚙️ **Workflows** — multi-step chained agent tasks
        - 💡 **Ideas Inbox** — agents proactively suggest opportunities
        - 📚 **Knowledge Base** — upload context your agents remember
        """)
