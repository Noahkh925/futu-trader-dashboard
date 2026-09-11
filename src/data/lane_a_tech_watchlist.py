"""Load and validate Lane A technical watchlist JSON (schema lane_a_tech_1.0).

Independent from Lane B analyst_watchlist (schema_version \"1.0\"). Fail closed:
bad schema raises; weak-evidence / gate contradictions reject the document.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "lane_a_tech_1.0"
_SYMBOL_RE = re.compile(r"^US\.[A-Z][A-Z0-9.\-]*$")


class LaneATechWatchlistError(ValueError):
    """Lane A tech watchlist failed validation — fail closed."""


@dataclass(frozen=True)
class LaneATechName:
    symbol: str
    score: float
    veto: bool
    reasons: tuple[str, ...]
    sources: tuple[str, ...]
    last_price: float | None
    min_price_ok: bool
    adv_ok: bool
    avg_dollar_volume: float | None = None
    avg_volume: float | None = None
    gap_pct: float | None = None
    atr_pct: float | None = None
    relative_volume: float | None = None
    regime_tag: str | None = None
    market: str | None = None
    reason: str | None = None

    @property
    def reason_text(self) -> str:
        if self.reason:
            return self.reason
        return "; ".join(self.reasons)


@dataclass(frozen=True)
class LaneATechWatchlist:
    schema_version: str
    as_of: date
    universe_note: str
    timezone: str
    generated_by: str
    names: tuple[LaneATechName, ...]

    def deployable(self) -> list[LaneATechName]:
        return [n for n in self.names if not n.veto]

    def vetoed(self) -> list[LaneATechName]:
        return [n for n in self.names if n.veto]


def _opt_float(row: dict[str, Any], key: str) -> float | None:
    if key not in row:
        return None
    val = row[key]
    if val is None:
        return None
    if not isinstance(val, (int, float)):
        raise LaneATechWatchlistError(f"{key} must be number or null")
    return float(val)


def _validate_name(i: int, row: dict[str, Any]) -> LaneATechName:
    if not isinstance(row, dict):
        raise LaneATechWatchlistError(f"names[{i}] must be object")

    symbol = str(row.get("symbol") or "")
    if not _SYMBOL_RE.match(symbol):
        raise LaneATechWatchlistError(
            f"names[{i}].symbol must match US.TICKER, got {symbol!r}"
        )

    if "score" not in row or not isinstance(row["score"], (int, float)):
        raise LaneATechWatchlistError(f"names[{i}].score must be number")
    if "veto" not in row or not isinstance(row["veto"], bool):
        raise LaneATechWatchlistError(f"names[{i}].veto must be boolean")

    reasons = row.get("reasons")
    sources = row.get("sources")
    if not isinstance(reasons, list) or not reasons or not all(
        isinstance(x, str) and str(x).strip() for x in reasons
    ):
        raise LaneATechWatchlistError(f"names[{i}].reasons minItems 1 (non-empty strings)")
    if not isinstance(sources, list) or not sources or not all(
        isinstance(x, str) and str(x).strip() for x in sources
    ):
        raise LaneATechWatchlistError(f"names[{i}].sources minItems 1 (non-empty strings)")

    if "last_price" not in row:
        raise LaneATechWatchlistError(f"names[{i}].last_price required (number or null)")
    last_price = row["last_price"]
    if last_price is not None and not isinstance(last_price, (int, float)):
        raise LaneATechWatchlistError(f"names[{i}].last_price must be number or null")
    last_price_f = float(last_price) if last_price is not None else None

    if "min_price_ok" not in row or not isinstance(row["min_price_ok"], bool):
        raise LaneATechWatchlistError(f"names[{i}].min_price_ok must be boolean")
    if "adv_ok" not in row or not isinstance(row["adv_ok"], bool):
        raise LaneATechWatchlistError(f"names[{i}].adv_ok must be boolean")

    has_dollar = "avg_dollar_volume" in row
    has_vol = "avg_volume" in row
    if not has_dollar and not has_vol:
        raise LaneATechWatchlistError(
            f"names[{i}]: require avg_dollar_volume or avg_volume (liquidity evidence)"
        )

    avg_dollar = _opt_float(row, "avg_dollar_volume") if has_dollar else None
    avg_vol = _opt_float(row, "avg_volume") if has_vol else None

    veto = bool(row["veto"])
    min_price_ok = bool(row["min_price_ok"])
    adv_ok = bool(row["adv_ok"])

    # FAIL-CLOSED gate contradictions (schema if/then)
    if not min_price_ok and not veto:
        raise LaneATechWatchlistError(
            f"names[{i}]: min_price_ok=false requires veto=true"
        )
    if not adv_ok and not veto:
        raise LaneATechWatchlistError(f"names[{i}]: adv_ok=false requires veto=true")
    if last_price_f is None and not veto:
        raise LaneATechWatchlistError(
            f"names[{i}]: last_price=null requires veto=true"
        )

    return LaneATechName(
        symbol=symbol,
        score=float(row["score"]),
        veto=veto,
        reasons=tuple(str(x) for x in reasons),
        sources=tuple(str(x) for x in sources),
        last_price=last_price_f,
        min_price_ok=min_price_ok,
        adv_ok=adv_ok,
        avg_dollar_volume=avg_dollar,
        avg_volume=avg_vol,
        gap_pct=_opt_float(row, "gap_pct"),
        atr_pct=_opt_float(row, "atr_pct"),
        relative_volume=_opt_float(row, "relative_volume"),
        regime_tag=(str(row["regime_tag"]) if row.get("regime_tag") is not None else None),
        market=(str(row["market"]) if row.get("market") is not None else None),
        reason=(str(row["reason"]) if row.get("reason") is not None else None),
    )


def validate_lane_a_tech_watchlist_dict(payload: dict[str, Any]) -> LaneATechWatchlist:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise LaneATechWatchlistError(
            f"schema_version must be {SCHEMA_VERSION!r}, "
            f"got {payload.get('schema_version')!r}"
        )
    try:
        as_of = date.fromisoformat(str(payload["as_of"]))
    except Exception as exc:
        raise LaneATechWatchlistError(f"as_of invalid: {payload.get('as_of')!r}") from exc

    note = str(payload.get("universe_note") or "").strip()
    if not note:
        raise LaneATechWatchlistError("universe_note required")
    timezone = str(payload.get("timezone") or "").strip()
    if not timezone:
        raise LaneATechWatchlistError("timezone required")
    generated_by = str(payload.get("generated_by") or "").strip()
    if not generated_by:
        raise LaneATechWatchlistError("generated_by required")

    names_raw = payload.get("names")
    if not isinstance(names_raw, list):
        raise LaneATechWatchlistError("names must be an array")

    names = [_validate_name(i, row) for i, row in enumerate(names_raw)]
    return LaneATechWatchlist(
        schema_version=SCHEMA_VERSION,
        as_of=as_of,
        universe_note=note,
        timezone=timezone,
        generated_by=generated_by,
        names=tuple(names),
    )


def load_lane_a_tech_watchlist(path: str | Path) -> LaneATechWatchlist:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise LaneATechWatchlistError(f"cannot read watchlist {p}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LaneATechWatchlistError(f"invalid JSON in {p}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LaneATechWatchlistError("watchlist root must be object")
    return validate_lane_a_tech_watchlist_dict(payload)
