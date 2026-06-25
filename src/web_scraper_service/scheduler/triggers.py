"""Trigger configuration parsing for cron / interval / date schedules."""

from __future__ import annotations

from typing import Any


def parse_cron(expression: str) -> Any:
    """Parse a cron expression into an APScheduler CronTrigger.

    Supports standard 5-field cron: minute hour day month day_of_week
    Also supports 6-field with seconds.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = expression.strip().split()
    if len(parts) == 5:
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )
    if len(parts) == 6:
        return CronTrigger(
            second=parts[0],
            minute=parts[1],
            hour=parts[2],
            day=parts[3],
            month=parts[4],
            day_of_week=parts[5],
        )
    msg = f"Invalid cron expression (expected 5 or 6 fields): {expression}"
    raise ValueError(msg)


def parse_interval(expression: str) -> Any:
    """Parse an interval expression like '30m', '2h', '1d' into an IntervalTrigger."""
    from apscheduler.triggers.interval import IntervalTrigger

    unit = expression[-1].lower()
    value = int(expression[:-1])
    kwargs: dict[str, int] = {}
    if unit == "s":
        kwargs["seconds"] = value
    elif unit == "m":
        kwargs["minutes"] = value
    elif unit == "h":
        kwargs["hours"] = value
    elif unit == "d":
        kwargs["days"] = value
    else:
        msg = f"Unknown interval unit: {unit}"
        raise ValueError(msg)
    return IntervalTrigger(**kwargs)


def parse_trigger(schedule: str) -> Any:
    """Auto-detect trigger type from schedule string.

    If it contains spaces (like '0 */6 * * *'), treat as cron.
    If it's a simple duration (like '30m'), treat as interval.
    """
    if " " in schedule.strip():
        return parse_cron(schedule)
    return parse_interval(schedule)
