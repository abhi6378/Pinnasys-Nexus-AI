from __future__ import annotations

import argparse
import logging
import os
import time

from storage.db import SessionLocal, init_db
from utils.logging_utils import configure_logging, log_event
from utils.perf import elapsed_ms, perf_counter
from utils.runtime_config import validate_production_config

from automation.service import enqueue_due_runs


logger = logging.getLogger(__name__)


def enqueue_due_once(*, batch_size: int = 20) -> list[dict]:
    started = perf_counter()
    db = SessionLocal()
    try:
        runs = enqueue_due_runs(db, limit=batch_size)
        log_event(
            logger,
            logging.INFO,
            "automation.scheduler.enqueue_due",
            run_count=len(runs),
            batch_size=batch_size,
            duration_ms=elapsed_ms(started),
        )
        return runs
    finally:
        db.close()


def scheduler_loop(*, poll_seconds: int = 30, batch_size: int = 20) -> None:
    while True:
        enqueue_due_once(batch_size=batch_size)
        time.sleep(max(1, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Nexus Ai automation scheduler.")
    parser.add_argument("--once", action="store_true", help="Enqueue one due batch and exit.")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("SINTRA_SCHEDULER_POLL_SECONDS", "30") or "30"))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SINTRA_SCHEDULER_BATCH_SIZE", "20") or "20"))
    args = parser.parse_args()
    configure_logging()
    validate_production_config()
    init_db()
    if args.once:
        enqueue_due_once(batch_size=args.batch_size)
        return
    scheduler_loop(poll_seconds=args.poll_seconds, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
