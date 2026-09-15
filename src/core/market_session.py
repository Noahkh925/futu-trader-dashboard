"""Market-aware RTH calendars for US and HK (PROH-175 / dual-market spec).

US: America/New_York continuous 09:30–16:00 (wraps existing ``core.rth``).
HK: Asia/Hong_Kong morning 09:30–12:00 + afternoon 13:00–16:00; lunch idle;
half-days morning-only (close 12:00).

Activation: at most one market is in continuous RTH at a wall-clock instant
under standard DST; if both would be open, prefer US and log.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from core import rth as us_rth

logger = logging.getLogger(__name__)

MarketId = Literal["US", "HK"]
SessionPhase = Literal[
    "pre_open",
    "rth",
    "lunch",
    "after_close",
    "weekend",
    "holiday",
]

HKT = ZoneInfo("Asia/Hong_Kong")
ET = us_rth.ET

HK_MORNING_OPEN = time(9, 30)
HK_MORNING_CLOSE = time(12, 0)
HK_AFTERNOON_OPEN = time(13, 0)
HK_AFTERNOON_CLOSE = time(16, 0)
HK_PRE_OPEN = time(9, 0)

# HKEX full-day closures (observed). Extend annually; missing future dates
# degrade to weekday clock only (same policy as NYSE list).
_HKEX_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2025
        date(2025, 1, 1),
        date(2025, 1, 28),
        date(2025, 1, 29),
        date(2025, 1, 30),
        date(2025, 1, 31),
        date(2025, 4, 18),
        date(2025, 4, 21),
        date(2025, 5, 1),
        date(2025, 5, 5),
        date(2025, 5, 31),
        date(2025, 7, 1),
        date(2025, 10, 1),
        date(2025, 10, 7),
        date(2025, 12, 25),
        date(2025, 12, 26),
        # 2026
        date(2026, 1, 1),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 4, 7),
        date(2026, 5, 1),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 1),
        date(2026, 10, 1),
        date(2026, 10, 19),
        date(2026, 12, 25),
        date(2026, 12, 26),
        # 2027
        date(2027, 1, 1),
        date(2027, 2, 8),
        date(2027, 2, 9),
        date(2027, 2, 10),
        date(2027, 3, 26),
        date(2027, 3, 29),
        date(2027, 4, 5),
        date(2027, 5, 1),
        date(2027, 5, 13),
        date(2027, 6, 9),
        date(2027, 7, 1),
        date(2027, 10, 1),
        date(2027, 10, 18),
        date(2027, 12, 25),
        date(2027, 12, 27),
    }
)

# HKEX half-day sessions (morning only; close 12:00). Eve of Lunar New Year /
# Christmas / New Year when applicable.
_HKEX_HALF_DAYS: frozenset[date] = frozenset(
    {
        date(2025, 1, 27),  # Lunar New Year eve
        date(2025, 12, 24),
        date(2025, 12, 31),
        date(2026, 2, 13),  # Lunar New Year eve (approx; verify annually)
        date(2026, 12, 24),
        date(2026, 12, 31),
        date(2027, 2, 5),
        date(2027, 12, 24),
        date(2027, 12, 31),
    }
)

RTH_MINUTES_US = 390
RTH_MINUTES_HK = 330
RTH_MINUTES_HK_HALF = 150

DEFAULT_CONFIG_BY_MARKET: dict[MarketId, str] = {
    "US": "config/portfolio.virtual.yaml",
    "HK": "config/portfolio.virtual.hk.yaml",
}


def normalize_market(raw: str | None) -> MarketId:
    """Normalize CLI/config market id; default US for backward compatibility."""
    if raw is None or str(raw).strip() == "":
        return "US"
    key = str(raw).strip().upper()
    if key not in {"US", "HK"}:
        raise ValueError(f"market must be US or HK, got {raw!r}")
    return key  # type: ignore[return-value]


def zone_for(market: MarketId) -> ZoneInfo:
    return ET if market == "US" else HKT


def now_market(market: MarketId, when: datetime | None = None) -> datetime:
    """Aware datetime in the market's session timezone."""
    tz = zone_for(market)
    if when is None:
        return datetime.now(tz)
    if when.tzinfo is None:
        return when.replace(tzinfo=tz)
    return when.astimezone(tz)


def is_hkex_holiday(day: date) -> bool:
    return day in _HKEX_HOLIDAYS


def is_hkex_half_day(day: date) -> bool:
    return day in _HKEX_HALF_DAYS


def is_trading_day(market: MarketId, day: date) -> bool:
    if market == "US":
        return us_rth.is_trading_day(day)
    if day.weekday() >= 5:
        return False
    return not is_hkex_holiday(day)


def rth_minutes(market: MarketId, day: date | None = None) -> int:
    if market == "US":
        return RTH_MINUTES_US
    if day is not None and is_hkex_half_day(day):
        return RTH_MINUTES_HK_HALF
    return RTH_MINUTES_HK


def session_open_time(market: MarketId) -> time:
    return us_rth.RTH_OPEN if market == "US" else HK_MORNING_OPEN


def session_open_dt(market: MarketId, session_day: date) -> datetime:
    tz = zone_for(market)
    return datetime.combine(session_day, session_open_time(market), tzinfo=tz)


def _hk_session_phase(ts: datetime) -> SessionPhase:
    day = ts.date()
    if day.weekday() >= 5:
        return "weekend"
    if is_hkex_holiday(day):
        return "holiday"
    t = ts.timetz().replace(tzinfo=None)
    half = is_hkex_half_day(day)
    if t < HK_MORNING_OPEN:
        return "pre_open"
    if t < HK_MORNING_CLOSE:
        return "rth"
    if half:
        return "after_close"
    if t < HK_AFTERNOON_OPEN:
        return "lunch"
    if t < HK_AFTERNOON_CLOSE:
        return "rth"
    return "after_close"


def session_phase(market: MarketId, when: datetime | None = None) -> SessionPhase:
    if market == "US":
        # US module has no lunch phase; cast is fine for Literal union.
        return us_rth.session_phase(when)  # type: ignore[return-value]
    return _hk_session_phase(now_market("HK", when))


def is_rth(market: MarketId, when: datetime | None = None) -> bool:
    """True only during continuous auction (not lunch / pre-open / after-close)."""
    return session_phase(market, when) == "rth"


def is_session_day_active(market: MarketId, when: datetime | None = None) -> bool:
    """True from first continuous open until final close (includes HK lunch idle).

    Used by the resident runner so lunch does not finalize the day early.
    """
    phase = session_phase(market, when)
    if phase in {"weekend", "holiday", "pre_open", "after_close"}:
        return False
    return phase in {"rth", "lunch"}


def seconds_until_phase_change(market: MarketId, when: datetime | None = None) -> float:
    if market == "US":
        return us_rth.seconds_until_phase_change(when)

    ts = now_market("HK", when)
    phase = _hk_session_phase(ts)
    day = ts.date()

    def _secs(target: time, on_day: date = day) -> float:
        boundary = datetime.combine(on_day, target, tzinfo=HKT)
        return max(0.0, (boundary - ts).total_seconds())

    if phase == "rth":
        if ts.timetz().replace(tzinfo=None) < HK_MORNING_CLOSE:
            return _secs(HK_MORNING_CLOSE)
        return _secs(HK_AFTERNOON_CLOSE)

    if phase == "lunch":
        return _secs(HK_AFTERNOON_OPEN)

    if phase == "pre_open":
        return _secs(HK_MORNING_OPEN)

    # after_close / weekend / holiday → next trading-day morning open
    cursor = day + timedelta(days=1)
    for _ in range(14):
        if is_trading_day("HK", cursor):
            return _secs(HK_MORNING_OPEN, cursor)
        cursor += timedelta(days=1)
    return _secs(HK_MORNING_OPEN, day + timedelta(days=7))


def rth_session_bounds(
    market: MarketId, day: date
) -> tuple[datetime, datetime] | None:
    """Return (first_open, final_close) in market TZ, or None if not a trading day."""
    if not is_trading_day(market, day):
        return None
    if market == "US":
        return us_rth.rth_session_bounds(day)
    open_ = datetime.combine(day, HK_MORNING_OPEN, tzinfo=HKT)
    if is_hkex_half_day(day):
        close = datetime.combine(day, HK_MORNING_CLOSE, tzinfo=HKT)
    else:
        close = datetime.combine(day, HK_AFTERNOON_CLOSE, tzinfo=HKT)
    return open_, close


def active_order_market(when: datetime | None = None) -> MarketId | None:
    """Which market may place orders now; US wins if both (should be rare)."""
    us_open = is_rth("US", when)
    hk_open = is_rth("HK", when)
    if us_open and hk_open:
        logger.warning(
            "dual_market calendar conflict: both US and HK RTH open; preferring US"
        )
        return "US"
    if us_open:
        return "US"
    if hk_open:
        return "HK"
    return None


def staging_day_dir(output_root: str | object, market: MarketId, day: date) -> object:
    """``logs/staging/{market}/YYYY-MM-DD`` relative path parts under output_root."""
    from pathlib import Path

    root = Path(str(output_root))
    return root / market / day.isoformat()


def default_config_for(market: MarketId) -> str:
    return DEFAULT_CONFIG_BY_MARKET[market]
