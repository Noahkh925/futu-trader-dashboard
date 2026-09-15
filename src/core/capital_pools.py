"""Isolated per-market capital pools (PROH-175 / RiskOps PROH-174).

US paper 5000 USD and HK paper 5000 USD are separate ledgers. There is no
transfer / borrow / top-up API between pools — any such attempt is a voiding
error by product gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.ledger import Ledger

MarketId = Literal["US", "HK"]

DEFAULT_CAPITAL_USD = 5000.0


class CrossPoolTransferError(RuntimeError):
    """Raised when code attempts to move capital between US and HK pools."""


@dataclass
class MarketCapitalPools:
    """Two isolated ledgers keyed by market. No shared cash."""

    us: Ledger
    hk: Ledger

    @classmethod
    def from_totals(
        cls,
        *,
        us_total: float = DEFAULT_CAPITAL_USD,
        hk_total: float = DEFAULT_CAPITAL_USD,
        lane_a_ratio: float = 0.70,
        lane_b_ratio: float = 0.30,
    ) -> MarketCapitalPools:
        return cls(
            us=Ledger.from_allocation(us_total, lane_a_ratio, lane_b_ratio),
            hk=Ledger.from_allocation(hk_total, lane_a_ratio, lane_b_ratio),
        )

    def ledger_for(self, market: MarketId) -> Ledger:
        if market == "US":
            return self.us
        return self.hk

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            "US": {
                "total": round(self.us.lane_a_balance + self.us.lane_b_balance, 2),
                "lane_a": float(self.us.lane_a_balance),
                "lane_b": float(self.us.lane_b_balance),
            },
            "HK": {
                "total": round(self.hk.lane_a_balance + self.hk.lane_b_balance, 2),
                "lane_a": float(self.hk.lane_a_balance),
                "lane_b": float(self.hk.lane_b_balance),
            },
        }

    def transfer(self, *_args: object, **_kwargs: object) -> None:
        """Hard deny — RiskOps: US ↔ HK mutual drawdown is forbidden."""
        raise CrossPoolTransferError(
            "Cross-market capital transfer is forbidden "
            "(US and HK pools are isolated; voiding error)."
        )
