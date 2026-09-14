"""US equity Regular Trading Hours (RTH) gate.

Default window: America/New_York 09:30–16:00 on weekdays that are not
known NYSE full-day holidays. Unknown future holidays degrade safely:
weekdays outside the static list are treated as potential RTH days
(runner still respects the clock window; holiday mis-open is preferred
over silently skipping a real session).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)

SessionPhase = Literal["pre_open", "rth", "after_close", "weekend", "holiday"]

# NYSE full-day closures (observed). Extend annually; missing future dates
# degrade to weekday RTH clock only (see module docstring).
_NYSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2024
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # Independence Day observed
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),  # Juneteenth observed
        date(2027, 7, 5),  # Independence Day observed
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),  # Christmas observed
    }
)


def now_et(when: datetime | None = None) -> datetime:
    """Return an America/New_York-aware datetime."""
    if when is None:
        return datetime.now(ET)
    if when.tzinfo is None:
        return when.replace(tzinfo=ET)
    return when.astimezone(ET)


def is_nyse_holiday(day: date) -> bool:
    """True when ``day`` is in the known full-day NYSE holiday set."""
    return day in _NYSE_HOLIDAYS


def is_trading_day(day: date) -> bool:
    """Weekday and not a known NYSE full-day holiday."""
    if day.weekday() >= 5:
        return False
    return not is_nyse_holiday(day)


def session_phase(when: datetime | None = None) -> SessionPhase:
    """Classify wall-clock instant relative to US equity RTH."""
    ts = now_et(when)
    day = ts.date()
    if day.weekday() >= 5:
        return "weekend"
    if is_nyse_holiday(day):
        return "holiday"
    t = ts.timetz().replace(tzinfo=None)
    if t < RTH_OPEN:
        return "pre_open"
    if t >= RTH_CLOSE:
        return "after_close"
    return "rth"


def is_rth(when: datetime | None = None) -> bool:
    """True only during Mon–Fri RTH on a non-holiday session day."""
    return session_phase(when) == "rth"


def seconds_until_phase_change(when: datetime | None = None) -> float:
    """Seconds until the next RTH open/close boundary (or next weekday open)."""
    ts = now_et(when)
    phase = session_phase(ts)
    day = ts.date()

    if phase == "rth":
        close = datetime.combine(day, RTH_CLOSE, tzinfo=ET)
        return max(0.0, (close - ts).total_seconds())

    if phase == "pre_open":
        open_ = datetime.combine(day, RTH_OPEN, tzinfo=ET)
        return max(0.0, (open_ - ts).total_seconds())

    # after_close / weekend / holiday → next trading-day open
    cursor = day + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(cursor):
            open_ = datetime.combine(cursor, RTH_OPEN, tzinfo=ET)
            return max(0.0, (open_ - ts).total_seconds())
        cursor += timedelta(days=1)
    # Fallback: one week out
    open_ = datetime.combine(day + timedelta(days=7), RTH_OPEN, tzinfo=ET)
    return max(0.0, (open_ - ts).total_seconds())


def rth_session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """Return (open, close) ET for a session day, or None if not a trading day."""
    if not is_trading_day(day):
        return None
    return (
        datetime.combine(day, RTH_OPEN, tzinfo=ET),
        datetime.combine(day, RTH_CLOSE, tzinfo=ET),
    )
