"""
ui/pages/chat_page.py  —  Main chat interface with agent selector

Mission 7 update:
  - Shows tool execution status indicators (⚡ Using... / ✅ Done).
  - Expandable "View tool details" section for tool-enabled responses.
  - Persists tool_used metadata in chat_history entries.
  - Improved connect_required card with clear visuals.
  - Retry button for connect_required and tool failure cases.
  - Backward compatible: text-only agents / workflows unaffected.
"""
import streamlit as st
from storage.db import SessionLocal
from storage import repositories as repo
from orchestrator.handler import handle_request
from helpers.configs import list_agents, AGENTS
from brain.quiz_engine import get_next_question, save_answer, quiz_progress
from tools.connector_service import (
    list_workspace_connectors,
    persist_connector_context,
    refresh_connector_status,
)
from ui.auth_state import get_state_membership_id, get_state_user_id
from ui.connector_state import build_connector_context, ensure_connector_state, set_connector_selection


# ── Toolkit display helpers ───────────────────────────────────────────────────

TOOLKIT_ICONS = {
    "GMAIL":            ("mail", "Gmail"),
    "GOOGLE_CALENDAR":  ("calendar", "Google Calendar"),
    "SLACK":            ("hash", "Slack"),
    "HUBSPOT":          ("contact", "HubSpot"),
    "GITHUB":           ("code", "GitHub"),
    "GOOGLE_SHEETS":    ("table", "Google Sheets"),
    "TAVILY":           ("globe", "Tavily Search"),
    "TWITTER":          ("share", "X / Twitter"),
    "LINKEDIN":         ("briefcase", "LinkedIn"),
}

# Maps tool_name → (emoji, human label) for execution indicators
TOOL_DISPLAY = {
    "GMAIL_SEND_EMAIL":              ("📧", "Sent email via Gmail"),
    "GMAIL_CREATE_EMAIL_DRAFT":      ("📧", "Drafted email via Gmail"),
    "GMAIL_GET_CONTACTS":            ("📧", "Fetched Gmail contacts"),
    "GMAIL_FETCH_EMAILS":            ("📧", "Listed emails from Gmail"),
    "GOOGLECALENDAR_CREATE_EVENT":   ("📅", "Created calendar event"),
    "GOOGLECALENDAR_EVENTS_LIST":    ("📅", "Listed calendar events"),
    "SLACK_SEND_MESSAGE":            ("💬", "Sent Slack message"),
    "SLACK_FETCH_CONVERSATION_HISTORY": ("💬", "Fetched Slack conversation history"),
    "SLACK_LIST_ALL_CHANNELS":       ("💬", "Listed Slack channels"),
    "HUBSPOT_CREATE_CONTACT":        ("📊", "Created HubSpot contact"),
    "HUBSPOT_LIST_CONTACTS":         ("📊", "Fetched HubSpot contacts"),
    "HUBSPOT_CREATE_DEAL":           ("📊", "Created HubSpot deal"),
    "GOOGLESHEETS_CREATE_SPREADSHEET_ROW": ("📋", "Added row to Google Sheet"),
    "GITHUB_CREATE_AN_ISSUE":        ("💻", "Created GitHub issue"),
    "GITHUB_LIST_REPOSITORY_ISSUES": ("💻", "Listed GitHub issues"),
    "TAVILY_SEARCH":                 ("🌐", "Searched the web via Tavily"),
    "TWITTER_CREATION_OF_A_POST":    ("📝", "Posted on X / Twitter"),
    "TWITTER_RECENT_SEARCH":         ("📝", "Fetched recent X / Twitter posts"),
    "LINKEDIN_GET_MY_INFO":          ("💼", "Fetched LinkedIn profile info"),
    "LINKEDIN_CREATE_LINKED_IN_POST": ("💼", "Published a LinkedIn post"),
}


def _toolkit_display(toolkit_key: str) -> tuple[str, str]:
    """Return (emoji, display_name) for a toolkit."""
    lookup = {
        "GMAIL":           ("📧", "Gmail"),
        "GOOGLE_CALENDAR": ("📅", "Google Calendar"),
        "SLACK":           ("💬", "Slack"),
        "HUBSPOT":         ("📊", "HubSpot"),
        "GITHUB":          ("💻", "GitHub"),
        "GOOGLE_SHEETS":   ("📋", "Google Sheets"),
        "TAVILY":          ("🌐", "Tavily Search"),
        "TWITTER":         ("📝", "X / Twitter"),
        "LINKEDIN":        ("💼", "LinkedIn"),
    }
    return lookup.get(toolkit_key.upper(), ("🔗", toolkit_key.replace("_", " ").title()))


def _tool_display(tool_name: str) -> tuple[str, str]:
    """Return (emoji, human_label) for a tool execution indicator."""
    return TOOL_DISPLAY.get(tool_name, ("⚡", tool_name.replace("_", " ").title()))


# ── Tool execution indicator ─────────────────────────────────────────────────

def _render_tool_indicator(tool_name: str):
    """Render a compact tool execution badge after the message."""
    emoji, label = _tool_display(tool_name)
    st.markdown(
        f"""<div style="
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            margin: 6px 0 2px 0;
            border-radius: 8px;
            background: linear-gradient(135deg, #0d3320 0%, #1a4731 100%);
            border: 1px solid #2d6a4f;
            font-size: 0.82em;
            color: #b7e4c7;
        ">
        ✅ <strong>{label}</strong>
        </div>""",
        unsafe_allow_html=True,
    )


# ── Expandable tool details ──────────────────────────────────────────────────

def _render_tool_details(tool_name: str):
    """Render an expandable section showing tool execution details."""
    emoji, label = _tool_display(tool_name)
    with st.expander(f"🔧 View tool details"):
        st.markdown(f"**Tool:** `{tool_name}`")
        st.markdown(f"**Action:** {label}")
        st.markdown(f"**Status:** ✅ Executed successfully")


# ── Connect-required card ─────────────────────────────────────────────────────

def _render_connect_card(msg: dict, msg_idx: int):
    """Render a connector card for a connect_required message."""
    connect_url   = msg.get("connect_url")
    resume_token  = msg.get("resume_token", "")
    original_input = msg.get("original_input", "")
    toolkit_raw   = msg.get("toolkit", "")

    emoji, toolkit_name = _toolkit_display(toolkit_raw)

    # Connector card
    st.markdown(
        f"""<div style="
            border: 1px solid #444;
            border-radius: 12px;
            padding: 16px 20px;
            margin: 8px 0;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        ">
        <span style="font-size: 1.4em;">{emoji}</span>
        <strong style="font-size: 1.1em; margin-left: 8px;">
            Connect {toolkit_name}
        </strong>
        <p style="margin: 8px 0 0 0; color: #aaa; font-size: 0.9em;">
            A tool from {toolkit_name} is needed to complete this request.
            Click below to connect your account.
        </p>
        </div>""",
        unsafe_allow_html=True,
    )

    col_connect, col_retry = st.columns([1, 1])

    with col_connect:
        if connect_url:
            st.link_button(
                f"{emoji} Connect {toolkit_name}",
                connect_url,
                use_container_width=True,
                type="primary",
            )

    with col_retry:
        if original_input:
            if st.button(
                "🔄 Retry",
                key=f"conn_retry_{msg_idx}",
                use_container_width=True,
            ):
                st.session_state.pending_tool_retry = {
                    "original_input": original_input,
                    "resume_token": resume_token,
                }
                st.rerun()

# ── Auth Unavailable card ─────────────────────────────────────────────────────

def _render_auth_unavailable_card(msg: dict, msg_idx: int):
    """Render a card when an integration cannot connect (e.g. missing API keys)."""
    toolkit_raw = msg.get("toolkit", "")
    emoji, toolkit_name = _toolkit_display(toolkit_raw)
    details = msg.get("auth_error", "").strip() or (
        f"The integration for {toolkit_name} is currently unavailable."
    )

    st.markdown(
        f"""<div style="
            border: 1px solid #662222;
            border-radius: 12px;
            padding: 16px 20px;
            margin: 8px 0;
            background: linear-gradient(135deg, #2a0e0e 0%, #1a0a0a 100%);
        ">
        <span style="font-size: 1.4em;">{emoji} ⚠️</span>
        <strong style="font-size: 1.1em; margin-left: 8px; color: #ff6b6b;">
            Setup Required for {toolkit_name}
        </strong>
        <p style="margin: 8px 0 0 0; color: #ffcccc; font-size: 0.9em;">
            {details}
        </p>
        </div>""",
        unsafe_allow_html=True,
    )


# ── Tool error display ────────────────────────────────────────────────────────

def _render_tool_error(msg: dict, msg_idx: int):
    """Render a friendly tool error message with retry option."""
    original_input = msg.get("original_input", "")

    st.markdown(
        """<div style="
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            margin: 6px 0 2px 0;
            border-radius: 8px;
            background: linear-gradient(135deg, #3d1f1f 0%, #4a2020 100%);
            border: 1px solid #6b3030;
            font-size: 0.82em;
            color: #f5b7b1;
        ">
        ⚠️ <strong>Tool execution failed — request stopped</strong>
        </div>""",
        unsafe_allow_html=True,
    )

    if original_input:
        if st.button(
            "🔄 Retry",
            key=f"error_retry_{msg_idx}",
        ):
            st.session_state.pending_tool_retry = {
                "original_input": original_input,
                "resume_token": "",
            }
            st.rerun()


def _render_invalid_tool_card(msg: dict):
    st.error("Invalid tool configuration or tool name detected.")
    st.markdown(msg.get("content", ""))


def _render_validation_error_card(msg: dict):
    st.warning("The tool request was invalid, so the app stopped before execution.")
    st.markdown(msg.get("content", ""))


def _render_approval_card(msg: dict, msg_idx: int):
    if not msg.get("approval_required"):
        return
    st.info("This live write action is waiting for explicit approval before execution.")
    requirement = msg.get("approval_requirement") or {}
    reason = str(requirement.get("reason", "") or "").strip()
    if reason:
        st.caption(reason)
    if msg.get("resume_token"):
        if st.button("Approve & Run", key=f"approve_retry_{msg_idx}", use_container_width=True):
            st.session_state.pending_tool_approval = {
                "resume_token": msg.get("resume_token", ""),
            }
            st.rerun()


def _render_workflow_state(msg: dict):
    if msg.get("workflow_paused"):
        step_label = msg.get("step_label", "Current step")
        st.info(f"Workflow paused at: **{step_label}**")
    if msg.get("workflow_resumed"):
        st.success("Workflow resumed from the last completed step.")


def _connector_scope_kwargs(workspace_id: str, db) -> dict:
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


def _persist_chat_connector_context(workspace_id: str, db) -> None:
    persist_connector_context(
        workspace_id,
        st.session_state.connector_context,
        db,
        **_connector_scope_kwargs(workspace_id, db),
    )


def _render_connector_controls(workspace_id: str, db) -> dict:
    """Render the chat-scoped connector selector and return the active context."""
    ensure_connector_state(st.session_state)
    current = build_connector_context(st.session_state)
    current_toolkit = str(current.get("selected_toolkit", "") or "")
    current_account_id = str(current.get("selected_account_id", "") or "")
    current_account_alias = str(current.get("selected_account_alias", "") or "")
    connector_rows = list_workspace_connectors(
        workspace_id,
        db,
        selected_toolkit=current_toolkit,
        include_connect_url=True,
    )
    connector_map = {row["toolkit"]: row for row in connector_rows}

    connector_options = ["AUTO"] + [row["toolkit"] for row in connector_rows]
    current_option = current_toolkit if current_toolkit in connector_map else "AUTO"
    selected_connector = st.selectbox(
        "Connector scope",
        options=connector_options,
        index=connector_options.index(current_option),
        format_func=lambda value: (
            "Auto"
            if value == "AUTO"
            else connector_map[value]["label"]
        ),
        key="chat_connector_scope",
    )

    if selected_connector == "AUTO":
        if current_toolkit:
            set_connector_selection(st.session_state, mode="auto", source="chat_input")
            _persist_chat_connector_context(workspace_id, db)
            st.rerun()
        st.caption("Auto keeps the existing capability-first routing behavior.")
        return build_connector_context(st.session_state)

    selected_row = connector_map[selected_connector]
    accounts = selected_row.get("accounts", [])
    if current_toolkit != selected_connector:
        default_account = accounts[0] if len(accounts) == 1 else {}
        set_connector_selection(
            st.session_state,
            mode="manual",
            selected_toolkit=selected_connector,
            selected_account_id=str(default_account.get("connected_account_id", "") or ""),
            selected_account_alias=str(default_account.get("account_alias", "") or ""),
            source="chat_input",
        )
        _persist_chat_connector_context(workspace_id, db)
        st.rerun()

    state_col, account_col = st.columns([1, 1.2])
    with state_col:
        if selected_row.get("connected"):
            status = "Connected"
            if selected_row.get("account_count", 0) > 1:
                status = f"Connected · {selected_row['account_count']} accounts"
            st.success(f"{selected_row['label']} · {status}")
        else:
            st.warning(f"{selected_row['label']} is not connected.")
            if selected_row.get("connect_url"):
                st.link_button(
                    f"Connect {selected_row['label']}",
                    selected_row["connect_url"],
                    use_container_width=True,
                )
        if st.button(
            f"Refresh {selected_row['label']}",
            key=f"chat_refresh_{selected_connector}",
            use_container_width=True,
        ):
            refreshed = refresh_connector_status(workspace_id, selected_connector, db, request_cache={})
            effective_account_id = str(refreshed.effective_account_id or current_account_id or "")
            effective_account_alias = str(refreshed.effective_account_alias or current_account_alias or "")
            set_connector_selection(
                st.session_state,
                mode="manual",
                selected_toolkit=selected_connector,
                selected_account_id=effective_account_id,
                selected_account_alias=effective_account_alias,
                source="chat_input",
            )
            _persist_chat_connector_context(workspace_id, db)
            st.rerun()

    with account_col:
        if accounts:
            account_ids = [str(account.get("connected_account_id", "") or "") for account in accounts]
            if len(accounts) == 1 and not current_account_id:
                only_account = accounts[0]
                set_connector_selection(
                    st.session_state,
                    mode="manual",
                    selected_toolkit=selected_connector,
                    selected_account_id=str(only_account.get("connected_account_id", "") or ""),
                    selected_account_alias=str(only_account.get("account_alias", "") or ""),
                    source="chat_input",
                )
                _persist_chat_connector_context(workspace_id, db)
                st.rerun()
            selected_account = st.selectbox(
                f"{selected_row['label']} account",
                options=account_ids,
                index=account_ids.index(current_account_id) if current_account_id in account_ids else 0,
                format_func=lambda value: next(
                    (
                        str(account.get("account_alias", "") or value)
                        for account in accounts
                        if str(account.get("connected_account_id", "") or "") == value
                    ),
                    value or "Connected account",
                ),
                key=f"chat_connector_account_{selected_connector}",
            )
            if selected_account != current_account_id:
                selected_account_row = next(
                    (
                        account
                        for account in accounts
                        if str(account.get("connected_account_id", "") or "") == selected_account
                    ),
                    {},
                )
                set_connector_selection(
                    st.session_state,
                    mode="manual",
                    selected_toolkit=selected_connector,
                    selected_account_id=selected_account,
                    selected_account_alias=str(selected_account_row.get("account_alias", "") or ""),
                    source="chat_input",
                )
                _persist_chat_connector_context(workspace_id, db)
                st.rerun()
        else:
            st.caption("No connected accounts available yet.")

    selected_label = current_account_alias or current_account_id or "Any connected account"
    st.caption(
        f"Manual mode is active. Execution is constrained to **{selected_row['label']}**"
        f" and account **{selected_label}**."
    )
    return build_connector_context(st.session_state)


# ── Main page render ──────────────────────────────────────────────────────────

def render_chat(auth_user=None):
    ws_id = st.session_state.workspace_id
    actor_user_id = getattr(auth_user, "id", None) if auth_user else None
    ensure_connector_state(st.session_state)
    st.session_state.setdefault("pending_tool_approval", None)

    st.markdown(f"## 💬 Chat — {st.session_state.workspace_name}")

    db = SessionLocal()
    try:
        # ── Brain AI quiz nudge ───────────────────────────────────────────────
        progress = quiz_progress(ws_id, db)
        if not progress["complete"]:
            next_q = get_next_question(ws_id, db)
            if next_q:
                with st.expander(
                    f"🧠 Brain AI Setup — {progress['percent']}% complete "
                    f"({progress['filled']}/{progress['total']} fields)",
                    expanded=progress["percent"] < 30
                ):
                    st.info(f"**{next_q['question']}**")
                    st.caption(f"Category: {next_q['category']}")
                    answer = st.text_input(
                        "Your answer", key=f"quiz_ans_{next_q['field']}",
                        placeholder="Type your answer here..."
                    )
                    if st.button("Save & Continue →", key="quiz_save"):
                        if answer.strip():
                            save_answer(ws_id, next_q["field"], next_q["question"], answer.strip(), db)
                            st.success("✅ Saved to Brain AI!")
                            st.rerun()

        st.markdown("---")

        pending_rows = repo.list_pending_tool_requests(db, ws_id, limit=3)
        if pending_rows:
            latest_pending = pending_rows[0]
            st.info(
                f"Pending tool request detected for **{latest_pending.requested_toolkit}**. "
                f"You can reconnect or resume it from here after returning from auth."
            )
            col_resume, col_reconnect = st.columns([1, 1])
            with col_resume:
                if st.button("Resume Pending Request", use_container_width=True):
                    st.session_state.pending_tool_retry = {
                        "original_input": latest_pending.original_input,
                        "resume_token": latest_pending.resume_token,
                    }
                    st.rerun()
            with col_reconnect:
                try:
                    from tools.composio_client import get_connect_link
                    reconnect_url = get_connect_link(ws_id, latest_pending.requested_toolkit)
                except Exception:
                    reconnect_url = None
                if reconnect_url:
                    st.link_button("Reconnect Account", reconnect_url, use_container_width=True)

        st.markdown("---")

        # ── Agent selector ────────────────────────────────────────────────────
        agents = list_agents()
        col_auto, *agent_cols = st.columns([1.5] + [1] * min(6, len(agents)))

        with col_auto:
            if st.button(
                "🤖 Auto-Route",
                use_container_width=True,
                type="primary" if not st.session_state.selected_agent else "secondary"
            ):
                st.session_state.selected_agent = None
                st.rerun()

        for i, agent in enumerate(agents[:6]):
            with agent_cols[i]:
                is_active = st.session_state.selected_agent == agent["key"]
                if st.button(
                    f"{agent['icon']} {agent['name']}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                    key=f"agent_btn_{agent['key']}"
                ):
                    st.session_state.selected_agent = agent["key"]
                    st.rerun()

        # Second row of agents
        if len(agents) > 6:
            agent_cols2 = st.columns(min(6, len(agents) - 6))
            for i, agent in enumerate(agents[6:12]):
                with agent_cols2[i]:
                    is_active = st.session_state.selected_agent == agent["key"]
                    if st.button(
                        f"{agent['icon']} {agent['name']}",
                        use_container_width=True,
                        type="primary" if is_active else "secondary",
                        key=f"agent_btn2_{agent['key']}"
                    ):
                        st.session_state.selected_agent = agent["key"]
                        st.rerun()

        # Show active agent info
        if st.session_state.selected_agent:
            agent_info = AGENTS.get(st.session_state.selected_agent, {})
            st.caption(f"🎯 Talking to **{agent_info.get('name')}** — {agent_info.get('role')}")
        else:
            st.caption("🤖 **Auto-Route** — Orchestrator picks the best agent or workflow automatically")

        st.markdown("---")

        # ── Chat history ──────────────────────────────────────────────────────
        chat_container = st.container()
        with chat_container:
            for msg_idx, msg in enumerate(st.session_state.chat_history):
                if msg["role"] == "user":
                    with st.chat_message("user"):
                        st.markdown(msg["content"])
                else:
                    icon = msg.get("icon", "🤖")
                    label = msg.get("label", "Assistant")
                    with st.chat_message("assistant"):
                        st.caption(f"{icon} **{label}**")
                        st.markdown(msg["content"])

                        # ── Tool execution indicator ──────────────────────────
                        tool_used = msg.get("tool_used")
                        if tool_used:
                            _render_tool_indicator(tool_used)
                            _render_tool_details(tool_used)

                        # ── Tool error indicator ──────────────────────────────
                        if msg.get("tool_error"):
                            _render_tool_error(msg, msg_idx)

                        if msg.get("invalid_tool"):
                            _render_invalid_tool_card(msg)

                        if msg.get("validation_error"):
                            _render_validation_error_card(msg)
                        if msg.get("approval_required"):
                            _render_approval_card(msg, msg_idx)

                        # ── Connect-required card ─────────────────────────────
                        if msg.get("connect_required"):
                            _render_connect_card(msg, msg_idx)

                        # ── Auth Unavailable card ─────────────────────────────
                        if msg.get("auth_unavailable"):
                            _render_auth_unavailable_card(msg, msg_idx)

                        _render_workflow_state(msg)

                        # Show workflow steps if available
                        # Uses .get() so both live and DB-hydrated entries are safe
                        steps = msg.get("steps") or []
                        if steps:
                            with st.expander("🔍 View workflow steps"):
                                for step in steps:
                                    st.markdown(f"**Step: {step.get('step', '')}** — Agent: `{step.get('agent', '')}`")
                                    st.markdown(step.get("output", "")[:400] + "...")
                                    st.markdown("---")

                        # Idea notification
                        # Uses .get() so DB-hydrated entries (idea=None) don't raise
                        idea = msg.get("idea")
                        if idea:
                            st.info(f"💡 **New Idea:** {idea['title']}\n\n{idea['description']}")
                            if st.button("View in Ideas Inbox →", key=f"goto_ideas_{idea['id']}"):
                                st.session_state.page = "ideas"
                                st.rerun()

        # ── Handle pending retry (from connect_required → Retry button) ───────
        pending_approval = st.session_state.get("pending_tool_approval")
        if pending_approval:
            resume_token = pending_approval.get("resume_token", "")
            st.session_state.pending_tool_approval = None
            if resume_token:
                from tools.tool_executor import (
                    get_pending_request,
                    mark_request_approved,
                    mark_request_resumed,
                    mark_request_completed,
                )

                pending = get_pending_request(db, resume_token)
                if pending:
                    mark_request_approved(db, resume_token)
                    mark_request_resumed(db, resume_token)
                    context = dict(getattr(pending, "context_json", {}) or {})
                    context["approval_granted"] = True
                    approved_keys = list(context.get("approved_idempotency_keys", []) or [])
                    if getattr(pending, "idempotency_key", "") and pending.idempotency_key not in approved_keys:
                        approved_keys.append(pending.idempotency_key)
                    context["approved_idempotency_keys"] = approved_keys
                    workflow_key = context.get("workflow_key")
                    retry_connector_context = context.get("connector_context") or build_connector_context(st.session_state)
                    with st.spinner("✅ Approval recorded. Running action..."):
                        result = handle_request(
                            pending.original_input,
                            ws_id,
                            db,
                            force_agent=pending.agent_key if (pending.agent_key and not workflow_key) else None,
                            force_workflow=workflow_key,
                            resume_state=context,
                            connector_context=retry_connector_context,
                            actor_user_id=actor_user_id,
                        )
                    if result.get("mode") not in {
                        "connect_required", "auth_unavailable", "invalid_tool",
                        "validation_error", "tool_error"
                    } and not result.get("error", False):
                        mark_request_completed(db, resume_token)
                    if result.get("connector_context"):
                        st.session_state.connector_context = result["connector_context"]
                    _append_result_to_history(result, original_input=pending.original_input)
                    st.rerun()

        pending_retry = st.session_state.get("pending_tool_retry")
        if pending_retry:
            retry_input = pending_retry["original_input"]
            resume_token = pending_retry.get("resume_token", "")
            # Clear the retry state immediately so it doesn't re-trigger
            st.session_state.pending_tool_retry = None

            # Invalidate stale Composio session so fresh connection is detected
            from tools.composio_client import invalidate_session
            invalidate_session(ws_id)

            # Add user message
            st.session_state.chat_history.append({
                "role": "user",
                "content": f"🔄 _{retry_input}_"
            })

            # Re-send through orchestrator
            with st.spinner("🔄 Retrying with connected tools..."):
                if resume_token:
                    from tools.tool_executor import (
                        get_pending_request,
                        mark_request_resumed,
                        mark_request_completed,
                    )

                    pending = get_pending_request(db, resume_token)
                    if pending:
                        if getattr(pending, "requested_toolkit", ""):
                            refresh_connector_status(ws_id, pending.requested_toolkit, db, request_cache={})
                        mark_request_resumed(db, resume_token)
                        context = pending.context_json or {}
                        workflow_key = context.get("workflow_key")
                        retry_connector_context = context.get("connector_context") or build_connector_context(st.session_state)
                        result = handle_request(
                            pending.original_input,
                            ws_id,
                            db,
                            force_agent=pending.agent_key if (pending.agent_key and not workflow_key) else None,
                            force_workflow=workflow_key,
                            resume_state=context,
                            connector_context=retry_connector_context,
                            actor_user_id=actor_user_id,
                        )
                        if result.get("mode") not in {
                            "connect_required", "auth_unavailable", "invalid_tool",
                            "validation_error", "tool_error"
                        } and not result.get("error", False):
                            mark_request_completed(db, resume_token)
                    else:
                        result = handle_request(
                            retry_input,
                            ws_id,
                            db,
                            force_agent=st.session_state.selected_agent,
                            connector_context=build_connector_context(st.session_state),
                            actor_user_id=actor_user_id,
                        )
                else:
                    result = handle_request(
                        retry_input,
                        ws_id,
                        db,
                        force_agent=st.session_state.selected_agent,
                        connector_context=build_connector_context(st.session_state),
                        actor_user_id=actor_user_id,
                    )

            if result.get("connector_context"):
                st.session_state.connector_context = result["connector_context"]
            _append_result_to_history(result, original_input=retry_input)
            st.rerun()

        # ── Input ─────────────────────────────────────────────────────────────
        st.markdown("---")
        connector_context = _render_connector_controls(ws_id, db)
        user_input = st.chat_input("Ask anything... (e.g. 'Write a product description' or 'Create a marketing campaign')")

        if user_input:
            # Add user message
            st.session_state.chat_history.append({
                "role": "user",
                "content": user_input
            })

            # Call orchestrator — show tool-aware spinner
            selected = st.session_state.selected_agent
            agent_conf = AGENTS.get(selected, {}) if selected else {}
            tool_mode = agent_conf.get("tool_mode", "text_only")

            if tool_mode == "tool_enabled":
                spinner_msg = "🧠 Thinking... (tools available)"
            else:
                spinner_msg = "🧠 Thinking..."

            with st.spinner(spinner_msg):
                result = handle_request(
                    user_input,
                    ws_id,
                    db,
                    force_agent=selected,
                    connector_context=connector_context,
                    actor_user_id=actor_user_id,
                )

            if result.get("connector_context"):
                st.session_state.connector_context = result["connector_context"]
            _append_result_to_history(result, original_input=user_input)
            st.rerun()

    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _append_result_to_history(result: dict, original_input: str = "") -> None:
    """
    Append an orchestrator result to chat_history.
    Handles normal, workflow, connect_required, and tool-used modes.
    Persists tool metadata for display on page refresh.
    """
    # Determine label and icon
    if result["mode"] == "workflow":
        wf_name = result.get("workflow", "workflow").replace("_", " ").title()
        label = f"Workflow: {wf_name}"
        icon  = "⚙️"
    else:
        agent_key  = result.get("agent", "assistant")
        agent_conf = AGENTS.get(agent_key, {})
        label = agent_conf.get("name", agent_key)
        icon  = agent_conf.get("icon", "🤖")

    entry = {
        "role":    "assistant",
        "content": result["output"],
        "label":   label,
        "icon":    icon,
        "steps":   result.get("steps", []),
        "idea":    result.get("idea"),
    }

    # Persist tool execution metadata
    tool_used = result.get("tool_used")
    if tool_used:
        entry["tool_used"] = tool_used

    # Flag tool errors (output contains error markers from executor)
    if result.get("mode") == "tool_error":
        entry["tool_error"] = True
        entry["original_input"] = original_input

    if result.get("mode") == "invalid_tool":
        entry["invalid_tool"] = True
        entry["original_input"] = original_input

    if result.get("mode") == "validation_error":
        entry["validation_error"] = True
        entry["original_input"] = original_input
        entry["approval_required"] = result.get("approval_required", False)
        entry["approval_requirement"] = result.get("approval_requirement")
        entry["resume_token"] = result.get("resume_token", "")
        entry["pending_kind"] = result.get("pending_kind", "")

    # If connect_required, attach extra fields for the connector card
    if result.get("mode") == "connect_required":
        entry["connect_required"] = True
        entry["connect_url"]     = result.get("connect_url")
        entry["resume_token"]    = result.get("resume_token", "")
        entry["toolkit"]         = result.get("toolkit", "")
        entry["original_input"]  = original_input

    # If auth_unavailable, attach fields for the setup required card
    if result.get("mode") == "auth_unavailable":
        entry["auth_unavailable"] = True
        entry["toolkit"]          = result.get("toolkit", "")
        entry["original_input"]   = original_input
        entry["auth_error"]       = result.get("auth_error", "")

    if result.get("workflow_paused"):
        entry["workflow_paused"] = True
        entry["step_label"] = result.get("step_label", "")

    if result.get("workflow_resumed"):
        entry["workflow_resumed"] = True

    st.session_state.chat_history.append(entry)
