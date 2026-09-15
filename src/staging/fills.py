"""Normalize fill events for daily_report.fills[] (dashboard trade detail)."""

from __future__ import annotations

from typing import Any


def normalize_fill_event(event: dict[str, Any], *, default_lane: str) -> dict[str, Any] | None:
    """Map a jsonl / EventLog fill record into a stable daily_report fill row."""
    if not isinstance(event, dict):
        return None
    ev_type = event.get("type")
    if ev_type is not None and str(ev_type) != "fill":
        return None

    lane_raw = event.get("lane") or default_lane
    lane = str(lane_raw).upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    else:
        lane = default_lane

    symbol = event.get("symbol")
    if not symbol:
        return None

    side = event.get("side")
    side_s = str(side).upper() if side not in (None, "") else None

    qty = _as_float(event.get("qty"))
    price = _as_float(event.get("price"))
    notional = _as_float(event.get("notional"))
    if notional is None and qty is not None and price is not None:
        notional = round(qty * price, 4)

    mode = event.get("mode")
    mode_s = str(mode) if mode not in (None, "") else None

    row: dict[str, Any] = {
        "lane": lane,
        "ts": str(event.get("ts") or "") or None,
        "symbol": str(symbol),
        "side": side_s,
        "qty": qty,
        "price": price,
        "notional": notional,
        "mode": mode_s,
    }
    if event.get("reason"):
        row["reason"] = str(event["reason"])
    if event.get("asset"):
        row["asset"] = str(event["asset"])
    if event.get("order_id") is not None:
        row["order_id"] = str(event["order_id"])
    return row


def collect_fills(
    lane_a_events: list[dict[str, Any]] | None,
    lane_b_events: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build chronological fills[] from two lane event lists."""
    rows: list[dict[str, Any]] = []
    for ev in lane_a_events or []:
        row = normalize_fill_event(ev, default_lane="A")
        if row:
            rows.append(row)
    for ev in lane_b_events or []:
        row = normalize_fill_event(ev, default_lane="B")
        if row:
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    return rows


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
