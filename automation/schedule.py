from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models.contracts import ScheduleSpec
from utils.time_utils import utc_now

try:  # croniter is an optional runtime dependency until installed from requirements.
    from croniter import croniter
except Exception:  # pragma: no cover - environment dependent
    croniter = None


def parse_datetime(value: str | datetime | None, timezone: str = "UTC") -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_zoneinfo(timezone))
    return parsed.astimezone(UTC)


def _zoneinfo(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc


def validate_schedule_spec(spec: ScheduleSpec | dict) -> list[str]:
    schedule = ScheduleSpec.from_value(spec)
    errors: list[str] = []
    if schedule.schedule_type not in {"once", "interval", "cron"}:
        errors.append("schedule_type must be one of: once, interval, cron.")
    try:
        _zoneinfo(schedule.timezone)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        parse_datetime(schedule.start_at, schedule.timezone)
        parse_datetime(schedule.end_at, schedule.timezone)
    except ValueError as exc:
        errors.append(f"Invalid schedule datetime: {exc}")
    if schedule.schedule_type == "once" and not schedule.start_at:
        errors.append("once schedules require start_at.")
    if schedule.schedule_type == "interval" and schedule.interval_seconds <= 0:
        errors.append("interval schedules require interval_seconds > 0.")
    if schedule.schedule_type == "cron":
        if not schedule.cron_expression.strip():
            errors.append("cron schedules require cron_expression.")
        elif croniter is None:
            errors.append("cron schedules require the croniter package to be installed.")
    return errors


def compute_next_run_at(
    spec: ScheduleSpec | dict,
    *,
    after: datetime | None = None,
    previous_run_at: datetime | None = None,
) -> datetime | None:
    schedule = ScheduleSpec.from_value(spec)
    errors = validate_schedule_spec(schedule)
    if errors:
        raise ValueError(" ".join(errors))

    now = (after or utc_now()).astimezone(UTC)
    start_at = parse_datetime(schedule.start_at, schedule.timezone)
    end_at = parse_datetime(schedule.end_at, schedule.timezone)
    previous = parse_datetime(previous_run_at, schedule.timezone)

    if schedule.schedule_type == "once":
        candidate = start_at
        if previous is not None:
            return None
    elif schedule.schedule_type == "interval":
        if previous is not None:
            candidate = previous + timedelta(seconds=schedule.interval_seconds)
        elif start_at is not None:
            candidate = start_at
        else:
            candidate = now + timedelta(seconds=schedule.interval_seconds)
    else:
        if croniter is None:
            raise ValueError("cron schedules require the croniter package to be installed.")
        tz = _zoneinfo(schedule.timezone)
        base = (previous or start_at or now).astimezone(tz)
        if previous is None and start_at is not None and start_at > now:
            candidate = start_at
        else:
            candidate = croniter(schedule.cron_expression, base).get_next(datetime).astimezone(UTC)

    if candidate is None:
        return None
    candidate = candidate.astimezone(UTC)
    if end_at is not None and candidate > end_at:
        return None
    return candidate

