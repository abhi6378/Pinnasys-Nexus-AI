"""
ui/pages/brain_page.py  —  Brain AI tab: profile, knowledge, quiz
"""
import streamlit as st
from storage.db import SessionLocal
from storage import repositories as repo
from brain.quiz_engine import get_next_question, save_answer, quiz_progress


def render_brain():
    ws_id = st.session_state.workspace_id
    st.markdown(f"## 🧠 Brain AI — {st.session_state.workspace_name}")
    st.markdown("Brain AI is the shared memory all your helpers use to stay on-brand.")

    db = SessionLocal()
    try:
        progress = quiz_progress(ws_id, db)

        # ── Progress bar ──────────────────────────────────────────────────────
        st.progress(progress["percent"] / 100,
                    text=f"Brain AI {progress['percent']}% complete — {progress['filled']}/{progress['total']} fields")

        tab1, tab2, tab3 = st.tabs(["📋 Business Profile", "📚 Knowledge Base", "❓ Setup Quiz"])

        # ── Tab 1: Profile ────────────────────────────────────────────────────
        with tab1:
            brain = repo.get_brain(db, ws_id)
            st.markdown("### Business Profile")
            st.caption("This is injected into every helper prompt automatically.")

            with st.form("brain_profile_form"):
                col1, col2 = st.columns(2)
                with col1:
                    company_name  = st.text_input("Company Name",  value=brain.company_name  if brain else "")
                    tone          = st.text_input("Brand Tone",    value=brain.tone          if brain else "",
                                                  placeholder="e.g. Professional, Friendly, Bold")
                    goals         = st.text_area("Business Goals", value=brain.goals         if brain else "", height=80)
                    pricing       = st.text_area("Pricing Info",   value=brain.pricing       if brain else "", height=80)
                with col2:
                    brand_context = st.text_area("Business Description", value=brain.brand_context if brain else "", height=100)
                    audience      = st.text_area("Target Audience",      value=brain.audience      if brain else "", height=80)
                    services      = st.text_area("Products / Services",  value=brain.services      if brain else "", height=80)
                    competitors   = st.text_area("Competitors",          value=brain.competitors   if brain else "", height=60)

                support_style = st.text_input("Support Style", value=brain.support_style if brain else "",
                                              placeholder="e.g. Empathetic, Fast, Formal")

                if st.form_submit_button("💾 Save Brain AI Profile", type="primary"):
                    repo.update_brain(db, ws_id, {
                        "company_name":  company_name,
                        "brand_context": brand_context,
                        "tone":          tone,
                        "audience":      audience,
                        "goals":         goals,
                        "services":      services,
                        "pricing":       pricing,
                        "competitors":   competitors,
                        "support_style": support_style,
                    })
                    st.success("✅ Brain AI profile updated!")
                    st.rerun()

        # ── Tab 2: Knowledge Base ─────────────────────────────────────────────
        with tab2:
            st.markdown("### Knowledge Base")
            st.caption("Add facts, FAQs, docs, and links that helpers will reference.")

            with st.form("add_knowledge_form"):
                k_title   = st.text_input("Title", placeholder="e.g. Refund Policy, Product Features")
                k_type    = st.selectbox("Type", ["text", "faq", "link", "policy", "product"])
                k_content = st.text_area("Content", height=120,
                                         placeholder="Paste your knowledge here...")
                k_tags    = st.text_input("Tags (comma separated)", placeholder="refund, policy, returns")

                if st.form_submit_button("➕ Add to Knowledge Base", type="primary"):
                    if k_title.strip() and k_content.strip():
                        tags = [t.strip() for t in k_tags.split(",") if t.strip()]
                        repo.add_knowledge(db, ws_id, k_type, k_title.strip(), k_content.strip(), tags)
                        st.success(f"✅ '{k_title}' added to Brain AI!")
                        st.rerun()
                    else:
                        st.warning("Title and content are required.")

            st.markdown("---")
            st.markdown("### 📖 Saved Knowledge")

            items = repo.list_all_knowledge(db, ws_id)
            if items:
                for item in items:
                    with st.expander(f"[{item.type.upper()}] {item.title}"):
                        st.markdown(item.content)
                        if item.tags:
                            st.caption("Tags: " + ", ".join(item.tags))
                        if st.button("🗑️ Delete", key=f"del_k_{item.id}"):
                            repo.delete_knowledge(db, item.id)
                            st.rerun()
            else:
                st.info("No knowledge added yet. Add facts, policies, or FAQs above.")

        # ── Tab 3: Quiz ───────────────────────────────────────────────────────
        with tab3:
            st.markdown("### 🎯 Brain AI Setup Quiz")
            st.caption("Answer these questions to make your helpers smarter.")

            if progress["complete"]:
                st.success("🎉 Brain AI is fully set up! Your helpers have all the context they need.")
            else:
                next_q = get_next_question(ws_id, db)
                if next_q:
                    st.info(f"**{next_q['question']}**")
                    st.caption(f"Category: {next_q['category']} | Field: `{next_q['field']}`")
                    answer = st.text_area("Your answer", height=100, key="quiz_brain_ans")
                    if st.button("✅ Save Answer", type="primary"):
                        if answer.strip():
                            save_answer(ws_id, next_q["field"], next_q["question"], answer.strip(), db)
                            st.success("Saved! Next question loading...")
                            st.rerun()
                        else:
                            st.warning("Please enter an answer.")

            # Show all previous answers
            answers = repo.get_quiz_answers(db, ws_id)
            if answers:
                st.markdown("---")
                st.markdown("#### ✅ Answered Questions")
                for qa in answers:
                    st.markdown(f"**Q:** {qa.question}")
                    st.markdown(f"**A:** {qa.answer}")
                    st.caption(f"Category: {qa.category}")
                    st.markdown("---")

    finally:
        db.close()
