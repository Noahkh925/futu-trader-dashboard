"""Lane A regime filters (minimal)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegimeConfig:
    """When enabled, suppress breakouts if opening range is too narrow."""

    enabled: bool = False
    min_or_range_pct: float = 0.001  # (or_high - or_low) / mid


def regime_config_from_mapping(raw: dict[str, Any] | None) -> RegimeConfig:
    data = raw or {}
    return RegimeConfig(
        enabled=bool(data.get("enabled", False)),
        min_or_range_pct=float(data.get("min_or_range_pct", 0.001)),
    )


def opening_range_too_narrow(or_high: float, or_low: float, min_pct: float) -> bool:
    mid = (or_high + or_low) / 2.0
    if mid <= 0:
        return True
    return ((or_high - or_low) / mid) < min_pct
