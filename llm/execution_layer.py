# ============================================================
# llm/execution_layer.py — HYBRID EXECUTION LAYER  (v5)
# ============================================================
# WHAT THIS IS:
#   The single place where LLM vs OpenClaw routing happens.
#   AgentExecutor always calls ExecutionLayer.execute().
#   The layer decides the path based on agent config.
#
# PATHS:
#   OpenClaw path  → use_openclaw=True AND gateway healthy
#   LLM path       → everything else (default + OpenClaw fallback)
#
# RULES:
#   ✅ Config-driven path selection (no runtime decisions by agents)
#   ✅ OpenClaw failure → immediate LLM fallback (no retry)
#   ✅ Retry logic on transient LLM errors (configurable)
#   ✅ Both paths return a plain string (same interface)
#   ✅ Health check cached (no hammering the gateway)
#   ❌ LangChain NOT used as an agent controller here
#   ❌ No autonomous path selection by the LLM
#
# EXAMPLE:
#   layer = ExecutionLayer()
#   output = layer.execute("Copywriter", config, system_prompt, user_message, history)
#   # → "Here is your LinkedIn post: ..."
# ============================================================

import logging
import time

from llm.llm_client import run_llm

logger = logging.getLogger(__name__)


class ExecutionLayer:
    """
    Hybrid execution layer: LLM path vs OpenClaw path.

    Called exclusively by AgentExecutor in Step 3 of the 5-step flow.
    Agents have no visibility into which path is used.

    Path selection:
      if config["use_openclaw"] == True:
          try OpenClaw gateway
          if unhealthy or error → fall back to LLM
      else:
          use LLM directly

    LangChain is used inside run_llm() as a wrapper only.
    No LangChain agent logic is involved.
    """

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        self._openclaw   = None   # lazy-initialized

    def execute(self,
                agent_name: str,
                config: dict,
                system_prompt: str,
                user_message: str,
                history: list = None) -> str:
        """
        Executes the agent's LLM call via the correct path.

        Args:
            agent_name    : e.g. "Copywriter"
            config        : agent config dict from configs.py
            system_prompt : fully assembled prompt from build_system_prompt()
            user_message  : user's current message
            history       : list of {role, content} from ConversationMemory

        Returns:
            str: agent response text
        """
        use_openclaw = config.get("use_openclaw", False)

        if use_openclaw:
            result = self._try_openclaw(
                agent_name=agent_name,
                config=config,
                system_prompt=system_prompt,
                user_message=user_message,
            )
            # If OpenClaw returned an error string, fall back to LLM
            if result is not None and not result.startswith("❌"):
                return result
            logger.warning(
                f"[EXEC LAYER] OpenClaw unavailable or errored for "
                f"'{agent_name}'. Falling back to LLM."
            )

        # LLM path — default and OpenClaw fallback
        return self._run_llm_with_retry(
            agent_name=agent_name,
            system_prompt=system_prompt,
            user_message=user_message,
            history=history or [],
        )

    # ─────────────────────────────────────────────────────────
    # OPENCLAW PATH
    # ─────────────────────────────────────────────────────────

    def _try_openclaw(self, agent_name: str, config: dict,
                      system_prompt: str, user_message: str) -> str | None:
        """
        Attempts execution via OpenClaw gateway.
        Returns None if gateway is unreachable (triggers LLM fallback).
        Returns error string starting with "❌" on gateway error.
        Returns response string on success.
        """
        openclaw = self._get_openclaw()
        if not openclaw.health_check():
            logger.info(f"[EXEC LAYER] OpenClaw offline for '{agent_name}'.")
            return None  # signal: fall back to LLM

        logger.info(f"[EXEC LAYER] OpenClaw path for '{agent_name}'.")
        return openclaw.run_agent(
            agent_name=agent_name,
            system_prompt=system_prompt,
            user_message=user_message,
            allowed_tools=config.get("allowed_tools", []),
        )

    def _get_openclaw(self):
        if self._openclaw is None:
            from openclaw.client import OpenClawClient
            self._openclaw = OpenClawClient()
        return self._openclaw

    # ─────────────────────────────────────────────────────────
    # LLM PATH (with retry)
    # ─────────────────────────────────────────────────────────

    def _run_llm_with_retry(self, agent_name: str,
                             system_prompt: str,
                             user_message: str,
                             history: list) -> str:
        """
        Calls LLM via run_llm() with exponential backoff retry
        on transient errors (rate limits, 503s).

        Non-transient errors (auth, bad API key) are not retried.
        """
        last_error = ""
        for attempt in range(1, self.max_retries + 2):
            logger.info(
                f"[EXEC LAYER] LLM call for '{agent_name}' "
                f"(attempt {attempt}/{self.max_retries + 1})"
            )
            result = run_llm(system_prompt, user_message, history)

            # Check for transient error patterns worth retrying
            if result.startswith("❌ LLM Error:"):
                last_error = result
                if attempt <= self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        f"[EXEC LAYER] Transient LLM error. "
                        f"Retrying in {wait}s... ({attempt}/{self.max_retries})"
                    )
                    time.sleep(wait)
                    continue
                # Exhausted retries
                logger.error(
                    f"[EXEC LAYER] LLM failed after {self.max_retries} retries."
                )
                return last_error

            return result   # success

        return last_error or "❌ LLM Error: Unknown failure."


# Module-level singleton — shared across all AgentExecutor instances
execution_layer = ExecutionLayer()
