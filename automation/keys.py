from __future__ import annotations

import hashlib
import json
from datetime import datetime


def _json_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def build_run_key(task_id: str, planned_for: datetime, target_kind: str, target_name: str) -> str:
    return _json_hash(
        {
            "task_id": task_id,
            "planned_for": planned_for.isoformat(),
            "target_kind": target_kind,
            "target_name": target_name,
        }
    )


def build_run_idempotency_key(task_id: str, planned_for: datetime, payload: dict) -> str:
    return _json_hash({"task_id": task_id, "planned_for": planned_for.isoformat(), "payload": payload})
