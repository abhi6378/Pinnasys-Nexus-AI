"""
app.py  —  Streamlit entry point
Run with: streamlit run app.py
"""
import streamlit as st
from storage.db import init_db, SessionLocal
from storage import repositories as repo
from helpers.configs import AGENTS
from ui.connector_state import default_connector_context
from tools.connector_service import load_persisted_connector_context
from ui.auth_state import (
    ensure_auth_state,
    get_state_membership_id,
    get_state_user_id,
    render_auth_gate,
    resolve_streamlit_user,
)
from utils.logging_utils import configure_logging

# Must be first Streamlit call
st.set_page_config(
    page_title="Nexus Ai",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

configure_logging()

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
if "pending_tool_retry" not in st.session_state:
    st.session_state.pending_tool_retry = None
if "connector_context" not in st.session_state:
    st.session_state.connector_context = default_connector_context()
if "connector_context_workspace_id" not in st.session_state:
    st.session_state.connector_context_workspace_id = None
ensure_auth_state(st.session_state)

_auth_db = SessionLocal()
try:
    _auth_user = resolve_streamlit_user(_auth_db, st.session_state)
    if st.session_state.auth_required and not _auth_user:
        render_auth_gate(st.session_state)
        st.stop()
    if _auth_user and st.session_state.workspace_id:
        if not repo.get_workspace_membership(_auth_db, st.session_state.workspace_id, _auth_user.id):
            st.session_state.workspace_id = None
            st.session_state.workspace_name = None
            st.session_state.chat_history = []
            st.session_state.connector_context = default_connector_context()
            st.session_state.connector_context_workspace_id = None
    if not st.session_state.workspace_id:
        if _auth_user:
            visible_workspaces = repo.list_workspaces_for_user(_auth_db, _auth_user.id)
        else:
            visible_workspaces = repo.list_workspaces(_auth_db)
        if visible_workspaces:
            first_workspace = visible_workspaces[0]
            st.session_state.workspace_id = first_workspace.id
            st.session_state.workspace_name = first_workspace.name
finally:
    _auth_db.close()

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

if (
    st.session_state.workspace_id
    and st.session_state.connector_context_workspace_id != st.session_state.workspace_id
):
    _cdb = SessionLocal()
    try:
        st.session_state.connector_context = load_persisted_connector_context(
            st.session_state.workspace_id,
            _cdb,
            user_id=get_state_user_id(st.session_state),
            membership_id=get_state_membership_id(_cdb, st.session_state, st.session_state.workspace_id),
        ).to_dict()
        st.session_state.connector_context_workspace_id = st.session_state.workspace_id
    finally:
        _cdb.close()

# ── Sidebar navigation ────────────────────────────────────────────────────────
from ui.sidebar import render_sidebar
from ui.pages.chat_page import render_chat
from ui.pages.brain_page import render_brain
from ui.pages.helpers_page import render_helpers
from ui.pages.ideas_page import render_ideas
from ui.pages.workflows_page import render_workflows
from ui.pages.automations_page import render_automations
from ui.pages.onboarding_page import render_onboarding

render_sidebar(auth_user=_auth_user)

# ── Route to page ─────────────────────────────────────────────────────────────
if not st.session_state.workspace_id:
    render_onboarding(auth_user=_auth_user)
else:
    page = st.session_state.page
    if page == "chat":
        render_chat(auth_user=_auth_user)
    elif page == "brain":
        render_brain()
    elif page == "helpers":
        render_helpers()
    elif page == "ideas":
        render_ideas()
    elif page == "workflows":
        render_workflows()
    elif page == "automations":
        render_automations(auth_user=_auth_user)
    else:
        render_chat()
