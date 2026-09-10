"""Opening Range Breakout (ORB) with VWAP confirmation for Lane A."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)


class SignalSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar (typically 1-minute). Timestamp should be timezone-aware ET."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            object.__setattr__(self, "ts", self.ts.replace(tzinfo=ET))


@dataclass(frozen=True)
class OrbVwapSignal:
    side: SignalSide
    reason: str
    or_high: float | None = None
    or_low: float | None = None
    vwap: float | None = None
    price: float | None = None
    session_date: date | None = None


@dataclass
class OpeningRange:
    high: float | None = None
    low: float | None = None
    finalized: bool = False
    window_end: datetime | None = None

    def update(self, bar: Bar, window_end: datetime) -> None:
        if self.finalized:
            return
        if bar.ts >= window_end:
            self.finalized = True
            self.window_end = window_end
            return
        if self.high is None or bar.high > self.high:
            self.high = bar.high
        if self.low is None or bar.low < self.low:
            self.low = bar.low

    def finalize_if_due(self, now: datetime, window_end: datetime) -> None:
        if not self.finalized and now >= window_end:
            self.finalized = True
            self.window_end = window_end


@dataclass
class VwapTracker:
    cum_pv: float = 0.0
    cum_vol: float = 0.0

    def update(self, bar: Bar) -> float | None:
        typical = (bar.high + bar.low + bar.close) / 3.0
        self.cum_pv += typical * bar.volume
        self.cum_vol += bar.volume
        if self.cum_vol <= 0:
            return None
        return self.cum_pv / self.cum_vol

    @property
    def value(self) -> float | None:
        if self.cum_vol <= 0:
            return None
        return self.cum_pv / self.cum_vol

    def reset(self) -> None:
        self.cum_pv = 0.0
        self.cum_vol = 0.0


def session_open_et(session_day: date) -> datetime:
    return datetime.combine(session_day, RTH_OPEN, tzinfo=ET)


@dataclass
class OrbVwapConfig:
    window_minutes: int = 15
    breakout_buffer_pct: float = 0.0
    require_above_for_long: bool = True
    require_below_for_short: bool = True
    regime_enabled: bool = False
    min_or_range_pct: float = 0.001


@dataclass
class OrbVwapEngine:
    """Stateful ORB + VWAP engine; resets automatically on a new session date."""

    config: OrbVwapConfig = field(default_factory=OrbVwapConfig)
    session_date: date | None = None
    opening_range: OpeningRange = field(default_factory=OpeningRange)
    vwap: VwapTracker = field(default_factory=VwapTracker)
    last_side: SignalSide = SignalSide.FLAT

    def reset(self, session_day: date) -> None:
        self.session_date = session_day
        self.opening_range = OpeningRange()
        self.vwap = VwapTracker()
        self.last_side = SignalSide.FLAT

    def _ensure_session(self, bar: Bar) -> None:
        day = bar.ts.astimezone(ET).date()
        if self.session_date != day:
            self.reset(day)

    def _window_end(self) -> datetime:
        assert self.session_date is not None
        return session_open_et(self.session_date) + timedelta(minutes=self.config.window_minutes)

    def on_bar(self, bar: Bar) -> OrbVwapSignal:
        self._ensure_session(bar)
        window_end = self._window_end()
        vwap_now = self.vwap.update(bar)
        self.opening_range.update(bar, window_end)
        self.opening_range.finalize_if_due(bar.ts, window_end)

        if not self.opening_range.finalized:
            return OrbVwapSignal(
                side=SignalSide.FLAT,
                reason="orb_forming",
                or_high=self.opening_range.high,
                or_low=self.opening_range.low,
                vwap=vwap_now,
                price=bar.close,
                session_date=self.session_date,
            )

        or_high = self.opening_range.high
        or_low = self.opening_range.low
        if or_high is None or or_low is None or vwap_now is None:
            return OrbVwapSignal(
                side=SignalSide.FLAT,
                reason="insufficient_data",
                or_high=or_high,
                or_low=or_low,
                vwap=vwap_now,
                price=bar.close,
                session_date=self.session_date,
            )

        if self.config.regime_enabled:
            from core.regime import opening_range_too_narrow

            if opening_range_too_narrow(or_high, or_low, self.config.min_or_range_pct):
                return OrbVwapSignal(
                    side=SignalSide.FLAT,
                    reason="regime_or_too_narrow",
                    or_high=or_high,
                    or_low=or_low,
                    vwap=vwap_now,
                    price=bar.close,
                    session_date=self.session_date,
                )

        buf = self.config.breakout_buffer_pct
        long_trigger = or_high * (1.0 + buf)
        short_trigger = or_low * (1.0 - buf)
        price = bar.close

        # Inside range → flat / false-breakout recovery
        if short_trigger < price < long_trigger:
            signal = OrbVwapSignal(
                side=SignalSide.FLAT,
                reason="inside_range" if self.last_side == SignalSide.FLAT else "false_breakout",
                or_high=or_high,
                or_low=or_low,
                vwap=vwap_now,
                price=price,
                session_date=self.session_date,
            )
            self.last_side = SignalSide.FLAT
            return signal

        if price >= long_trigger:
            if self.config.require_above_for_long and price < vwap_now:
                signal = OrbVwapSignal(
                    side=SignalSide.FLAT,
                    reason="long_breakout_below_vwap",
                    or_high=or_high,
                    or_low=or_low,
                    vwap=vwap_now,
                    price=price,
                    session_date=self.session_date,
                )
                self.last_side = SignalSide.FLAT
                return signal
            signal = OrbVwapSignal(
                side=SignalSide.LONG,
                reason="long_breakout",
                or_high=or_high,
                or_low=or_low,
                vwap=vwap_now,
                price=price,
                session_date=self.session_date,
            )
            self.last_side = SignalSide.LONG
            return signal

        # price <= short_trigger
        if self.config.require_below_for_short and price > vwap_now:
            signal = OrbVwapSignal(
                side=SignalSide.FLAT,
                reason="short_breakout_above_vwap",
                or_high=or_high,
                or_low=or_low,
                vwap=vwap_now,
                price=price,
                session_date=self.session_date,
            )
            self.last_side = SignalSide.FLAT
            return signal
        signal = OrbVwapSignal(
            side=SignalSide.SHORT,
            reason="short_breakout",
            or_high=or_high,
            or_low=or_low,
            vwap=vwap_now,
            price=price,
            session_date=self.session_date,
        )
        self.last_side = SignalSide.SHORT
        return signal
