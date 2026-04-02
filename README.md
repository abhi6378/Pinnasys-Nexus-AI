# AI Workforce v6 — Stable Production-Ready Multi-Agent Platform

Upgraded from v5. Focused on stability, clarity, structured memory, and deterministic workflows.

## ⚡ Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env     # add OPENAI_API_KEY
streamlit run app.py
```

---

## 🧠 v6 Memory Structure (Brain AI)

```python
memory = {
    "brand_context":        {...},   # company identity
    "user_context":         {...},   # brand voice, tone, keywords
    "conversation_history": [...],   # NEW: all user + agent turns in order
    "previous_outputs":     [...],   # all per-step structured outputs
}
```

Access via: `workspace.get_memory_state()`

### Key methods (v6)

| Method | Called by | Purpose |
|--------|-----------|---------|
| `inject_into_prompt(agent_name)` | AgentExecutor Step 1 | Build Brain AI string for system prompt |
| `update_memory(structured_output)` | AgentExecutor Step 5 + WorkflowEngine | Update all 4 memory fields after each step |
| `log_user_message(agent, msg)` | Orchestrator | Add user turn to conversation_history |
| `get_memory_state()` | UI, orchestrator | Full structured memory dict |

---

## 📋 Structured Agent Output (v6)

Every agent returns:
```python
{
    "agent":   "Copywriter",
    "content": "Here is your LinkedIn post...",
    "metadata": {
        "intent":        "create_content",    # agent's intent label
        "next_step":     "SEO Specialist",    # hint for next agent
        "tools_used":    ["mock_search_web"],
        "tool_results":  {...},
        "task":          "Write a LinkedIn...",
        "step":          1,
        "workflow":      "content_pipeline",
        "timestamp":     "...",
        "tokens_approx": 420,
        "success":       True,
        "error":         "",
    }
}
```

No raw text passing between agents — always structured dicts.

---

## 🔗 Workflow Engine — update_memory() after every step

```python
# Exactly as described in spec:
def run_workflow(workflow_name, user_task):
    step1 = run_agent("Copywriter", user_task)
    update_memory(step1)                          # ← called after every step

    step2 = run_agent("SEO Specialist", step1["content"])
    update_memory(step2)

    step3 = run_agent("Social Media Manager", step2["content"])
    update_memory(step3)

    return step3
```

Structured outputs chained between steps — `step1["content"]` passed as `workflow_input` to step 2.

---

## 🔧 Tool Execution — Three Trigger Modes

```python
# Mode 1: auto_tools — always run (declared in agent config)
auto_tools = ["mock_read_sheet", "mock_query_database"]

# Mode 2: intent_tools — triggered by agent's intent label (v6)
INTENT_TOOL_MAP = {
    "write_email":  ["mock_send_email"],     # intent → tool
    "seo_optimize": ["mock_search_web"],
    "analyze_data": ["mock_read_sheet", "mock_query_database"],
}

# Mode 3: keyword_tools — triggered by task text keywords (spec)
KEYWORD_TOOL_MAP = [
    ("send email",  "mock_send_email"),
    ("search for",  "mock_search_web"),
    ...
]
```

---

## 🎯 Intent Classifier (v6 — improved)

Uses simple `any()` pattern as described in spec:
```python
def detect_workflow(input):
    keywords = ["campaign", "strategy", "plan", "launch"]
    if any(k in input.lower() for k in keywords):
        return "marketing"
    return None
```

Extended with per-workflow keyword groups + optional regex for precision.

---

## 📁 Files Changed in v6 vs v5

| File | Change |
|------|--------|
| `memory/workspace.py` | + `conversation_history`, `update_memory()`, `inject_into_prompt()`, `get_memory_state()` |
| `agents/executor.py` | + `inject_into_prompt()` in Step 1, `update_memory()` in Step 5, intent detection, `next_step` hint, 3 tool trigger modes |
| `workflow/engine.py` | + structured output chaining, `update_memory()` log after each step, `next_step_hint` per step |
| `tools/tool_registry.py` | + `INTENT_TOOL_MAP`, `execute_intent_tools()`, `detect_intent_tools()` |
| `orchestrator/intent_classifier.py` | + `any()` keyword groups, per-workflow keyword lists, cleaner API |
| `orchestrator/handler.py` | + memory state logged at start/end, cleaner decision layer, `intent` in response |
| `app.py` | + Memory State panel (conversation_history + previous_outputs), intent badges on steps |

**Unchanged from v5:** `agents/configs.py`, `llm/llm_client.py`, `llm/execution_layer.py`, `orchestrator/execution_control.py`, `memory/conversation.py`, `openclaw/client.py`
