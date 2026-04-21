from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Iterator

from utils.logging_utils import log_event, log_exception


def perf_counter() -> float:
    return time.perf_counter()


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


@contextlib.contextmanager
def timed_log(
    logger: Any,
    event: str,
    *,
    level: int = logging.INFO,
    error_event: str | None = None,
    **context: Any,
) -> Iterator[dict[str, Any]]:
    """Log duration for a sync operation without changing its control flow."""
    started = perf_counter()
    metrics: dict[str, Any] = {}
    try:
        yield metrics
    except Exception as exc:
        log_exception(
            logger,
            error_event or f"{event}.failed",
            exc,
            **context,
            **metrics,
            duration_ms=elapsed_ms(started),
        )
        raise
    else:
        log_event(
            logger,
            level,
            event,
            **context,
            **metrics,
            duration_ms=elapsed_ms(started),
        )
