from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from automation import service
from utils.time_utils import utc_now


SCHEDULE_MARKERS = (
    "do this later",
    "schedule this",
    "schedule it",
    "automate this",
    "repeat this",
    "run this later",
    "run every",
    "every day",
    "daily",
)


def _default_timezone() -> str:
    return os.getenv("SINTRA_DEFAULT_TIMEZONE", "UTC") or "UTC"


def _parse_clock(text: str) -> tuple[int, int] | None:
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def extract_schedule_spec(text: str, *, timezone: str | None = None) -> tuple[dict | None, str]:
    """Parse only obvious schedule phrases; ambiguous requests stay interactive."""
    lowered = text.lower()
    tz_name = timezone or _default_timezone()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = "UTC"
        tz = ZoneInfo("UTC")

    every_hours = re.search(r"\bevery\s+(\d+)\s+hours?\b", lowered)
    if every_hours:
        return {
            "schedule_type": "interval",
            "timezone": tz_name,
            "interval_seconds": int(every_hours.group(1)) * 3600,
            "start_at": utc_now().isoformat(),
        }, ""

    every_minutes = re.search(r"\bevery\s+(\d+)\s+minutes?\b", lowered)
    if every_minutes:
        return {
            "schedule_type": "interval",
            "timezone": tz_name,
            "interval_seconds": int(every_minutes.group(1)) * 60,
            "start_at": utc_now().isoformat(),
        }, ""

    if "every day" in lowered or "daily" in lowered:
        clock = _parse_clock(text) or (9, 0)
        return {
            "schedule_type": "cron",
            "timezone": tz_name,
            "cron_expression": f"{clock[1]} {clock[0]} * * *",
        }, ""

    relative = re.search(r"\bin\s+(\d+)\s+(minutes?|hours?|days?)\b", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = timedelta(days=amount) if unit.startswith("day") else timedelta(hours=amount) if unit.startswith("hour") else timedelta(minutes=amount)
        return {
            "schedule_type": "once",
            "timezone": tz_name,
            "start_at": (utc_now() + delta).isoformat(),
        }, ""

    if "tomorrow" in lowered or "today" in lowered:
        clock = _parse_clock(text)
        if not clock:
            return None, "Please include a time, for example `tomorrow at 9 AM`."
        now_local = utc_now().astimezone(tz)
        days = 1 if "tomorrow" in lowered else 0
        target = datetime(
            now_local.year,
            now_local.month,
            now_local.day,
            clock[0],
            clock[1],
            tzinfo=tz,
        ) + timedelta(days=days)
        return {
            "schedule_type": "once",
            "timezone": tz_name,
            "start_at": target.isoformat(),
        }, ""

    if any(marker in lowered for marker in SCHEDULE_MARKERS):
        return None, "Please include when this should run, for example `tomorrow at 9 AM` or `every 2 hours`."
    return None, ""


def maybe_create_chat_schedule(
    *,
    db,
    workspace_id: str,
    user_input: str,
    workflow_key: str | None,
    connector_context: dict | None = None,
    actor_user_id: str | None = None,
    membership_id: str | None = None,
) -> dict | None:
    schedule, schedule_error = extract_schedule_spec(user_input)
    if schedule_error:
        return {
            "mode": "clarify",
            "agent": "system",
            "output": f"I can schedule that, but I need one more detail. {schedule_error}",
            "steps": [],
        }
    if not schedule:
        return None
    if not workflow_key:
        return {
            "mode": "clarify",
            "agent": "system",
            "output": (
                "I found the schedule, but not the automation target. "
                "Please name a workflow such as `email triage`, `lead capture`, or `research and outreach`."
            ),
            "steps": [],
        }
    task = service.create_schedule(
        db,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        membership_id=membership_id,
        schedule=schedule,
        payload={
            "target_kind": "workflow",
            "target_name": workflow_key,
            "user_input": user_input,
            "force_workflow": workflow_key,
        },
        connector_context=connector_context,
        metadata_json={"created_from": "chat"},
    )
    task_data = service.task_to_dict(task)
    return {
        "mode": "automation_scheduled",
        "agent": "system",
        "output": (
            f"Scheduled `{workflow_key}` automation. "
            f"Next run: {task_data.get('next_run_at') or 'not yet computed'}."
        ),
        "steps": [],
        "automation": task_data,
    }
