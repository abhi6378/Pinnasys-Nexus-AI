# ============================================================
# app.py
# ------------------------------------------------------------
# Streamlit UI — the client-facing interface.
#
# Features:
#   - 12 agents in sidebar with emoji identifiers
#   - Business context setup panel (feeds shared workspace)
#   - 3 execution modes: Single agent / Workflow / Auto-route
#   - Workflow result display (step-by-step breakdown)
#   - Live workspace memory viewer
#   - Clear chat per agent
#
# Run: streamlit run app.py
# ============================================================

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from orchestrator.handler import Orchestrator
from agents.configs import AGENTS
from memory.workspace import WorkspaceMemory
from memory.conversation import ConversationMemory
from workflow.engine import WORKFLOWS


# ── PAGE CONFIG ───────────────────────────────────────────────
st.set_page_config(
    page_title="AI Workforce",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    #MainMenu { visibility: hidden; }
    footer    { visibility: hidden; }
    .stAlert  { border-radius: 10px; }
    .step-card {
        background: #f8f9fa;
        border-left: 4px solid #6c63ff;
        border-radius: 6px;
        padding: 12px 16px;
        margin: 8px 0;
    }
    .step-label {
        font-size: 12px;
        font-weight: 600;
        color: #6c63ff;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
</style>
""", unsafe_allow_html=True)


# ── SESSION STATE ─────────────────────────────────────────────
if "workspace" not in st.session_state:
    st.session_state.workspace = WorkspaceMemory()

if "conv_memory" not in st.session_state:
    st.session_state.conv_memory = ConversationMemory()

if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator(
        workspace=st.session_state.workspace,
        conversation_memory=st.session_state.conv_memory,
    )

if "selected_agent" not in st.session_state:
    st.session_state.selected_agent = "Copywriter"

if "chat_log" not in st.session_state:
    st.session_state.chat_log = {}

if "exec_mode" not in st.session_state:
    st.session_state.exec_mode = "single"

if "selected_workflow" not in st.session_state:
    st.session_state.selected_workflow = list(WORKFLOWS.keys())[0]


# ── SIDEBAR ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🤖 AI Workforce")
    st.caption("Multi-agent system · Powered by GPT + LangChain")
    st.divider()

    # ── Execution mode ────────────────────────────────────────
    st.markdown("**Execution Mode**")
    exec_mode = st.radio(
        label="mode",
        options=["Single Agent", "Workflow", "Auto-Route"],
        label_visibility="collapsed",
        horizontal=False,
    )
    mode_map = {
        "Single Agent": "single",
        "Workflow":     "workflow",
        "Auto-Route":   "auto",
    }
    st.session_state.exec_mode = mode_map[exec_mode]

    # Show workflow selector if workflow mode
    if st.session_state.exec_mode == "workflow":
        st.markdown("**Select Workflow**")
        wf_options = {
            v["name"]: k for k, v in WORKFLOWS.items()
        }
        selected_wf_name = st.selectbox(
            "workflow",
            options=list(wf_options.keys()),
            label_visibility="collapsed",
        )
        st.session_state.selected_workflow = wf_options[selected_wf_name]
        wf = WORKFLOWS[st.session_state.selected_workflow]
        st.caption(f"{wf['description']}")
        st.caption(" → ".join(wf["steps"]))

    st.divider()

    # ── Agent selection ───────────────────────────────────────
    st.markdown("**Your AI Team**")
    for name, config in AGENTS.items():
        emoji = config.get("emoji", "🤖")
        label = f"{emoji}  {name}"
        is_oc = config["use_openclaw"]
        tag = " 🔵" if is_oc else ""

        if name == st.session_state.selected_agent:
            st.markdown(f"**→ {label}{tag}**")
        else:
            if st.button(label + tag, key=f"btn_{name}", use_container_width=True):
                st.session_state.selected_agent = name
                st.session_state.exec_mode = "single"
                st.rerun()

    st.divider()

    # ── Business context setup ────────────────────────────────
    with st.expander("⚙️ Business Context (Shared Memory)"):
        ws = st.session_state.workspace
        with st.form("biz_form"):
            company = st.text_input("Company name", value=ws.business_context["company_name"])
            industry = st.text_input("Industry", value=ws.business_context["industry"])
            audience = st.text_input("Target audience", value=ws.business_context["target_audience"])
            product = st.text_input("Product / Service", value=ws.business_context["product_service"])
            usp = st.text_area("USP / Value proposition", value=ws.business_context["usp"], height=60)
            tone = st.text_input("Brand tone", value=ws.business_context["brand_tone"]["overall_tone"])
            saved = st.form_submit_button("Save to Shared Memory")

        if saved:
            ws.set_business_context(
                company_name=company,
                industry=industry,
                target_audience=audience,
                product_service=product,
                usp=usp,
            )
            ws.brand_tone["overall_tone"] = tone
            st.success("✅ Saved — all agents now know your business context.")

    # ── Workspace viewer ──────────────────────────────────────
    with st.expander("🧠 Shared Workspace Memory"):
        outputs = st.session_state.workspace.agent_outputs
        if outputs:
            for agent, data in outputs.items():
                st.markdown(f"**{agent}** — {data['task'][:40]}")
                st.caption(data["output"][:150] + "...")
                st.divider()
        else:
            st.caption("No outputs yet. Chat with agents to populate workspace.")

    # ── Clear buttons ─────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            agent = st.session_state.selected_agent
            st.session_state.conv_memory.clear_agent(agent)
            st.session_state.chat_log[agent] = []
            st.rerun()
    with col2:
        if st.button("🧹 Clear All", use_container_width=True):
            st.session_state.conv_memory.clear_all()
            st.session_state.chat_log = {}
            st.session_state.workspace = WorkspaceMemory()
            st.session_state.orchestrator = Orchestrator(
                workspace=st.session_state.workspace,
                conversation_memory=st.session_state.conv_memory,
            )
            st.rerun()


# ── MAIN AREA ─────────────────────────────────────────────────
selected = st.session_state.selected_agent
config = AGENTS[selected]
emoji = config.get("emoji", "🤖")
mode = st.session_state.exec_mode

# Header
col1, col2 = st.columns([3, 1])
with col1:
    if mode == "workflow":
        wf = WORKFLOWS[st.session_state.selected_workflow]
        st.markdown(f"## {wf['emoji']}  {wf['name']} Workflow")
        st.caption(wf["description"])
    elif mode == "auto":
        st.markdown("## 🧭  Auto-Route Mode")
        st.caption("The orchestrator will automatically pick the best agent or workflow for your request.")
    else:
        st.markdown(f"## {emoji}  {selected}")
        st.caption(config["role"][:120] + "...")
with col2:
    path = "🔵 OpenClaw" if config["use_openclaw"] else "🟢 GPT + LangChain"
    st.markdown(f"<br><span style='font-size:12px'>{path}</span>", unsafe_allow_html=True)

st.divider()

# ── CHAT HISTORY ──────────────────────────────────────────────
chat_key = f"{mode}_{selected}"
chat_log = st.session_state.chat_log.get(chat_key, [])

if not chat_log:
    _examples = {
        "Copywriter":          "Write a LinkedIn post about the power of AI in small businesses.",
        "SEO Specialist":      "Give me 10 long-tail keywords for a SaaS project management tool.",
        "Social Media Manager":"Write 5 Instagram captions for our new product launch.",
        "Email Marketer":      "Write a 3-email welcome sequence for new SaaS trial users.",
        "Customer Support":    "Reply to a customer upset about a delayed refund.",
        "Data Analyst":        "Analyze Q1 sales data and give me the top 3 insights.",
        "Sales Strategist":    "Write a cold outreach script for selling HR software.",
        "Business Strategist": "Do a SWOT analysis of entering the European market.",
        "Virtual Assistant":   "Draft a follow-up email to a client after a demo call.",
    }
    example = _examples.get(selected, "Ask me anything!")
    st.info(f"💡 **Try:** {example}")

for entry in chat_log:
    with st.chat_message("user"):
        st.write(entry["user"])

    if entry.get("type") == "workflow":
        # ── Workflow result display ───────────────────────────
        with st.chat_message("assistant", avatar="🔗"):
            st.markdown(f"**{entry.get('workflow_name', 'Workflow')} complete** — {entry.get('description', '')}")

            for step in entry.get("steps", []):
                with st.expander(
                    f"Step {step['step']} — {step['emoji']} {step['agent']}",
                    expanded=(step["step"] == len(entry["steps"]))
                ):
                    st.markdown(step["output"])
    else:
        # ── Single agent result display ───────────────────────
        with st.chat_message("assistant", avatar=emoji):
            if entry.get("reason") and mode == "auto":
                st.caption(f"🧭 Routed to: **{entry.get('routed_to', selected)}** — {entry.get('reason', '')}")
            st.markdown(entry["response"])


# ── CHAT INPUT ────────────────────────────────────────────────
placeholder = {
    "single":   f"Message {selected}...",
    "workflow": f"Describe your task for the workflow...",
    "auto":     "Describe what you need — the system will route it automatically...",
}.get(mode, "Message...")

user_input = st.chat_input(placeholder)

if user_input:
    with st.chat_message("user"):
        st.write(user_input)

    orch = st.session_state.orchestrator

    with st.chat_message("assistant", avatar=emoji):
        with st.spinner("🧠 Orchestrating..."):

            if mode == "workflow":
                result = orch.handle(
                    user_input=user_input,
                    agent_name=selected,
                    mode=f"workflow:{st.session_state.selected_workflow}",
                )
            elif mode == "auto":
                result = orch.handle(user_input=user_input, agent_name=selected, mode="auto")
            else:
                result = orch.handle(user_input=user_input, agent_name=selected, mode="single")

        # ── Display result ────────────────────────────────────
        if result["type"] == "workflow":
            st.markdown(f"**{result.get('workflow_name', 'Workflow')} complete** — {result.get('description', '')}")
            for step in result.get("steps", []):
                with st.expander(
                    f"Step {step['step']} — {step['emoji']} {step['agent']}",
                    expanded=(step["step"] == len(result["steps"]))
                ):
                    st.markdown(step["output"])
        else:
            if result.get("reason") and mode == "auto":
                st.caption(f"🧭 Routed to: **{result.get('routed_to', selected)}** — {result.get('reason', '')}")
            st.markdown(result["response"])

    # Save to chat log
    if chat_key not in st.session_state.chat_log:
        st.session_state.chat_log[chat_key] = []

    log_entry = {
        "user":          user_input,
        "type":          result["type"],
        "response":      result.get("response", ""),
        "steps":         result.get("steps", []),
        "workflow_name": result.get("workflow_name", ""),
        "description":   result.get("description", ""),
        "routed_to":     result.get("routed_to", selected),
        "reason":        result.get("reason", ""),
    }
    st.session_state.chat_log[chat_key].append(log_entry)
    st.rerun()
