"""
ui/pages/helpers_page.py  —  Browse all 12 helpers + quick-launch
"""
import streamlit as st
from helpers.configs import list_agents, AGENTS


def render_helpers():
    st.markdown(f"## 🤖 AI Helpers — {st.session_state.workspace_name}")
    st.markdown("Your AI workforce. Each helper is a specialist. Click any to start chatting.")
    st.markdown("---")

    agents = list_agents()
    cols = st.columns(3)

    for i, agent in enumerate(agents):
        with cols[i % 3]:
            full = AGENTS[agent["key"]]
            with st.container(border=True):
                st.markdown(f"### {agent['icon']} {agent['name']}")
                st.caption(f"**{agent['role']}**")
                st.markdown(f"_{full['goal']}_")
                st.markdown(f"**Tone:** {full['tone']}")
                st.markdown("**Use cases:**")
                for uc in agent["use_cases"][:3]:
                    st.markdown(f"- {uc}")

                if st.button(
                    f"Chat with {agent['name']} →",
                    key=f"launch_{agent['key']}",
                    use_container_width=True,
                    type="primary"
                ):
                    st.session_state.selected_agent = agent["key"]
                    st.session_state.page = "chat"
                    st.rerun()
