# ============================================================
# memory/workspace.py — BRAIN AI  (v6)
# ============================================================
# Central shared intelligence for all agents.
# Every agent reads from it before execution.
# Every agent writes to it after execution (via update_memory).
#
# v6 STRUCTURED MEMORY (spec):
#   memory = {
#       "brand_context":        {...},
#       "user_context":         {...},
#       "conversation_history": [],   ← NEW in v6
#       "previous_outputs":     [],
#   }
#
# v6 UPGRADES vs v5:
#   + conversation_history — full session turn log (user + agent)
#   + update_memory()      — single method called after EVERY step
#   + inject_into_prompt() — clean string for system prompt injection
#   + get_memory_state()   — canonical structured dict
# ============================================================

import uuid
from datetime import datetime


class WorkspaceMemory:
    """
    The Brain AI — shared state for the entire session.

    Rules:
      ✅ Every agent calls inject_into_prompt() before executing
      ✅ WorkflowEngine calls update_memory() after every step
      ✅ Memory injected into every system prompt
      ✅ Memory passed across all workflow steps
      ❌ Agents do NOT write to memory directly
    """

    def __init__(self):
        self.session_id = str(uuid.uuid4())[:8]
        self.created_at = datetime.now().isoformat()

        # brand_context
        self.business_context: dict = {
            "company_name":    "",
            "industry":        "",
            "target_audience": "",
            "product_service": "",
            "usp":             "",
            "competitors":     [],
        }

        # user_context / brand voice
        self.brand_tone: dict = {
            "overall_tone":       "",
            "keywords_to_use":    [],
            "keywords_to_avoid":  [],
            "writing_style":      "",
        }

        # conversation_history — v6 explicit requirement
        # [{role: "user"|"agent", agent: str, content: str, timestamp: str}]
        self.conversation_history: list[dict] = []

        # previous_outputs — ordered step-by-step outputs
        # [{agent, content, metadata, step, workflow, timestamp}]
        self.previous_outputs: list[dict] = []

        # Fast lookup: agent_name → latest output record
        self.agent_outputs: dict = {}

        # Supporting
        self.workflow_context: dict = {}
        self.documents:        dict = {}
        self.interaction_log:  list = []

    # ─────────────────────────────────────────────────────────
    # CANONICAL MEMORY STATE
    # ─────────────────────────────────────────────────────────

    def get_memory_state(self) -> dict:
        """
        Returns the full v6 structured memory dict.

            memory = {
                "brand_context":        {...},
                "user_context":         {...},
                "conversation_history": [...],
                "previous_outputs":     [...],
            }
        """
        return {
            "brand_context":        self.business_context,
            "user_context":         self.brand_tone,
            "conversation_history": self.conversation_history,
            "previous_outputs":     self.previous_outputs,
        }

    # ─────────────────────────────────────────────────────────
    # PROMPT INJECTION
    # ─────────────────────────────────────────────────────────

    def inject_into_prompt(self, agent_name: str = "") -> str:
        """
        Builds the Brain AI context string injected into every agent prompt.
        Called by AgentExecutor Step 1 before every execution.

        Sections:
          1. brand_context     — company identity
          2. user_context      — brand voice and tone
          3. conversation_history — last 4 turns (compact)
          4. previous_outputs  — last 5 agent outputs (skip own)
          5. reference docs    — if any uploaded
        """
        sections: list[str] = []

        # 1. brand_context
        ctx  = self.business_context
        biz  = []
        if ctx["company_name"]:    biz.append(f"Company        : {ctx['company_name']}")
        if ctx["industry"]:        biz.append(f"Industry       : {ctx['industry']}")
        if ctx["target_audience"]: biz.append(f"Target audience: {ctx['target_audience']}")
        if ctx["product_service"]: biz.append(f"Product/Service: {ctx['product_service']}")
        if ctx["usp"]:             biz.append(f"USP            : {ctx['usp']}")
        if ctx["competitors"]:     biz.append(f"Competitors    : {', '.join(ctx['competitors'])}")
        if biz:
            sections.append("━━ BRAND CONTEXT ━━\n" + "\n".join(biz))

        # 2. user_context
        tone = self.brand_tone
        tl   = []
        if tone["overall_tone"]:      tl.append(f"Tone   : {tone['overall_tone']}")
        if tone["keywords_to_use"]:   tl.append(f"Use    : {', '.join(tone['keywords_to_use'])}")
        if tone["keywords_to_avoid"]: tl.append(f"Avoid  : {', '.join(tone['keywords_to_avoid'])}")
        if tone["writing_style"]:     tl.append(f"Style  : {tone['writing_style']}")
        if tl:
            sections.append("━━ USER CONTEXT / BRAND VOICE ━━\n" + "\n".join(tl))

        # 3. conversation_history — last 4 turns
        recent = self.conversation_history[-4:]
        if recent:
            lines = []
            for turn in recent:
                label   = f"[{turn.get('agent', 'USER').upper()}]" if turn["role"] == "agent" else "[USER]"
                content = self._cut(turn["content"], 180)
                lines.append(f"{label}: {content}")
            sections.append("━━ RECENT CONVERSATION ━━\n" + "\n".join(lines))

        # 4. previous_outputs — last 5, skip own
        prior = [e for e in self.previous_outputs[-5:] if e.get("agent") != agent_name]
        if prior:
            blocks = []
            for e in prior:
                wf_tag = f" step {e['step']}" if e.get("step") else ""
                label  = f"[{e['agent']}{wf_tag}] ({e['timestamp'][11:19]})"
                blocks.append(f"{label}:\n{self._cut(e['content'], 400)}")
            sections.append(
                "━━ PREVIOUS OUTPUTS (build on these — do not repeat) ━━\n"
                + "\n\n".join(blocks)
            )

        # 5. documents
        if self.documents:
            doc_blocks = [
                f"[{name}]:\n{self._cut(d['content'], 300)}"
                for name, d in self.documents.items()
            ]
            sections.append("━━ REFERENCE DOCUMENTS ━━\n" + "\n\n".join(doc_blocks))

        return "\n\n".join(sections) if sections else ""

    # Backwards-compat alias
    def get_full_context_for_agent(self, agent_name: str = "",
                                   include_prior_outputs: bool = True) -> str:
        return self.inject_into_prompt(agent_name)

    # ─────────────────────────────────────────────────────────
    # update_memory() — SINGLE CALL AFTER EVERY AGENT STEP
    # ─────────────────────────────────────────────────────────

    def update_memory(self, structured_output: dict,
                      step_num: int = 0,
                      workflow_name: str = ""):
        """
        Updates all memory structures from a structured agent output.

        Called by:
          - WorkflowEngine after every step  (workflow mode)
          - AgentExecutor Step 5             (single mode)

        Args:
            structured_output:
                {
                    "agent":    "Copywriter",
                    "content":  "Here is your copy...",
                    "metadata": {"intent": "...", "next_step": "...", ...}
                }
        """
        agent_name = structured_output.get("agent", "unknown")
        content    = structured_output.get("content", "")
        metadata   = structured_output.get("metadata", {})
        task       = metadata.get("task", "")[:80]
        now        = datetime.now().isoformat()

        # previous_outputs list (ordered)
        self.previous_outputs.append({
            "agent":     agent_name,
            "content":   content,
            "metadata":  metadata,
            "task":      task,
            "step":      step_num,
            "workflow":  workflow_name,
            "timestamp": now,
        })

        # agent_outputs dict (fast lookup — latest only)
        version = self.agent_outputs.get(agent_name, {}).get("version", 0) + 1
        self.agent_outputs[agent_name] = {
            "output":    content,
            "task":      task,
            "timestamp": now,
            "step":      step_num,
            "workflow":  workflow_name,
            "version":   version,
            "metadata":  metadata,
        }

        # conversation_history — agent response turn
        self.conversation_history.append({
            "role":      "agent",
            "agent":     agent_name,
            "content":   self._cut(content, 500),
            "timestamp": now,
            "step":      step_num,
            "workflow":  workflow_name,
        })

        # audit
        self.interaction_log.append({
            "type":           "agent_output",
            "agent":          agent_name,
            "task":           task,
            "output_preview": content[:200],
            "timestamp":      now,
        })

    # ─────────────────────────────────────────────────────────
    # USER MESSAGE LOGGING
    # ─────────────────────────────────────────────────────────

    def log_user_message(self, agent_name: str, message: str):
        """Logs user message to conversation_history and interaction_log."""
        now = datetime.now().isoformat()
        self.conversation_history.append({
            "role":      "user",
            "agent":     agent_name,
            "content":   message,
            "timestamp": now,
        })
        self.interaction_log.append({
            "type":      "user_message",
            "agent":     agent_name,
            "content":   message,
            "timestamp": now,
        })

    # ─────────────────────────────────────────────────────────
    # SETTERS
    # ─────────────────────────────────────────────────────────

    def set_business_context(self, **kwargs):
        for k, v in kwargs.items():
            if k in self.business_context:
                self.business_context[k] = v

    def set_brand_tone(self, **kwargs):
        for k, v in kwargs.items():
            if k in self.brand_tone:
                self.brand_tone[k] = v

    def has_business_context(self) -> bool:
        return any(bool(v) for v in self.business_context.values())

    # ─────────────────────────────────────────────────────────
    # FAST LOOKUPS
    # ─────────────────────────────────────────────────────────

    def get_agent_output(self, agent_name: str) -> str:
        entry = self.agent_outputs.get(agent_name)
        return entry["output"] if entry else ""

    def get_agent_structured(self, agent_name: str) -> dict | None:
        return self.agent_outputs.get(agent_name)

    def get_all_agent_outputs_summary(self) -> str:
        if not self.agent_outputs:
            return "No agent outputs yet."
        parts = ["PREVIOUS AGENT OUTPUTS:"]
        for agent, data in self.agent_outputs.items():
            parts.append(f"\n[{agent}] ({data['task'][:50]}):\n{self._cut(data['output'], 300)}")
        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────
    # WORKFLOW CONTEXT
    # ─────────────────────────────────────────────────────────

    def set_workflow_context(self, key: str, value):
        self.workflow_context[key] = value

    def get_workflow_context(self, key: str, default=None):
        return self.workflow_context.get(key, default)

    def clear_workflow_context(self):
        self.workflow_context = {}

    # ─────────────────────────────────────────────────────────
    # DOCUMENTS
    # ─────────────────────────────────────────────────────────

    def add_document(self, name: str, content: str):
        self.documents[name] = {"content": content, "added_at": datetime.now().isoformat()}

    def get_document(self, name: str) -> str:
        doc = self.documents.get(name)
        return doc["content"] if doc else ""

    def list_documents(self) -> list[str]:
        return list(self.documents.keys())

    # ─────────────────────────────────────────────────────────
    # DIAGNOSTICS
    # ─────────────────────────────────────────────────────────

    def memory_summary(self) -> str:
        return (
            f"Session {self.session_id} | "
            f"Agents: {list(self.agent_outputs.keys())} | "
            f"Steps: {len(self.previous_outputs)} | "
            f"Conv turns: {len(self.conversation_history)}"
        )

    def get_session_summary(self) -> dict:
        return {
            "session_id":      self.session_id,
            "created_at":      self.created_at,
            "agents_active":   list(self.agent_outputs.keys()),
            "total_steps":     len(self.previous_outputs),
            "conv_turns":      len(self.conversation_history),
            "has_biz_context": self.has_business_context(),
            "document_count":  len(self.documents),
        }

    # compat
    def get_session_state(self) -> dict:
        return self.get_memory_state()

    def save_agent_output(self, agent_name: str, output: str,
                          task: str = "", step_num: int = 0,
                          workflow_name: str = ""):
        """Backwards-compat wrapper. Prefer update_memory()."""
        self.update_memory(
            structured_output={
                "agent":    agent_name,
                "content":  output,
                "metadata": {"task": task, "success": True},
            },
            step_num=step_num,
            workflow_name=workflow_name,
        )

    def get_recent_log(self, n: int = 10) -> list[dict]:
        return self.interaction_log[-n:]

    @staticmethod
    def _cut(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f" ...[+{len(text)-max_chars}]"
