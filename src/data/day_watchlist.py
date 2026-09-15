"""Autopilot day watchlist product (PROH-187).

Machine-readable envelope shared by Lane A/B × HK/US Autopilots:

    market, lane, session_date, symbols[], rationale?, empty_reason?

Empty ``symbols`` requires a non-empty ``empty_reason`` (fail closed for UI —
never silently pretend there is a tradeable list).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from core.market_symbols import is_valid_market_symbol

SCHEMA_VERSION = "day_watchlist_1.0"
MarketId = Literal["US", "HK"]
LaneId = Literal["A", "B"]


class DayWatchlistError(ValueError):
    """Day watchlist product failed validation — fail closed."""


@dataclass(frozen=True)
class DayWatchlist:
    market: MarketId
    lane: LaneId
    session_date: date
    symbols: tuple[str, ...]
    empty_reason: str | None = None
    rationale: str | None = None
    schema_version: str | None = None
    generated_by: str | None = None

    @property
    def is_empty(self) -> bool:
        return len(self.symbols) == 0


def _norm_market(raw: Any) -> MarketId:
    m = str(raw or "").strip().upper()
    if m not in {"US", "HK"}:
        raise DayWatchlistError(f"market must be US|HK, got {raw!r}")
    return m  # type: ignore[return-value]


def _norm_lane(raw: Any) -> LaneId:
    lane = str(raw or "").strip().upper()
    if lane in {"LANE_A", "A"}:
        return "A"
    if lane in {"LANE_B", "B"}:
        return "B"
    raise DayWatchlistError(f"lane must be A|B, got {raw!r}")


def validate_day_watchlist_dict(payload: dict[str, Any]) -> DayWatchlist:
    if not isinstance(payload, dict):
        raise DayWatchlistError("payload must be object")

    schema = payload.get("schema_version")
    if schema is not None:
        schema_s = str(schema).strip()
        if schema_s and schema_s not in {SCHEMA_VERSION, "1.0"}:
            raise DayWatchlistError(
                f"schema_version must be {SCHEMA_VERSION!r} or omitted, got {schema!r}"
            )
    else:
        schema_s = None

    market = _norm_market(payload.get("market"))
    lane = _norm_lane(payload.get("lane"))

    try:
        session_date = date.fromisoformat(str(payload["session_date"]))
    except Exception as exc:
        raise DayWatchlistError(
            f"session_date must be YYYY-MM-DD, got {payload.get('session_date')!r}"
        ) from exc

    raw_symbols = payload.get("symbols")
    if raw_symbols is None:
        raw_symbols = []
    if not isinstance(raw_symbols, list):
        raise DayWatchlistError("symbols must be an array")
    symbols: list[str] = []
    for i, item in enumerate(raw_symbols):
        sym = str(item or "").strip()
        if not sym:
            raise DayWatchlistError(f"symbols[{i}] empty")
        if not is_valid_market_symbol(sym):
            raise DayWatchlistError(
                f"symbols[{i}] must match US.TICKER or HK.#####, got {sym!r}"
            )
        prefix = sym.split(".", 1)[0].upper()
        if prefix != market:
            raise DayWatchlistError(
                f"symbols[{i}] market prefix {prefix} != document market={market}"
            )
        symbols.append(sym)

    empty_reason = payload.get("empty_reason")
    if empty_reason is not None:
        empty_reason = str(empty_reason).strip() or None

    if not symbols and not empty_reason:
        raise DayWatchlistError(
            "empty symbols requires non-empty empty_reason (no silent empty list)"
        )
    if symbols:
        # Prefer not to carry a stale empty_reason when names exist.
        empty_reason = None

    rationale = payload.get("rationale")
    if rationale is not None:
        rationale = str(rationale).strip() or None

    generated_by = payload.get("generated_by")
    if generated_by is not None:
        generated_by = str(generated_by).strip() or None

    return DayWatchlist(
        market=market,
        lane=lane,
        session_date=session_date,
        symbols=tuple(symbols),
        empty_reason=empty_reason,
        rationale=rationale,
        schema_version=schema_s,
        generated_by=generated_by,
    )


def load_day_watchlist(path: str | Path) -> DayWatchlist:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DayWatchlistError(f"cannot read {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DayWatchlistError(f"invalid JSON in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise DayWatchlistError(f"{p}: root must be object")
    return validate_day_watchlist_dict(raw)
