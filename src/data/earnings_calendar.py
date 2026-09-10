"""Earnings calendar source + SCOUT watchlist filter.

OpenD has no reliable public earnings-calendar API in this repo's P2 scope.
When OpenD is unavailable (or returns nothing), use the JSON fixture for
replay / CI acceptance scripts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
EarningsSession = Literal["bmo", "amc"]

# Conventional US equity earnings anchors (comparable ET / UTC instants).
BMO_ET = time(8, 0)  # before the open
AMC_ET = time(16, 5)  # after the close (+ brief buffer past 16:00)

DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "earnings_calendar.json"
)


@dataclass(frozen=True)
class CalendarFilterConfig:
    """Filters applied during SCOUT watchlist construction."""

    horizon_days: int = 14
    min_price: float = 5.0
    min_avg_volume: float = 0.0
    require_us: bool = True
    require_options_tradable: bool = True
    us_prefix: str = "US."


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    earnings_at: datetime  # timezone-aware (UTC recommended)
    session: EarningsSession
    market: str
    options_tradable: bool
    last_price: float | None = None
    avg_volume: float | None = None

    def to_et(self) -> datetime:
        return self.earnings_at.astimezone(ET)


@dataclass(frozen=True)
class WatchlistEntry:
    symbol: str
    earnings_at: datetime
    score: float
    reason: str

    def to_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "earnings_at": self.earnings_at.astimezone(timezone.utc).isoformat(),
            "score": self.score,
            "reason": self.reason,
        }


def anchor_earnings_datetime(
    earnings_date: date,
    session: EarningsSession,
    *,
    tz: ZoneInfo = ET,
) -> datetime:
    """Map calendar date + BMO/AMC to a comparable timezone-aware datetime.

    Returns UTC so SCOUT / paper clocks can compare without local ambiguity.
    """
    if session == "bmo":
        local = datetime.combine(earnings_date, BMO_ET, tzinfo=tz)
    elif session == "amc":
        local = datetime.combine(earnings_date, AMC_ET, tzinfo=tz)
    else:
        raise ValueError(f"unknown earnings session: {session!r}")
    return local.astimezone(timezone.utc)


def _parse_session(raw: str) -> EarningsSession:
    value = raw.strip().lower()
    if value in {"bmo", "before_open", "premarket", "am"}:
        return "bmo"
    if value in {"amc", "after_close", "after_hours", "pm", "post"}:
        return "amc"
    raise ValueError(f"unsupported earnings session: {raw!r}")


def _event_from_mapping(raw: dict[str, Any]) -> EarningsEvent:
    if "earnings_at" in raw:
        earnings_at = datetime.fromisoformat(str(raw["earnings_at"]))
        if earnings_at.tzinfo is None:
            earnings_at = earnings_at.replace(tzinfo=timezone.utc)
        else:
            earnings_at = earnings_at.astimezone(timezone.utc)
        session = _parse_session(str(raw.get("session", "amc")))
    else:
        earnings_date = date.fromisoformat(str(raw["earnings_date"]))
        session = _parse_session(str(raw.get("session", "amc")))
        earnings_at = anchor_earnings_datetime(earnings_date, session)

    market = str(raw.get("market") or ("US" if str(raw["symbol"]).startswith("US.") else "UNKNOWN"))
    return EarningsEvent(
        symbol=str(raw["symbol"]),
        earnings_at=earnings_at,
        session=session,
        market=market,
        options_tradable=bool(raw.get("options_tradable", False)),
        last_price=(float(raw["last_price"]) if raw.get("last_price") is not None else None),
        avg_volume=(float(raw["avg_volume"]) if raw.get("avg_volume") is not None else None),
    )


def load_fixture_events(path: str | Path | None = None) -> list[EarningsEvent]:
    fixture_path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
    with fixture_path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload.get("events", payload if isinstance(payload, list) else [])
    return [_event_from_mapping(row) for row in rows]


def fetch_opend_earnings_events(
    host: str = "127.0.0.1",
    port: int = 11111,
    *,
    horizon_days: int = 14,
) -> list[EarningsEvent]:
    """Best-effort OpenD pull.

    Futu OpenD does not expose a first-class earnings calendar in this project's
    integration surface. This function attempts a soft probe and returns [] on
    any failure so callers can fall back to fixtures.
    """
    del horizon_days  # reserved for a future OpenD calendar query
    try:
        from futu import OpenQuoteContext  # type: ignore

        ctx = OpenQuoteContext(host=host, port=port)
        try:
            # Soft connectivity check only — no calendar endpoint wired yet.
            _ = ctx
            logger.info(
                "OpenD reachable at %s:%s but earnings calendar API is not wired; "
                "returning empty calendar",
                host,
                port,
            )
            return []
        finally:
            ctx.close()
    except ImportError:
        logger.warning("futu-api not installed; OpenD earnings calendar unavailable")
    except Exception as exc:
        logger.warning("OpenD earnings calendar unavailable (%s)", exc)
    return []


def load_earnings_events(
    *,
    source: Literal["fixture", "opend", "auto"] = "auto",
    fixture_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 11111,
    horizon_days: int = 14,
) -> tuple[list[EarningsEvent], str]:
    """Load events from OpenD and/or fixture.

    Returns ``(events, source_used)``.
    """
    if source == "fixture":
        return load_fixture_events(fixture_path), "fixture"

    if source == "opend":
        events = fetch_opend_earnings_events(host, port, horizon_days=horizon_days)
        return events, "opend"

    # auto: prefer OpenD when it yields rows; otherwise fixture (CI / offline).
    live = fetch_opend_earnings_events(host, port, horizon_days=horizon_days)
    if live:
        return live, "opend"
    return load_fixture_events(fixture_path), "fixture"


def _ensure_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _within_horizon(event: EarningsEvent, *, as_of: datetime, horizon_days: int) -> bool:
    as_of_utc = _ensure_utc(as_of)
    if event.earnings_at < as_of_utc:
        return False
    return event.earnings_at <= as_of_utc + timedelta(days=horizon_days)


def passes_filters(event: EarningsEvent, config: CalendarFilterConfig) -> bool:
    if config.require_us:
        if not event.symbol.startswith(config.us_prefix) and event.market.upper() != "US":
            return False
    if config.require_options_tradable and not event.options_tradable:
        return False
    if event.last_price is not None and event.last_price < config.min_price:
        return False
    if event.avg_volume is not None and event.avg_volume < config.min_avg_volume:
        return False
    return True


def filter_events(
    events: Sequence[EarningsEvent],
    *,
    as_of: datetime,
    config: CalendarFilterConfig | None = None,
) -> list[EarningsEvent]:
    cfg = config or CalendarFilterConfig()
    return [
        event
        for event in events
        if _within_horizon(event, as_of=as_of, horizon_days=cfg.horizon_days)
        and passes_filters(event, cfg)
    ]


def score_event(event: EarningsEvent, *, as_of: datetime) -> tuple[float, str]:
    """Heuristic SCOUT score: nearer events and liquid names rank higher."""
    as_of_utc = _ensure_utc(as_of)
    days_ahead = max((event.earnings_at - as_of_utc).total_seconds() / 86400.0, 0.0)
    proximity = max(0.0, 14.0 - days_ahead)  # 0..14
    liquidity = 0.0
    if event.avg_volume is not None:
        liquidity = min(event.avg_volume / 1_000_000.0, 10.0)
    price_bonus = 1.0 if (event.last_price or 0) >= 20 else 0.0
    session_tag = "AMC" if event.session == "amc" else "BMO"
    score = proximity + liquidity + price_bonus
    reason = (
        f"{session_tag} in {days_ahead:.1f}d; "
        f"liq={event.avg_volume or 0:.0f}; px={event.last_price}"
    )
    return score, reason


def build_watchlist(
    events: Sequence[EarningsEvent],
    *,
    as_of: datetime,
    config: CalendarFilterConfig | None = None,
) -> list[WatchlistEntry]:
    filtered = filter_events(events, as_of=as_of, config=config)
    entries: list[WatchlistEntry] = []
    for event in filtered:
        score, reason = score_event(event, as_of=as_of)
        entries.append(
            WatchlistEntry(
                symbol=event.symbol,
                earnings_at=event.earnings_at,
                score=round(score, 4),
                reason=reason,
            )
        )
    entries.sort(key=lambda e: (-e.score, e.earnings_at, e.symbol))
    return entries


def write_watchlist_jsonl(entries: Sequence[WatchlistEntry], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry.to_record(), ensure_ascii=True) + "\n")
    return out


def scout_watchlist(
    *,
    as_of: datetime,
    config: CalendarFilterConfig | None = None,
    source: Literal["fixture", "opend", "auto"] = "auto",
    fixture_path: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 11111,
) -> tuple[list[WatchlistEntry], str]:
    """SCOUT helper: load calendar → filter → ranked watchlist."""
    cfg = config or CalendarFilterConfig()
    events, used = load_earnings_events(
        source=source,
        fixture_path=fixture_path,
        host=host,
        port=port,
        horizon_days=cfg.horizon_days,
    )
    return build_watchlist(events, as_of=as_of, config=cfg), used


def calendar_filter_config_from_mapping(raw: dict[str, Any] | None) -> CalendarFilterConfig:
    data = raw or {}
    return CalendarFilterConfig(
        horizon_days=int(data.get("horizon_days", 14)),
        min_price=float(data.get("min_price", 5.0)),
        min_avg_volume=float(data.get("min_avg_volume", 0.0)),
        require_us=bool(data.get("require_us", True)),
        require_options_tradable=bool(data.get("require_options_tradable", True)),
        us_prefix=str(data.get("us_prefix", "US.")),
    )


def event_as_dict(event: EarningsEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["earnings_at"] = event.earnings_at.astimezone(timezone.utc).isoformat()
    return payload
