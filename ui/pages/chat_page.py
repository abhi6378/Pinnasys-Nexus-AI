"""
ui/pages/chat_page.py  —  Main chat interface with agent selector
"""
import streamlit as st
from storage.db import SessionLocal
from orchestrator.handler import handle_request
from helpers.configs import list_agents, AGENTS
from brain.quiz_engine import get_next_question, save_answer, quiz_progress


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
            for msg in st.session_state.chat_history:
                if msg["role"] == "user":
                    with st.chat_message("user"):
                        st.markdown(msg["content"])
                else:
                    icon = msg.get("icon", "🤖")
                    label = msg.get("label", "Assistant")
                    with st.chat_message("assistant"):
                        st.caption(f"{icon} **{label}**")
                        st.markdown(msg["content"])

                        # Show workflow steps if available
                        if msg.get("steps"):
                            with st.expander("🔍 View workflow steps"):
                                for step in msg["steps"]:
                                    st.markdown(f"**Step: {step.get('step', '')}** — Agent: `{step.get('agent', '')}`")
                                    st.markdown(step.get("output", "")[:400] + "...")
                                    st.markdown("---")

                        # Idea notification
                        if msg.get("idea"):
                            idea = msg["idea"]
                            st.info(f"💡 **New Idea:** {idea['title']}\n\n{idea['description']}")
                            if st.button("View in Ideas Inbox →", key=f"goto_ideas_{idea['id']}"):
                                st.session_state.page = "ideas"
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

            st.session_state.chat_history.append({
                "role":    "assistant",
                "content": result["output"],
                "label":   label,
                "icon":    icon,
                "steps":   result.get("steps", []),
                "idea":    result.get("idea"),
            })

            st.rerun()

    finally:
        db.close()
