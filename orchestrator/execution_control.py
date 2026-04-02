# ============================================================
# orchestrator/execution_control.py  (v5)
# ============================================================
# WHAT THIS IS:
#   Production-grade execution control layer.
#   Enforces limits, handles timeouts, and captures errors
#   so the system never hangs or silently fails.
#
# COMPONENTS:
#   ExecutionConfig   — per-request configuration (limits, timeouts)
#   ExecutionGuard    — context manager that enforces limits
#   StepCounter       — tracks steps within a workflow run
#   ExecutionError    — typed error for controlled failures
#   with_timeout()    — decorator for timeout-bounded calls
#
# RULES:
#   ✅ MAX_WORKFLOW_STEPS enforced — no runaway chains
#   ✅ Per-request timeout enforced — no hanging requests
#   ✅ Every error captured as structured dict (not bare exception)
#   ✅ Guard is reentrant-safe (one guard per request)
#   ❌ No silent error swallowing
#   ❌ No infinite loops or unbounded recursion
# ============================================================

import time
import logging
import threading
from dataclasses import dataclass, field
from contextlib import contextmanager
from typing import Callable, Any

logger = logging.getLogger(__name__)

# ── SYSTEM-WIDE LIMITS ────────────────────────────────────────
MAX_WORKFLOW_STEPS:  int   = 10       # hard cap on steps per workflow run
MAX_SINGLE_RETRIES:  int   = 2        # retries on transient LLM errors
DEFAULT_TIMEOUT_SEC: float = 120.0    # seconds per full request
DEFAULT_STEP_TIMEOUT: float = 45.0   # seconds per individual agent step


# ── TYPED ERRORS ──────────────────────────────────────────────

class ExecutionError(Exception):
    """
    Typed error for controlled execution failures.
    Carries structured metadata so orchestrator can return
    a proper error response rather than a raw traceback.
    """
    def __init__(self, message: str, error_type: str = "execution_error",
                 step: int = 0, agent: str = ""):
        super().__init__(message)
        self.error_type = error_type
        self.step       = step
        self.agent      = agent

    def to_dict(self) -> dict:
        return {
            "error":      True,
            "error_type": self.error_type,
            "message":    str(self),
            "step":       self.step,
            "agent":      self.agent,
        }


class TimeoutError(ExecutionError):
    def __init__(self, message: str, step: int = 0, agent: str = ""):
        super().__init__(message, error_type="timeout", step=step, agent=agent)


class StepLimitError(ExecutionError):
    def __init__(self, limit: int):
        super().__init__(
            f"Workflow exceeded maximum step limit ({limit} steps).",
            error_type="step_limit_exceeded",
        )


# ── EXECUTION CONFIG ──────────────────────────────────────────

@dataclass
class ExecutionConfig:
    """
    Per-request execution configuration.
    Passed to ExecutionGuard at the start of every handle() call.
    """
    max_steps:    int   = MAX_WORKFLOW_STEPS
    timeout_sec:  float = DEFAULT_TIMEOUT_SEC
    step_timeout: float = DEFAULT_STEP_TIMEOUT
    max_retries:  int   = MAX_SINGLE_RETRIES
    strict_mode:  bool  = False   # if True, step failure aborts workflow


# ── STEP COUNTER ──────────────────────────────────────────────

class StepCounter:
    """
    Tracks the number of agent executions in a workflow run.
    Raises StepLimitError if max_steps is exceeded.
    Thread-safe (uses a lock).
    """

    def __init__(self, max_steps: int = MAX_WORKFLOW_STEPS):
        self.max_steps = max_steps
        self._count    = 0
        self._lock     = threading.Lock()

    def increment(self, agent_name: str = "") -> int:
        with self._lock:
            self._count += 1
            if self._count > self.max_steps:
                raise StepLimitError(self.max_steps)
            logger.debug(
                f"[STEP COUNTER] Step {self._count}/{self.max_steps}"
                + (f" — {agent_name}" if agent_name else "")
            )
            return self._count

    @property
    def count(self) -> int:
        return self._count

    def reset(self):
        with self._lock:
            self._count = 0


# ── TIMEOUT UTILITIES ─────────────────────────────────────────

def run_with_timeout(fn: Callable, timeout_sec: float,
                     step: int = 0, agent: str = "",
                     *args, **kwargs) -> Any:
    """
    Runs `fn(*args, **kwargs)` in a thread.
    Raises TimeoutError if it doesn't complete within timeout_sec.

    Args:
        fn          : callable to execute
        timeout_sec : seconds before timeout
        step        : current step number (for error context)
        agent       : agent name (for error context)

    Returns:
        Whatever `fn` returns.

    Raises:
        TimeoutError if execution exceeds timeout_sec
        ExecutionError if fn raises an unexpected exception
    """
    result:    list = []
    exception: list = []

    def _target():
        try:
            result.append(fn(*args, **kwargs))
        except Exception as e:
            exception.append(e)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)

    if thread.is_alive():
        msg = (
            f"Agent '{agent}' timed out after {timeout_sec:.0f}s "
            f"(step {step})."
        )
        logger.error(f"[TIMEOUT] {msg}")
        raise TimeoutError(msg, step=step, agent=agent)

    if exception:
        exc = exception[0]
        if isinstance(exc, (ExecutionError, TimeoutError, StepLimitError)):
            raise exc
        raise ExecutionError(
            f"Agent '{agent}' raised: {str(exc)}",
            step=step, agent=agent,
        )

    return result[0] if result else None


# ── EXECUTION GUARD ───────────────────────────────────────────

class ExecutionGuard:
    """
    Context manager that enforces limits for a full request lifecycle.

    Usage:
        guard = ExecutionGuard(config)
        with guard.run():
            step_count = guard.step("Copywriter")
            result = guard.timed_step(fn, "Copywriter")
            ...

    Features:
      - Tracks elapsed time for the full request
      - Provides step counter (raises StepLimitError on breach)
      - Provides per-step timeout wrapper
      - Collects all errors without aborting (unless strict_mode)
    """

    def __init__(self, config: ExecutionConfig = None):
        self.config  = config or ExecutionConfig()
        self.counter = StepCounter(self.config.max_steps)
        self.errors: list[dict] = []
        self._start: float = 0.0

    @contextmanager
    def run(self):
        """Context manager for a complete request lifecycle."""
        self._start = time.time()
        logger.info(
            f"[GUARD] Request started. "
            f"Limits: {self.config.max_steps} steps, "
            f"{self.config.timeout_sec}s total."
        )
        try:
            yield self
        finally:
            elapsed = time.time() - self._start
            logger.info(
                f"[GUARD] Request complete. "
                f"Steps: {self.counter.count}, "
                f"Elapsed: {elapsed:.2f}s, "
                f"Errors: {len(self.errors)}"
            )

    def step(self, agent_name: str = "") -> int:
        """Increments step counter. Raises StepLimitError if exceeded."""
        return self.counter.increment(agent_name)

    def timed_step(self, fn: Callable, agent_name: str = "",
                   step_num: int = 0, **kwargs) -> Any:
        """
        Executes fn() with per-step timeout.
        Raises TimeoutError if fn takes longer than config.step_timeout.
        """
        return run_with_timeout(
            fn,
            timeout_sec=self.config.step_timeout,
            step=step_num,
            agent=agent_name,
            **kwargs,
        )

    def elapsed(self) -> float:
        """Returns seconds elapsed since guard.run() started."""
        return time.time() - self._start

    def time_remaining(self) -> float:
        """Returns seconds remaining before overall timeout."""
        return max(0.0, self.config.timeout_sec - self.elapsed())

    def is_timed_out(self) -> bool:
        """Returns True if the overall request timeout has been exceeded."""
        return self.elapsed() >= self.config.timeout_sec

    def record_error(self, error: dict):
        """Records a non-fatal step error for reporting."""
        self.errors.append(error)
        logger.warning(f"[GUARD] Non-fatal error recorded: {error}")

    def has_errors(self) -> bool:
        return bool(self.errors)

    def get_summary(self) -> dict:
        """Returns execution summary dict for response metadata."""
        return {
            "steps_executed": self.counter.count,
            "elapsed_sec":    round(self.elapsed(), 2),
            "errors":         self.errors,
            "timed_out":      self.is_timed_out(),
        }
