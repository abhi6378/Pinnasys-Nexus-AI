# 🧠 Sintra Clone — AI Workforce Platform

A fully functional Sintra.ai-inspired platform with 12 AI helpers, Brain AI memory, multi-step workflows, and an Ideas Inbox. Powered by **GPT-4o-mini**.

---

## 🚀 Quick Start

### 1. Clone / unzip the project
```bash
cd sintra_clone
```

### 2. Create virtual environment
```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set your OpenAI API key
```bash
cp .env.example .env
# Edit .env and add your key:
# OPENAI_API_KEY=sk-...
```

### 5. Run the app
```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## 🏗️ Architecture

```
User
  ↓
Streamlit UI (app.py)
  ↓
Orchestrator (orchestrator/handler.py)
  ↓
Brain AI (brain/brain_ai.py)     ← shared memory
  ↓
Single Helper OR Workflow Engine
  ↓
LLM Layer (GPT-4o-mini)
  ↓
Structured Output → UI + Brain AI update
```

---

## 🤖 The 12 AI Helpers

| Helper | Name | Role |
|--------|------|------|
| copywriter | Penn | AI Copywriter |
| seo | Seomi | SEO Specialist |
| social_media | Soshie | Social Media Manager |
| support | Cassie | Customer Support |
| sales | Milli | Sales Assistant |
| strategist | Strat | Business Strategist |
| data_analyst | Dexter | Data Analyst |
| assistant | Buddy | Virtual Assistant |
| recruiter | Remy | Recruitment Specialist |
| email_marketer | Emmie | Email Marketing |
| designer_advisor | Vizzy | Design Advisor |
| finance_advisor | Finn | Finance Advisor |

---

## ⚙️ Workflows

| Workflow | Agents Chained |
|----------|---------------|
| Marketing Campaign | Copywriter → SEO → Social Media |
| Content Creation | Copywriter → SEO |
| Sales Outreach | Sales → Email Marketer |
| Support Setup | Support → Copywriter |
| Business Strategy | Strategist → Data Analyst |

---

## 📁 Project Structure

```
sintra_clone/
├── app.py                      ← Streamlit entry point
├── requirements.txt
├── .env.example
├── api/
│   └── routes.py               ← FastAPI REST API
├── orchestrator/
│   └── handler.py              ← Central brain / router
├── brain/
│   ├── brain_ai.py             ← Memory + context retrieval
│   ├── quiz_engine.py          ← Adaptive onboarding quiz
│   └── memory_extractor.py     ← Auto-learns from outputs
├── helpers/
│   ├── configs.py              ← All 12 agent definitions
│   └── executor.py             ← Runs a single helper
├── workflows/
│   └── engine.py               ← All multi-step workflows
├── storage/
│   ├── db.py                   ← SQLAlchemy models
│   └── repositories.py         ← All CRUD functions
├── workspace/
│   └── manager.py              ← Workspace lifecycle
├── llm/
│   └── client.py               ← GPT-4o-mini wrapper
├── ui/
│   ├── sidebar.py              ← Navigation sidebar
│   └── pages/
│       ├── onboarding_page.py  ← First-run setup
│       ├── chat_page.py        ← Main chat UI
│       ├── brain_page.py       ← Brain AI manager
│       ├── helpers_page.py     ← Browse all helpers
│       ├── ideas_page.py       ← Ideas Inbox
│       └── workflows_page.py   ← Workflow launcher
```

---

## 🔌 REST API (Optional)

Run the FastAPI backend separately:

```bash
uvicorn api.routes:app --reload
```

Available at: http://localhost:8000/docs

Key endpoints:
- `POST /workspace/create`
- `POST /chat`
- `GET  /workspace/{id}/brain`
- `POST /brain/add-knowledge`
- `GET  /workspace/{id}/ideas`
- `POST /ideas/{id}/accept`

---

## 💡 Key Features

- **Brain AI** — Shared memory injected into every helper prompt
- **Adaptive Quiz** — Fills Brain AI gaps through guided questions
- **Auto-memory extraction** — Helpers learn from every conversation
- **Auto-routing** — Orchestrator picks the right helper or workflow
- **Ideas Inbox** — Agents proactively surface opportunities
- **Workflow traces** — See every step a workflow took
- **Multiple workspaces** — Isolated environments per project/client

---

## 🛠️ Customization

**Add a new helper:** Edit `helpers/configs.py` and add to the `AGENTS` dict.

**Add a new workflow:** Edit `workflows/engine.py`, add a function and register it in `WORKFLOWS`.

**Change LLM:** Edit `llm/client.py` — swap the model string or replace with Anthropic/Gemini.
