"""
ui/pages/workflows_page.py  —  Browse + manually launch workflows + history
"""
import streamlit as st
from storage.db import SessionLocal
from storage import repositories as repo
from orchestrator.handler import handle_request


WORKFLOW_META = {
    "marketing_campaign": {
        "icon": "📣",
        "title": "Marketing Campaign",
        "description": "Full campaign package: copy → SEO optimization → social media posts",
        "agents": ["✍️ Penn (Copywriter)", "🔍 Seomi (SEO)", "📱 Soshie (Social Media)"],
        "example": "Launch campaign for our new running shoes collection",
    },
    "content_creation": {
        "icon": "📝",
        "title": "Content Creation",
        "description": "Blog post or article with SEO recommendations",
        "agents": ["✍️ Penn (Copywriter)", "🔍 Seomi (SEO)"],
        "example": "Write a blog post about the benefits of daily exercise",
    },
    "sales_outreach": {
        "icon": "💰",
        "title": "Sales Outreach",
        "description": "Sales strategy + 3-email outreach sequence",
        "agents": ["💰 Milli (Sales)", "📧 Emmie (Email Marketer)"],
        "example": "Outreach campaign targeting small business owners",
    },
    "support_setup": {
        "icon": "💬",
        "title": "Support Setup",
        "description": "Customer support scripts + polished FAQ content",
        "agents": ["💬 Cassie (Support)", "✍️ Penn (Copywriter)"],
        "example": "FAQ and scripts for our SaaS product billing questions",
    },
    "business_strategy": {
        "icon": "🧠",
        "title": "Business Strategy",
        "description": "Full business strategy + KPIs and data recommendations",
        "agents": ["🧠 Strat (Strategist)", "📊 Dexter (Data Analyst)"],
        "example": "Strategy for expanding into the European market",
    },
}


def render_workflows():
    ws_id = st.session_state.workspace_id
    st.markdown(f"## ⚙️ Workflows — {st.session_state.workspace_name}")
    st.markdown("Multi-step AI pipelines. Each workflow chains multiple helpers automatically.")
    st.markdown("---")

    tab1, tab2 = st.tabs(["🚀 Run a Workflow", "📜 Workflow History"])

    with tab1:
        cols = st.columns(2)
        for i, (key, meta) in enumerate(WORKFLOW_META.items()):
            with cols[i % 2]:
                with st.container(border=True):
                    st.markdown(f"### {meta['icon']} {meta['title']}")
                    st.markdown(meta["description"])
                    st.markdown("**Agents involved:**")
                    for a in meta["agents"]:
                        st.markdown(f"  - {a}")

                    st.caption(f"Example: _{meta['example']}_")

                    with st.form(f"wf_form_{key}"):
                        user_input = st.text_area(
                            "What do you want to create?",
                            placeholder=meta["example"],
                            key=f"wf_input_{key}",
                            height=80
                        )
                        submitted = st.form_submit_button(
                            f"▶️ Run {meta['title']}", type="primary",
                            use_container_width=True
                        )

                    if submitted:
                        if user_input.strip():
                            db = SessionLocal()
                            try:
                                with st.spinner(f"⚙️ Running {meta['title']} workflow..."):
                                    # Route through handle_request with force_workflow so
                                    # memory extraction, idea detection, and
                                    # save_workflow_run all run inside the orchestrator.
                                    # No manual BrainAI init or duplicate save needed.
                                    result = handle_request(
                                        user_input, ws_id, db,
                                        force_workflow=key
                                    )

                                st.success("✅ Workflow complete!")

                                # Push to chat history
                                st.session_state.chat_history.append({
                                    "role":    "user",
                                    "content": f"[{meta['title']} Workflow] {user_input}",
                                })
                                st.session_state.chat_history.append({
                                    "role":    "assistant",
                                    "content": result["output"],
                                    "label":   f"Workflow: {meta['title']}",
                                    "icon":    meta["icon"],
                                    "steps":   result.get("steps", []),
                                    "idea":    result.get("idea"),
                                })

                                with st.expander("📋 View full output", expanded=True):
                                    st.markdown(result["output"])

                                with st.expander("🔍 Step-by-step trace"):
                                    for step in result.get("steps", []):
                                        st.markdown(f"**{step['step']}** — `{step['agent']}`")
                                        st.markdown(step["output"][:500])
                                        st.markdown("---")

                                # Surface idea notification if the orchestrator detected one
                                if result.get("idea"):
                                    idea = result["idea"]
                                    st.info(
                                        f"💡 **New Idea detected:** {idea['title']}\n\n"
                                        f"{idea['description']}\n\n"
                                        "_Check your Ideas Inbox to act on it._"
                                    )

                            finally:
                                db.close()
                        else:
                            st.warning("Enter a description to run the workflow.")

    with tab2:
        st.markdown("### 📜 Past Workflow Runs")
        db = SessionLocal()
        try:
            runs = repo.get_workflow_runs(db, ws_id)
            if not runs:
                st.info("No workflows run yet.")
            else:
                for run in runs:
                    meta = WORKFLOW_META.get(run.workflow_name, {})
                    icon = meta.get("icon", "⚙️")
                    title = meta.get("title", run.workflow_name)
                    with st.expander(
                        f"{icon} {title} — {str(run.created_at)[:16]}"
                    ):
                        st.markdown(run.final_output)
                        if run.steps:
                            st.markdown("**Steps:**")
                            for step in run.steps:
                                st.markdown(f"- **{step.get('step')}** via `{step.get('agent')}`")
        finally:
            db.close()
