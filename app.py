"""
app.py  —  Streamlit entry point
Run with: streamlit run app.py
"""
import streamlit as st
from storage.db import init_db

# Must be first Streamlit call
st.set_page_config(
    page_title="Sintra Clone",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Init DB on startup
init_db()

# ── Session defaults ──────────────────────────────────────────────────────────
if "workspace_id" not in st.session_state:
    st.session_state.workspace_id = None
if "workspace_name" not in st.session_state:
    st.session_state.workspace_name = None
if "page" not in st.session_state:
    st.session_state.page = "chat"
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "selected_agent" not in st.session_state:
    st.session_state.selected_agent = None

# ── Sidebar navigation ────────────────────────────────────────────────────────
from ui.sidebar import render_sidebar
from ui.pages.chat_page import render_chat
from ui.pages.brain_page import render_brain
from ui.pages.helpers_page import render_helpers
from ui.pages.ideas_page import render_ideas
from ui.pages.workflows_page import render_workflows
from ui.pages.onboarding_page import render_onboarding

render_sidebar()

# ── Route to page ─────────────────────────────────────────────────────────────
if not st.session_state.workspace_id:
    render_onboarding()
else:
    page = st.session_state.page
    if page == "chat":
        render_chat()
    elif page == "brain":
        render_brain()
    elif page == "helpers":
        render_helpers()
    elif page == "ideas":
        render_ideas()
    elif page == "workflows":
        render_workflows()
    else:
        render_chat()
