"""
ui/pages/chat_page.py  —  Main chat interface with agent selector

Mission 4 update:
  - Renders connect_required messages as a connector card with Connect button.
  - Shows a Retry button after connect_required so the user can re-send
    their original request without re-typing it.
  - Stores pending retry context in st.session_state.pending_tool_retry.
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

            _append_result_to_history(result)
            st.rerun()

        # ── Input ─────────────────────────────────────────────────────────────
        user_input = st.chat_input("Ask anything... (e.g. 'Write a product description' or 'Create a marketing campaign')")

        if user_input:
            # Add user message
            st.session_state.chat_history.append({
                "role": "user",
                "content": user_input
            })

            # Call orchestrator
            with st.spinner("🧠 Thinking..."):
                result = handle_request(
                    user_input,
                    ws_id,
                    db,
                    force_agent=st.session_state.selected_agent
                )

            _append_result_to_history(result, original_input=user_input)
            st.rerun()

    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _append_result_to_history(result: dict, original_input: str = "") -> None:
    """
    Append an orchestrator result to chat_history.
    Handles normal, workflow, and connect_required modes.
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

    # If connect_required, attach extra fields for the connector card
    if result.get("mode") == "connect_required":
        entry["connect_required"] = True
        entry["connect_url"]     = result.get("connect_url")
        entry["resume_token"]    = result.get("resume_token", "")
        entry["toolkit"]         = result.get("toolkit", "")
        entry["original_input"]  = original_input

    st.session_state.chat_history.append(entry)
