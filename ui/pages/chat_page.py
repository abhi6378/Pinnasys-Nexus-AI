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
from orchestrator.handler import handle_request
from helpers.configs import list_agents, AGENTS
from brain.quiz_engine import get_next_question, save_answer, quiz_progress


# ── Toolkit display helpers ───────────────────────────────────────────────────

TOOLKIT_ICONS = {
    "GMAIL":            ("mail", "Gmail"),
    "GOOGLE_CALENDAR":  ("calendar", "Google Calendar"),
    "SLACK":            ("hash", "Slack"),
    "HUBSPOT":          ("contact", "HubSpot"),
    "GITHUB":           ("code", "GitHub"),
    "GOOGLE_SHEETS":    ("table", "Google Sheets"),
}

# Maps tool_name → (emoji, human label) for execution indicators
TOOL_DISPLAY = {
    "GMAIL_SEND_EMAIL":              ("📧", "Sent email via Gmail"),
    "GMAIL_GET_PROFILE":             ("📧", "Fetched Gmail profile"),
    "GMAIL_LIST_EMAILS":             ("📧", "Listed emails from Gmail"),
    "GOOGLE_CALENDAR_CREATE_EVENT":  ("📅", "Created calendar event"),
    "GOOGLE_CALENDAR_LIST_EVENTS":   ("📅", "Listed calendar events"),
    "SLACK_SEND_MESSAGE":            ("💬", "Sent Slack message"),
    "HUBSPOT_CREATE_CONTACT":        ("📊", "Created HubSpot contact"),
    "HUBSPOT_GET_CONTACTS":          ("📊", "Fetched HubSpot contacts"),
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
        else:
            st.warning("Connection link unavailable. Please try again later.")

    with col_retry:
        if original_input:
            if st.button(
                "🔄 Retry Request",
                key=f"retry_{msg_idx}_{resume_token[:8]}",
                use_container_width=True,
            ):
                # Store the retry context and trigger a rerun.
                # The main input handler will pick this up.
                st.session_state.pending_tool_retry = {
                    "original_input": original_input,
                    "resume_token": resume_token,
                }
                st.rerun()


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
        ⚠️ <strong>Tool execution failed — responded from knowledge</strong>
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


# ── Main page render ──────────────────────────────────────────────────────────

def render_chat():
    ws_id = st.session_state.workspace_id

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

                        # ── Connect-required card ─────────────────────────────
                        if msg.get("connect_required"):
                            _render_connect_card(msg, msg_idx)

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
        pending_retry = st.session_state.get("pending_tool_retry")
        if pending_retry:
            retry_input = pending_retry["original_input"]
            # Clear the retry state immediately so it doesn't re-trigger
            st.session_state.pending_tool_retry = None

            # Add user message
            st.session_state.chat_history.append({
                "role": "user",
                "content": f"🔄 _{retry_input}_"
            })

            # Re-send through orchestrator
            with st.spinner("🔄 Retrying with connected tools..."):
                result = handle_request(
                    retry_input,
                    ws_id,
                    db,
                    force_agent=st.session_state.selected_agent
                )

            _append_result_to_history(result, original_input=retry_input)
            st.rerun()

        # ── Input ─────────────────────────────────────────────────────────────
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
                    force_agent=selected
                )

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
    if result.get("error") and not result.get("mode") == "workflow":
        entry["tool_error"] = True
        entry["original_input"] = original_input

    # If connect_required, attach extra fields for the connector card
    if result.get("mode") == "connect_required":
        entry["connect_required"] = True
        entry["connect_url"]     = result.get("connect_url")
        entry["resume_token"]    = result.get("resume_token", "")
        entry["toolkit"]         = result.get("toolkit", "")
        entry["original_input"]  = original_input

    st.session_state.chat_history.append(entry)
