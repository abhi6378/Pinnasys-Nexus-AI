# AI Workforce v2 — Production Multi-Agent System

A Sintra.ai-like multi-agent platform with shared memory, workflow engine,
LangChain integration, and OpenClaw runtime support.

---

## Quick Start (3 commands)

```bash
pip install -r requirements.txt
cp .env.example .env        # paste your OpenAI key inside
streamlit run app.py
```

Open http://localhost:8501

---

## Architecture

```
sintra_v2/
│
├── app.py                        ← Streamlit UI (3 execution modes)
│
├── orchestrator/
│   └── handler.py                ← THE BRAIN — routes all requests
│
├── agents/
│   └── configs.py                ← All 12 agent definitions (config-driven)
│
├── workflow/
│   └── engine.py                 ← Multi-agent chaining (Workflow Engine)
│
├── memory/
│   ├── workspace.py              ← Shared Brain AI (all agents read/write here)
│   └── conversation.py          ← Per-agent conversation history
│
├── llm/
│   └── llm_client.py            ← LangChain abstraction over OpenAI
│
├── tools/
│   └── tool_registry.py         ← All tools + registry + call_tool()
│
└── openclaw/
    └── client.py                ← HTTP client for OpenClaw gateway (Phase 3)
```

---

## 3 Execution Modes

### 1. Single Agent
User picks one agent from the sidebar. Orchestrator calls that agent directly.
Business context from shared workspace is automatically injected.

### 2. Workflow (Multi-Agent Chain)
**Content Pipeline**: Copywriter → SEO Specialist → Social Media Manager
Each agent receives the previous agent's output as input. All outputs are
saved to shared workspace memory.

**Research & Write**: Data Analyst → Copywriter
Data insights are turned into polished content automatically.

### 3. Auto-Route
The orchestrator uses GPT to read the user's request and decide whether
to use a single agent or a full workflow — no manual selection needed.

---

## Shared Workspace Memory (Brain AI)

Set your business context in the sidebar (Company name, Industry, USP, etc.).
This is stored in `WorkspaceMemory` and automatically injected into EVERY
agent's system prompt. Every agent knows about your business without you
having to repeat yourself.

---

## Adding a New Agent

In `agents/configs.py`, add one entry:
```python
"New Agent Name": {
    "name":         "New Agent Name",
    "role":         "You are...",
    "tone":         "...",
    "allowed_tools": ["save_to_workspace", "read_from_workspace"],
    "use_openclaw": False,
    "can_chain":    True,
    "emoji":        "🎯",
}
```
No other file changes needed.

---

## Adding a New Workflow

In `workflow/engine.py`, add one entry to `WORKFLOWS`:
```python
"my_workflow": {
    "name":        "My Workflow",
    "description": "Step 1 → Step 2",
    "steps":       ["Agent One", "Agent Two"],
    "emoji":       "⚡",
}
```
Agents must have `can_chain: True`.

---

## Enabling OpenClaw (Phase 3)

1. `openclaw gateway start`
2. In `agents/configs.py` → set `"use_openclaw": True` for Customer Support + Data Analyst
3. In `orchestrator/handler.py` → OpenClaw path is already wired in

---

## Build Phases

| Phase | Feature                        | Status |
|-------|--------------------------------|--------|
| 1     | 1 agent, GPT direct            | ✅ Done |
| 2     | All 12 agents + shared memory  | ✅ Done |
| 3     | Workflow engine                | ✅ Done |
| 4     | Auto-routing (LLM router)      | ✅ Done |
| 5     | OpenClaw for 2 agents          | 🔜 Next |
| 6     | Real tools via Composio        | 🔜 Next |
