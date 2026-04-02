# ============================================================
# orchestrator/handler.py — THE ORCHESTRATOR  (v6)
# ============================================================
# ARCHITECTURE:
#
#   User → Orchestrator.handle()
#     → OrchestrationTrace             ← audit every event
#     → ExecutionGuard.run()           ← enforce limits + timeouts
#     → DetectionLayer.decide()        ← explicit, deterministic routing
#         → keyword classifier         ← no LLM (fast path)
#         → LLM router (last resort)   ← validated before use
#     → WorkflowEngine.run()  OR  AgentExecutor.run()
#     → Structured response → app.py
#
# v6 UPGRADES vs v5:
#   + Orchestrator explicitly injects memory into execution context
#     (memory_state logged + passed to trace for transparency)
#   + Memory state logged at start + end of every request
#   + Cleaner DecisionLayer with simpler, more readable routing
#   + LLM vs OpenClaw selection explicitly controlled here
#   + Error responses include error_type for UI differentiation
#
# RULES:
#   ✅ Single entry point — Orchestrator.handle() only
#   ✅ Intent classifier fires before any LLM call
#   ✅ Orchestrator controls execution path (LLM vs OpenClaw)
#   ✅ Memory injected into every execution (via workspace)
#   ✅ All decisions logged to OrchestrationTrace
#   ❌ No autonomous agents
#   ❌ No LangChain as controller
#   ❌ No recursive loops
# ============================================================

import logging
from datetime import datetime

from agents.configs import AGENTS
from agents.executor import AgentExecutor
from workflow.engine import WorkflowEngine, WORKFLOWS
from llm.llm_client import run_router_llm
from orchestrator.intent_classifier import IntentClassifier, classify_full
from orchestrator.execution_control import (
    ExecutionGuard, ExecutionConfig,
    ExecutionError, StepLimitError, TimeoutError,
)

logger = logging.getLogger(__name__)


# ── ORCHESTRATION TRACE ───────────────────────────────────────

class OrchestrationTrace:
    """Immutable audit log of every orchestrator decision."""

    def __init__(self):
        self.entries: list[dict] = []

    def log(self, event: str, details: dict = None):
        entry = {
            "event":     event,
            "details":   details or {},
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        }
        self.entries.append(entry)
        logger.info(f"[ORCH] {event}: {details}")

    def get(self) -> list[dict]:
        return list(self.entries)

    def clear(self):
        self.entries = []


# ── DECISION LAYER ────────────────────────────────────────────

class DecisionLayer:
    """
    Explicit, deterministic routing.

    Priority:
      1. Explicit workflow  (mode="workflow:X")
      2. Explicit single   (mode="single")
      3. Auto-route:
           a. Keyword classifier → workflow
           b. Keyword classifier → agent hint
           c. LLM router (last resort, validated)
    """

    def __init__(self, trace: OrchestrationTrace):
        self.trace      = trace
        self.classifier = IntentClassifier()

    def decide(self, user_input: str, agent_name: str,
               mode: str) -> tuple[str, str, str]:
        """
        Returns (exec_type, target, explanation).
          exec_type : "workflow" | "single" | "error"
          target    : workflow_key | agent_name | error_message
          explanation : human-readable reason
        """

        # 1. Explicit workflow
        if mode.startswith("workflow:"):
            wf_key = mode.split(":", 1)[1]
            if wf_key not in WORKFLOWS:
                msg = f"Workflow '{wf_key}' not registered."
                self.trace.log("DECISION_ERROR", {"reason": msg})
                return "error", msg, msg
            self.trace.log("DECISION", {"path": "explicit_workflow", "target": wf_key})
            return "workflow", wf_key, f"Explicit workflow '{wf_key}'."

        # 2. Explicit single agent
        if mode == "single":
            if agent_name not in AGENTS:
                msg = f"Agent '{agent_name}' not configured."
                self.trace.log("DECISION_ERROR", {"reason": msg})
                return "error", msg, msg
            self.trace.log("DECISION", {"path": "explicit_single", "target": agent_name})
            return "single", agent_name, f"Explicit agent '{agent_name}'."

        # 3. Auto-route
        if mode == "auto":
            # 3a. Keyword classifier — workflow
            intent = self.classifier.classify(user_input)
            self.trace.log("INTENT_CLASSIFICATION", {
                "workflow":   intent.workflow,
                "confidence": intent.confidence,
                "matched_on": intent.matched_on[:3],
            })

            if intent.workflow:
                self.trace.log("DECISION", {
                    "path":   "classifier → workflow",
                    "target": intent.workflow,
                })
                return "workflow", intent.workflow, intent.explanation

            # 3b. Keyword classifier — agent hint
            agent_hint = self.classifier.suggest_agent(user_input)
            if agent_hint:
                self.trace.log("DECISION", {
                    "path": "classifier → agent_hint", "target": agent_hint,
                })
                return "single", agent_hint, f"Keyword match → '{agent_hint}'."

            # 3c. LLM router — last resort
            self.trace.log("LLM_ROUTER_START", {
                "reason": "No keyword match. Calling LLM router."
            })
            routing = run_router_llm(user_input, list(AGENTS.keys()))
            self.trace.log("LLM_ROUTER_RESULT", {
                "mode":     routing.get("mode"),
                "agent":    routing.get("agent", ""),
                "workflow": routing.get("workflow", ""),
                "reason":   routing.get("reason", ""),
            })

            if routing.get("mode") == "workflow":
                wf_key = routing.get("workflow", "")
                if wf_key in WORKFLOWS:
                    return "workflow", wf_key, routing.get("reason", "LLM router.")
                self.trace.log("LLM_ROUTER_FALLBACK", {
                    "reason": f"Unknown workflow '{wf_key}'. Falling back to agent."
                })

            agent = routing.get("agent", "Virtual Assistant")
            if agent not in AGENTS:
                agent = "Virtual Assistant"
            return "single", agent, routing.get("reason", "LLM router.")

        # Unknown mode — safe fallback
        fallback = agent_name if agent_name in AGENTS else "Virtual Assistant"
        self.trace.log("UNKNOWN_MODE_FALLBACK", {"mode": mode, "fallback": fallback})
        return "single", fallback, f"Unknown mode '{mode}'. Fallback to '{fallback}'."


# ── ORCHESTRATOR ──────────────────────────────────────────────

class Orchestrator:
    """
    The central brain. ONLY entry point for all user requests.

    Responsibilities:
      - Classify intent (keyword first, LLM last)
      - Control execution path (LLM vs OpenClaw) via config
      - Inject shared memory into every execution (via workspace)
      - Enforce execution limits (steps, timeout)
      - Return structured response to UI

    Agents do NOT coordinate.
    Coordination happens ONLY via orchestrator + workflow engine + workspace.
    """

    def __init__(self, workspace, conversation_memory,
                 config: ExecutionConfig = None):
        self.workspace   = workspace
        self.conv_memory = conversation_memory
        self.config      = config or ExecutionConfig()
        self.trace       = OrchestrationTrace()

        self.executor = AgentExecutor(workspace, conversation_memory)
        self.workflow  = WorkflowEngine(workspace, conversation_memory)
        self.decision  = DecisionLayer(self.trace)

    # ─────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────

    def handle(self, user_input: str, agent_name: str,
               mode: str = "single") -> dict:
        """
        Single entry point for ALL user requests.

        Args:
            user_input  : raw user message
            agent_name  : agent selected in UI
            mode        : "single" | "auto" | "workflow:<key>"

        Returns:
            Structured response dict for app.py
        """
        self.trace.clear()
        self.trace.log("REQUEST_RECEIVED", {
            "mode":  mode,
            "agent": agent_name,
            "input": user_input[:100],
        })

        # Log user message to Brain AI conversation_history
        self.workspace.log_user_message(agent_name, user_input)

        # Log current memory state for transparency
        self.trace.log("MEMORY_STATE", {
            "summary": self.workspace.memory_summary(),
        })

        guard = ExecutionGuard(self.config)

        with guard.run():
            try:
                # ── DECISION ──────────────────────────────────
                exec_type, target, explanation = self.decision.decide(
                    user_input, agent_name, mode
                )

                if exec_type == "error":
                    return self._error_response(target)

                is_auto = (mode == "auto")

                # ── EXECUTION ─────────────────────────────────
                if exec_type == "workflow":
                    result = self._run_workflow(
                        user_input, target, guard, is_auto, explanation
                    )
                else:
                    result = self._run_single_agent(
                        user_input, target, guard, is_auto, explanation
                    )

                # Attach execution summary
                result["execution_summary"] = guard.get_summary()

                # Log final memory state
                self.trace.log("MEMORY_UPDATED", {
                    "agents_active": list(self.workspace.agent_outputs.keys()),
                    "total_steps":   len(self.workspace.previous_outputs),
                    "conv_turns":    len(self.workspace.conversation_history),
                })

                return result

            except StepLimitError as e:
                self.trace.log("STEP_LIMIT_EXCEEDED", {"limit": self.config.max_steps})
                return self._error_response(str(e), "step_limit")

            except TimeoutError as e:
                self.trace.log("TIMEOUT", {"elapsed": guard.elapsed()})
                return self._error_response(str(e), "timeout")

            except ExecutionError as e:
                self.trace.log("EXECUTION_ERROR", {"error": str(e)})
                return self._error_response(str(e))

            except Exception as e:
                logger.error(f"[ORCHESTRATOR] Unexpected: {e}", exc_info=True)
                return self._error_response(f"Unexpected error: {str(e)}")

    # ─────────────────────────────────────────────────────────
    # EXECUTION PATHS
    # ─────────────────────────────────────────────────────────

    def _run_single_agent(self, user_input: str, agent_name: str,
                          guard: ExecutionGuard, auto_routed: bool,
                          explanation: str) -> dict:
        """Single agent through full 5-step AgentExecutor pipeline."""
        config = AGENTS.get(agent_name)
        if not config:
            return self._error_response(f"Agent '{agent_name}' not configured.")

        step_num = guard.step(agent_name)
        self.trace.log("EXECUTING_SINGLE_AGENT", {
            "agent":      agent_name,
            "step_num":   step_num,
            "openclaw":   config.get("use_openclaw", False),
        })

        try:
            result = guard.timed_step(
                self.executor.run,
                agent_name=agent_name,
                step_num=step_num,
                config=config,
                user_message=user_input,
                workflow_input="",
            )
        except TimeoutError as e:
            return self._error_response(str(e), "step_timeout")

        self.trace.log("SINGLE_AGENT_DONE", {
            "agent":    agent_name,
            "intent":   result["metadata"].get("intent", ""),
            "tools":    result["metadata"].get("tools_used", []),
            "success":  result["metadata"].get("success", False),
        })

        if not result["metadata"].get("success", True):
            return self._error_response(
                result["metadata"].get("error", "Agent failed.")
            )

        return {
            "type":        "single",
            "agent":       agent_name,
            "emoji":       result["metadata"].get("emoji", "🤖"),
            "response":    result["content"],
            "tools_used":  result["metadata"].get("tools_used", []),
            "intent":      result["metadata"].get("intent", ""),
            "routed_to":   agent_name,
            "auto_routed": auto_routed,
            "reason":      explanation,
            "steps":       [],
            "trace":       self.trace.get(),
        }

    def _run_workflow(self, user_input: str, workflow_name: str,
                      guard: ExecutionGuard, auto_routed: bool,
                      explanation: str) -> dict:
        """Multi-agent workflow via WorkflowEngine."""
        self.trace.log("EXECUTING_WORKFLOW", {"workflow": workflow_name})

        result = self.workflow.run(
            workflow_name=workflow_name,
            user_task=user_input,
            guard=guard,
        )

        self.trace.log("WORKFLOW_DONE", {
            "workflow":     workflow_name,
            "steps":        len(result.get("steps", [])),
            "failed_steps": result.get("failed_steps", []),
            "success":      result.get("success", False),
        })

        if not result.get("success"):
            return self._error_response(result.get("error", "Workflow failed."))

        return {
            "type":          "workflow",
            "workflow_name": result["workflow_name"],
            "description":   result["description"],
            "steps":         result["steps"],
            "response":      result["final_output"],
            "routed_to":     workflow_name,
            "auto_routed":   auto_routed,
            "reason":        explanation,
            "tools_used":    [],
            "failed_steps":  result.get("failed_steps", []),
            "trace":         self.trace.get(),
        }

    # ─────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────

    def get_trace(self) -> list[dict]:
        return self.trace.get()

    def _error_response(self, message: str,
                        error_type: str = "execution_error") -> dict:
        self.trace.log("ERROR", {"message": message, "type": error_type})
        return {
            "type":             "error",
            "error_type":       error_type,
            "response":         f"❌ {message}",
            "steps":            [],
            "reason":           message,
            "execution_summary": {},
            "trace":            self.trace.get(),
        }
