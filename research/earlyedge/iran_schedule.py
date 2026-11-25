"""Iran-local operating window for the future EarlyEdge live runner.

This is a pre-scan guard: call it before network downloads, symbol loops, or
Telegram delivery. Historical backtests must not use this guard.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

IRAN_TZ = ZoneInfo("Asia/Tehran")
SCAN_START_HOUR = 7
SCAN_END_HOUR = 23  # exclusive


def iran_now(now: datetime | None = None) -> datetime:
    """Return an aware datetime in Asia/Tehran."""
    if now is None:
        return datetime.now(IRAN_TZ)
    if now.tzinfo is None:
        # Naive inputs are interpreted as Iran wall-clock time for easy tests/config.
        return now.replace(tzinfo=IRAN_TZ)
    return now.astimezone(IRAN_TZ)


def is_scan_window(now: datetime | None = None) -> bool:
    """True from 07:00 inclusive until 23:00 exclusive, Iran local time."""
    local = iran_now(now)
    return SCAN_START_HOUR <= local.hour < SCAN_END_HOUR


def scan_window_status(now: datetime | None = None) -> dict[str, str | bool]:
    """Machine-readable status for logs and a live runner's early exit."""
    local = iran_now(now)
    allowed = is_scan_window(local)
    return {
        "allowed": allowed,
        "timezone": "Asia/Tehran",
        "local_time": local.isoformat(),
        "window": "07:00-23:00",
        "reason": "inside_iran_scan_window" if allowed else "outside_iran_scan_window",
    }
