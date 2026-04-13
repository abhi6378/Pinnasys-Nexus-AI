"""
app.py  —  Streamlit entry point
Run with: streamlit run app.py
"""
import streamlit as st
from storage.db import init_db, SessionLocal
from storage import repositories as repo
from helpers.configs import AGENTS

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

# ── Hydrate chat history from DB on cold start / browser refresh ──────────────
# Runs only when: workspace is known AND chat_history is empty (i.e. page was
# just refreshed). On every subsequent rerun session_state already has messages
# so this block is skipped entirely — zero extra DB calls during normal use.
if st.session_state.workspace_id and not st.session_state.chat_history:
    _hdb = SessionLocal()
    try:
        # get_conversations returns newest-first; reverse for chronological order
        _rows = list(reversed(
            repo.get_conversations(_hdb, st.session_state.workspace_id, limit=50)
        ))
        for _row in _rows:
            # User turn
            st.session_state.chat_history.append({
                "role":    "user",
                "content": _row.input,
            })
            # Assistant turn — resolve icon/name from AGENTS if key is known
            _agent_conf = AGENTS.get(_row.helper, {})
            st.session_state.chat_history.append({
                "role":    "assistant",
                "content": _row.output,
                "label":   _agent_conf.get("name", _row.helper),
                "icon":    _agent_conf.get("icon", "🤖"),
                "steps":   [],   # step traces are not stored in ConversationModel
                "idea":    None, # idea state is managed separately via IdeaModel
            })
    finally:
        _hdb.close()

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
