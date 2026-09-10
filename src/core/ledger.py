"""Dual-lane cash ledger for A/B capital allocation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Ledger:
    """Tracks independent lane balances derived from total capital and ratios."""

    lane_a_balance: float
    lane_b_balance: float

    @classmethod
    def from_allocation(
        cls,
        total_capital: float,
        lane_a_ratio: float,
        lane_b_ratio: float,
    ) -> Ledger:
        if lane_a_ratio < 0 or lane_b_ratio < 0:
            raise ValueError("lane ratios must be non-negative")
        ratio_sum = lane_a_ratio + lane_b_ratio
        if ratio_sum <= 0:
            raise ValueError("lane ratios must sum to a positive value")
        if abs(ratio_sum - 1.0) > 1e-9:
            raise ValueError("lane ratios must sum to 1.0")

        return cls(
            lane_a_balance=round(total_capital * lane_a_ratio, 2),
            lane_b_balance=round(total_capital * lane_b_ratio, 2),
        )

    @property
    def total(self) -> float:
        return round(self.lane_a_balance + self.lane_b_balance, 2)

    def update_lane_a(self, delta: float) -> None:
        """Apply a signed cash delta to lane A."""
        self.lane_a_balance = round(self.lane_a_balance + delta, 2)

    def update_lane_b(self, delta: float) -> None:
        """Apply a signed cash delta to lane B."""
        self.lane_b_balance = round(self.lane_b_balance + delta, 2)
