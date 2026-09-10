"""Transaction cost model for paper / staging fills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class CostModel:
    """Commissions and optional option half-spread (bps of premium)."""

    commission_per_share: float = 0.0
    commission_per_contract: float = 0.0
    option_half_spread_bps: float = 0.0

    def equity_commission(self, qty: float) -> float:
        return round(abs(qty) * self.commission_per_share, 4)

    def option_commission(self, contracts: float) -> float:
        return round(abs(contracts) * self.commission_per_contract, 4)

    def option_spread_adjust(self, side: OrderSide, premium: float) -> float:
        """Widen adverse premium by half-spread bps."""
        half = premium * (self.option_half_spread_bps / 10_000.0)
        if side == "BUY":
            return round(premium + half, 4)
        return round(max(premium - half, 0.0), 4)

    def to_dict(self) -> dict[str, float]:
        return {
            "commission_per_share": self.commission_per_share,
            "commission_per_contract": self.commission_per_contract,
            "option_half_spread_bps": self.option_half_spread_bps,
        }


def cost_model_from_mapping(raw: dict[str, Any] | None) -> CostModel:
    data = raw or {}
    return CostModel(
        commission_per_share=float(data.get("commission_per_share", 0.0)),
        commission_per_contract=float(data.get("commission_per_contract", 0.0)),
        option_half_spread_bps=float(data.get("option_half_spread_bps", 0.0)),
    )
