# ============================================================
# app.py  (v6) — Streamlit UI
# ============================================================
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

from orchestrator.handler import Orchestrator
from orchestrator.execution_control import ExecutionConfig
from orchestrator.intent_classifier import classify_full, IntentClassifier
from agents.configs import AGENTS
from memory.workspace import WorkspaceMemory
from memory.conversation import ConversationMemory
from workflow.engine import WORKFLOWS

st.set_page_config(
    page_title="AI Workforce v6",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  #MainMenu{visibility:hidden}footer{visibility:hidden}
  .tool-badge{display:inline-block;font-size:11px;padding:2px 8px;
    border-radius:12px;background:#EAF3DE;color:#27500A;margin:2px}
  .memory-dot{display:inline-block;width:8px;height:8px;
    border-radius:50%;background:#1D9E75;margin-right:6px}
  .trace-line{font-size:10px;color:#888;font-family:monospace;
    border-left:2px solid #EEEDFE;padding-left:8px;margin:2px 0}
  .route-badge{display:inline-block;font-size:11px;padding:2px 10px;
    border-radius:12px;background:#FFF3CD;color:#856404;margin-right:6px}
  .intent-badge{display:inline-block;font-size:11px;padding:2px 10px;
    border-radius:12px;background:#D1ECF1;color:#0C5460;margin:2px}
  .intent-label{display:inline-block;font-size:11px;padding:2px 8px;
    border-radius:12px;background:#E8DEF8;color:#4A3580;margin:2px}
  .conv-user{color:#1a73e8;font-size:12px;font-family:monospace}
  .conv-agent{color:#188038;font-size:12px;font-family:monospace}
</style>
""", unsafe_allow_html=True)


# ── SESSION STATE ─────────────────────────────────────────────

def _init():
    defaults = {
        "workspace":      None,
        "conv_memory":    None,
        "orchestrator":   None,
        "selected_agent": "Copywriter",
        "chat_log":       {},
        "exec_mode":      "single",
        "sel_workflow":   list(WORKFLOWS.keys())[0],
        "exec_config":    ExecutionConfig(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    if st.session_state.workspace is None:
        st.session_state.workspace = WorkspaceMemory()
    if st.session_state.conv_memory is None:
        st.session_state.conv_memory = ConversationMemory()
    if st.session_state.orchestrator is None:
        st.session_state.orchestrator = Orchestrator(
            st.session_state.workspace,
            st.session_state.conv_memory,
            st.session_state.exec_config,
        )

_init()


# ── SIDEBAR ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🤖 AI Workforce v6")
    st.caption("Orchestrated multi-agent platform with shared Brain AI")
    st.divider()

    # ── Execution Mode ────────────────────────────────────────
    st.markdown("**Execution Mode**")
    mode_label = st.radio(
        "mode", ["Single Agent", "Workflow", "Auto-Route"],
        label_visibility="collapsed",
    )
    mode_map = {"Single Agent": "single", "Workflow": "workflow", "Auto-Route": "auto"}
    st.session_state.exec_mode = mode_map[mode_label]

    if st.session_state.exec_mode == "workflow":
        wf_options = {v["name"]: k for k, v in WORKFLOWS.items()}
        sel = st.selectbox("workflow", list(wf_options.keys()), label_visibility="collapsed")
        st.session_state.sel_workflow = wf_options[sel]
        wf = WORKFLOWS[st.session_state.sel_workflow]
        st.caption(f"_{wf['description']}_")
        st.caption("→ " + " → ".join(wf["steps"]))

    st.divider()

    # ── Agent Roster ──────────────────────────────────────────
    st.markdown("**Your AI Team**")
    for name, cfg in AGENTS.items():
        emoji = cfg.get("emoji", "🤖")
        claw  = " 🔵" if cfg["use_openclaw"] else ""
        label = f"{emoji}  {name}{claw}"
        if name == st.session_state.selected_agent:
            st.markdown(f"**→ {label}**")
        else:
            if st.button(label, key=f"btn_{name}", use_container_width=True):
                st.session_state.selected_agent = name
                st.session_state.exec_mode      = "single"
                st.rerun()

    st.divider()

    # ── Brain AI Context ──────────────────────────────────────
    with st.expander("⚙️ Brain AI — Business Context"):
        ws = st.session_state.workspace
        with st.form("biz_form"):
            company  = st.text_input("Company",         value=ws.business_context["company_name"])
            industry = st.text_input("Industry",        value=ws.business_context["industry"])
            audience = st.text_input("Target audience", value=ws.business_context["target_audience"])
            product  = st.text_input("Product/Service", value=ws.business_context["product_service"])
            usp      = st.text_area("USP",              value=ws.business_context["usp"], height=60)
            tone     = st.text_input("Brand tone",      value=ws.brand_tone["overall_tone"])
            keywords = st.text_input("Brand keywords",  value=", ".join(ws.brand_tone["keywords_to_use"]))
            avoid    = st.text_input("Avoid words",     value=", ".join(ws.brand_tone["keywords_to_avoid"]))
            saved    = st.form_submit_button("💾 Save to Brain AI")
        if saved:
            ws.set_business_context(company_name=company, industry=industry,
                                    target_audience=audience, product_service=product, usp=usp)
            ws.set_brand_tone(
                overall_tone=tone,
                keywords_to_use=[k.strip() for k in keywords.split(",") if k.strip()],
                keywords_to_avoid=[k.strip() for k in avoid.split(",") if k.strip()],
            )
            st.success("✅ Saved — all agents share this context.")

    # ── Memory State (v6 structured view) ────────────────────
    with st.expander("🧠 Brain AI Memory State"):
        ws      = st.session_state.workspace
        mem     = ws.get_memory_state()
        summary = ws.get_session_summary()

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Steps",      summary["total_steps"])
        col_b.metric("Conv turns", summary["conv_turns"])
        col_c.metric("Agents",     len(summary["agents_active"]))

        if summary["agents_active"]:
            st.caption("Active agents: " + ", ".join(summary["agents_active"]))

        st.divider()

        # Conversation history (v6)
        if mem["conversation_history"]:
            st.markdown("**Recent conversation_history:**")
            for turn in mem["conversation_history"][-4:]:
                role    = turn["role"]
                label   = f"[{turn.get('agent','user').upper()}]"
                content = turn["content"][:120] + "..."
                css     = "conv-user" if role == "user" else "conv-agent"
                st.markdown(
                    f'<div class="{css}">{label} {content}</div>',
                    unsafe_allow_html=True,
                )
            st.divider()

        # Previous outputs (v6)
        if mem["previous_outputs"]:
            st.markdown("**previous_outputs:**")
            for entry in reversed(mem["previous_outputs"][-4:]):
                st.markdown(
                    f'<span class="memory-dot"></span>**{entry["agent"]}**'
                    + (f' [step {entry["step"]}]' if entry.get("step") else ""),
                    unsafe_allow_html=True,
                )
                if entry.get("metadata", {}).get("intent"):
                    st.markdown(
                        f'<span class="intent-label">intent: {entry["metadata"]["intent"]}</span>',
                        unsafe_allow_html=True,
                    )
                st.caption(entry["content"][:100] + "...")
                st.divider()
        else:
            st.caption("Empty. Run agents to populate.")

    # ── Execution Control ─────────────────────────────────────
    with st.expander("⚡ Execution Control"):
        cfg = st.session_state.exec_config
        max_steps = st.slider("Max workflow steps", 2, 10, value=cfg.max_steps)
        timeout   = st.slider("Request timeout (sec)", 30, 180, value=int(cfg.timeout_sec), step=10)
        step_to   = st.slider("Per-step timeout (sec)", 15, 90, value=int(cfg.step_timeout), step=5)
        if st.button("Apply", use_container_width=True):
            new_cfg = ExecutionConfig(max_steps=max_steps,
                                      timeout_sec=float(timeout),
                                      step_timeout=float(step_to))
            st.session_state.exec_config = new_cfg
            st.session_state.orchestrator = Orchestrator(
                st.session_state.workspace,
                st.session_state.conv_memory,
                new_cfg,
            )
            st.success(f"✅ {max_steps} steps / {timeout}s total / {step_to}s per step")

    # ── Orchestration Trace ───────────────────────────────────
    with st.expander("🔍 Orchestration Trace"):
        trace = st.session_state.orchestrator.get_trace()
        if trace:
            for entry in trace:
                st.markdown(
                    f'<div class="trace-line">{entry["timestamp"]} '
                    f'<b>{entry["event"]}</b> — '
                    f'{str(entry.get("details",""))[:120]}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Send a message to see trace.")

    # ── Controls ──────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            agent = st.session_state.selected_agent
            key   = f"{st.session_state.exec_mode}_{agent}"
            st.session_state.conv_memory.clear_agent(agent)
            st.session_state.chat_log[key] = []
            st.rerun()
    with c2:
        if st.button("🧹 Reset All", use_container_width=True):
            for k in ["workspace", "conv_memory", "orchestrator", "chat_log", "exec_config"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()


# ── MAIN AREA ─────────────────────────────────────────────────
selected = st.session_state.selected_agent
config   = AGENTS[selected]
emoji    = config.get("emoji", "🤖")
mode     = st.session_state.exec_mode

col1, col2 = st.columns([3, 1])
with col1:
    if mode == "workflow":
        wf = WORKFLOWS[st.session_state.sel_workflow]
        st.markdown(f"## {wf['emoji']}  {wf['name']}")
        st.caption(f"_{wf['description']}_")
    elif mode == "auto":
        st.markdown("## 🧭  Auto-Route Mode")
        st.caption("Keyword classifier first → agent hint → LLM router (last resort).")
    else:
        st.markdown(f"## {emoji}  {selected}")
        st.caption(config["role"][:120] + "...")
with col2:
    path = "🔵 OpenClaw" if config["use_openclaw"] else "🟢 LLM Path"
    cfg  = st.session_state.exec_config
    st.markdown(
        f"<br><span style='font-size:11px'>{path}<br>Max {cfg.max_steps} steps</span>",
        unsafe_allow_html=True,
    )

st.divider()

# ── INTENT PREVIEW (auto mode) ────────────────────────────────
if mode == "auto":
    with st.expander("🔎 Intent Classifier Preview (type to test)", expanded=False):
        preview = st.text_input(
            "Preview:",
            placeholder="e.g. create a full marketing campaign for our product launch",
            key="intent_preview",
        )
        if preview:
            clf    = IntentClassifier()
            intent = clf.classify(preview)
            agent  = clf.suggest_agent(preview)
            if intent.workflow:
                wf_name = WORKFLOWS.get(intent.workflow, {}).get("name", intent.workflow)
                st.markdown(
                    f'<span class="intent-badge">🔗 → {wf_name}</span>'
                    f' confidence: {intent.confidence}',
                    unsafe_allow_html=True,
                )
                if intent.matched_on:
                    st.caption(f"Matched on: {intent.matched_on[:3]}")
            elif agent:
                st.markdown(
                    f'<span class="intent-badge">👤 → {agent}</span>'
                    f' keyword match',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<span class="route-badge">🤖 LLM Router will decide</span>',
                    unsafe_allow_html=True,
                )
    st.divider()


# ── CHAT HISTORY ──────────────────────────────────────────────
chat_key = f"{mode}_{selected}"
chat_log = st.session_state.chat_log.get(chat_key, [])

EXAMPLES = {
    "Copywriter":           "Write a LinkedIn post about AI transforming small businesses.",
    "SEO Specialist":       "Give me 10 long-tail keywords for a SaaS project management tool.",
    "Social Media Manager": "Write 5 Instagram captions for our new product launch.",
    "Email Marketer":       "Write a 3-email welcome sequence for new SaaS trial users.",
    "Customer Support":     "Reply to a customer upset about a delayed refund.",
    "Data Analyst":         "Analyze Q1 sales data and give me the top 3 insights.",
    "Sales Strategist":     "Write a cold outreach sequence for mid-market SaaS prospects.",
    "Business Strategist":  "Build a go-to-market plan for the South Asian market.",
    "PR Specialist":        "Write a press release for our Series A funding announcement.",
    "Product Manager":      "Write a PRD for a new onboarding flow to reduce churn.",
}

if not chat_log:
    ex = EXAMPLES.get(selected, "Ask me anything!")
    st.info(f"💡 **Try:** _{ex}_")

for entry in chat_log:
    with st.chat_message("user"):
        st.write(entry["user"])

    if entry.get("type") == "workflow":
        with st.chat_message("assistant", avatar="🔗"):
            if entry.get("auto_routed"):
                st.markdown(
                    f'<span class="route-badge">🧭 Auto → {entry.get("routed_to","")}</span>'
                    f' _{entry.get("reason","")}_', unsafe_allow_html=True,
                )
            st.markdown(f"**{entry.get('workflow_name','')}** — _{entry.get('description','')}_")
            for step in entry.get("steps", []):
                is_fail = not step.get("success", True)
                with st.expander(
                    f"Step {step['step']} — {step['emoji']} {step['agent']}"
                    + (" ❌" if is_fail else ""),
                    expanded=(step["step"] == len(entry["steps"])),
                ):
                    if step.get("intent"):
                        st.markdown(
                            f'<span class="intent-label">intent: {step["intent"]}</span>',
                            unsafe_allow_html=True,
                        )
                    if step.get("tools_used"):
                        st.markdown(
                            "Tools: " + "  ".join(
                                f'<span class="tool-badge">{t}</span>'
                                for t in step["tools_used"]
                            ), unsafe_allow_html=True,
                        )
                    st.markdown(step["output"])
            es = entry.get("execution_summary", {})
            if es:
                st.caption(f"⏱ {es.get('elapsed_sec',0):.1f}s | {es.get('steps_executed',0)} steps")

    else:
        with st.chat_message("assistant", avatar=emoji):
            if entry.get("auto_routed"):
                st.markdown(
                    f'<span class="route-badge">🧭 Auto → {entry.get("routed_to","")}</span>'
                    f' _{entry.get("reason","")}_', unsafe_allow_html=True,
                )
            if entry.get("intent"):
                st.markdown(
                    f'<span class="intent-label">intent: {entry["intent"]}</span>',
                    unsafe_allow_html=True,
                )
            if entry.get("tools_used"):
                st.markdown(
                    "Tools: " + "  ".join(
                        f'<span class="tool-badge">{t}</span>'
                        for t in entry.get("tools_used", [])
                    ), unsafe_allow_html=True,
                )
            st.markdown(entry.get("response", ""))
            es = entry.get("execution_summary", {})
            if es:
                st.caption(f"⏱ {es.get('elapsed_sec',0):.1f}s")


# ── CHAT INPUT ────────────────────────────────────────────────
placeholder = {
    "single":   f"Message {selected}...",
    "workflow":  "Describe your task for the workflow...",
    "auto":      "Describe what you need — orchestrator will route it...",
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
                    user_input, selected,
                    mode=f"workflow:{st.session_state.sel_workflow}",
                )
            elif mode == "auto":
                result = orch.handle(user_input, selected, mode="auto")
            else:
                result = orch.handle(user_input, selected, mode="single")

        if result["type"] == "error":
            err_type = result.get("error_type", "error")
            if err_type == "timeout":
                st.error(f"⏰ {result['response']}")
            elif err_type == "step_limit":
                st.warning(f"🚧 {result['response']}")
            else:
                st.error(result["response"])

        elif result["type"] == "workflow":
            if result.get("auto_routed"):
                st.markdown(
                    f'<span class="route-badge">🧭 Auto → {result.get("routed_to","")}</span>'
                    f' _{result.get("reason","")}_', unsafe_allow_html=True,
                )
            st.markdown(f"**{result.get('workflow_name','')}** — _{result.get('description','')}_")
            steps = result.get("steps", [])
            for step in steps:
                is_fail = not step.get("success", True)
                with st.expander(
                    f"Step {step['step']} — {step['emoji']} {step['agent']}"
                    + (" ❌" if is_fail else ""),
                    expanded=(step["step"] == len(steps)),
                ):
                    if step.get("intent"):
                        st.markdown(
                            f'<span class="intent-label">intent: {step["intent"]}</span>',
                            unsafe_allow_html=True,
                        )
                    if step.get("tools_used"):
                        st.markdown(
                            "Tools: " + "  ".join(
                                f'<span class="tool-badge">{t}</span>'
                                for t in step["tools_used"]
                            ), unsafe_allow_html=True,
                        )
                    st.markdown(step["output"])
            if result.get("failed_steps"):
                st.warning(f"⚠️ Failed: {', '.join(result['failed_steps'])}. Others completed.")
            es = result.get("execution_summary", {})
            if es:
                st.caption(
                    f"⏱ {es.get('elapsed_sec',0):.1f}s | "
                    f"{es.get('steps_executed',0)} steps"
                )

        else:
            if result.get("auto_routed"):
                st.markdown(
                    f'<span class="route-badge">🧭 Auto → {result.get("routed_to","")}</span>'
                    f' _{result.get("reason","")}_', unsafe_allow_html=True,
                )
            if result.get("intent"):
                st.markdown(
                    f'<span class="intent-label">intent: {result["intent"]}</span>',
                    unsafe_allow_html=True,
                )
            if result.get("tools_used"):
                st.markdown(
                    "Tools: " + "  ".join(
                        f'<span class="tool-badge">{t}</span>'
                        for t in result.get("tools_used", [])
                    ), unsafe_allow_html=True,
                )
            st.markdown(result.get("response", ""))
            es = result.get("execution_summary", {})
            if es:
                st.caption(f"⏱ {es.get('elapsed_sec',0):.1f}s")

    if chat_key not in st.session_state.chat_log:
        st.session_state.chat_log[chat_key] = []
    st.session_state.chat_log[chat_key].append({
        "user":              user_input,
        "type":              result.get("type", "single"),
        "response":          result.get("response", ""),
        "steps":             result.get("steps", []),
        "workflow_name":     result.get("workflow_name", ""),
        "description":       result.get("description", ""),
        "routed_to":         result.get("routed_to", selected),
        "reason":            result.get("reason", ""),
        "tools_used":        result.get("tools_used", []),
        "intent":            result.get("intent", ""),
        "auto_routed":       result.get("auto_routed", False),
        "failed_steps":      result.get("failed_steps", []),
        "execution_summary": result.get("execution_summary", {}),
    })
    st.rerun()
