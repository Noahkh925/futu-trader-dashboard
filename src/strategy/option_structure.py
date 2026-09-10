"""Lane B option structure selection: ATM straddle / OTM strangle.

Produces buy legs sized so estimated premium notional stays within
``lane_b_balance * max_notional_pct``. Only callable in DEPLOY context by
higher layers; this module is pure selection + sizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence

from data.option_chain import (
    OptionChain,
    OptionChainError,
    OptionContract,
    select_expiry_after_event,
)

StructureName = Literal["straddle", "strangle"]
LegSide = Literal["BUY", "SELL"]

# US equity option contract multiplier.
CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class StructureConfig:
    """Config-driven structure / strike / expiry preferences."""

    structure: StructureName = "straddle"
    # Relative OTM offset for strangle (0.05 = 5% away from spot).
    strangle_otm_pct: float = 0.05
    # Absolute strike step preference when snapping to listed strikes (0 = nearest).
    strike_step: float = 0.0
    max_notional_pct: float = 0.25
    contract_multiplier: int = CONTRACT_MULTIPLIER


@dataclass(frozen=True)
class OptionLegOrder:
    contract: OptionContract
    side: LegSide
    qty: int
    limit_price: float

    @property
    def premium_notional(self) -> float:
        return round(self.limit_price * self.qty * CONTRACT_MULTIPLIER, 2)


@dataclass(frozen=True)
class StructurePlan:
    structure: StructureName
    underlying: str
    spot: float
    expiry: date
    legs: tuple[OptionLegOrder, ...]
    contracts: int
    estimated_notional: float
    max_notional: float

    def to_order_requests(self) -> list[dict]:
        """Paper-broker oriented dicts (symbol / side / qty / limit_price)."""
        return [
            {
                "symbol": leg.contract.symbol,
                "side": leg.side,
                "qty": float(leg.qty),
                "limit_price": leg.limit_price,
                "reason": f"lane_b_{self.structure}",
            }
            for leg in self.legs
        ]


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def nearest_strike(strikes: Sequence[float], target: float) -> float:
    if not strikes:
        raise OptionChainError("no strikes available")
    return min(strikes, key=lambda s: (abs(s - target), s))


def atm_strike(chain: OptionChain, *, expiry: date, spot: float | None = None) -> float:
    """ATM = listed strike closest to spot on the chosen expiry."""
    px = chain.spot if spot is None else spot
    strikes = sorted({c.strike for c in chain.contracts if c.expiry == expiry})
    return nearest_strike(strikes, px)


def _pick_contract(
    contracts: Sequence[OptionContract],
    *,
    expiry: date,
    strike: float,
    right: Literal["C", "P"],
) -> OptionContract:
    matches = [
        c
        for c in contracts
        if c.expiry == expiry and c.strike == strike and c.right == right
    ]
    if not matches:
        raise OptionChainError(
            f"missing {right} strike={strike} expiry={expiry.isoformat()} on chain"
        )
    return matches[0]


def _strangle_targets(spot: float, otm_pct: float) -> tuple[float, float]:
    if otm_pct < 0:
        raise OptionChainError(f"strangle_otm_pct must be >= 0, got {otm_pct}")
    call_target = spot * (1.0 + otm_pct)
    put_target = spot * (1.0 - otm_pct)
    return call_target, put_target


def select_legs(
    chain: OptionChain,
    *,
    earnings_at: date | datetime,
    config: StructureConfig | None = None,
) -> tuple[OptionContract, OptionContract, date]:
    """Pick call+put contracts for the configured structure.

    Expiry prefers the nearest listing on/after the earnings calendar date.
    """
    cfg = config or StructureConfig()
    structure = cfg.structure
    if structure not in {"straddle", "strangle"}:
        raise OptionChainError(f"unsupported structure: {structure!r}")

    if not chain.contracts:
        raise OptionChainError(f"empty option chain for {chain.underlying}")

    event_day = _as_date(earnings_at)
    expiry = select_expiry_after_event(chain.contracts, after=event_day)
    spot = chain.spot
    strikes = sorted({c.strike for c in chain.contracts if c.expiry == expiry})

    if structure == "straddle":
        strike = nearest_strike(strikes, spot)
        call = _pick_contract(chain.contracts, expiry=expiry, strike=strike, right="C")
        put = _pick_contract(chain.contracts, expiry=expiry, strike=strike, right="P")
        return call, put, expiry

    call_tgt, put_tgt = _strangle_targets(spot, cfg.strangle_otm_pct)
    call_strike = nearest_strike(strikes, call_tgt)
    put_strike = nearest_strike(strikes, put_tgt)
    # Ensure defined-risk long strangle stays OTM when possible.
    if call_strike < spot:
        above = [s for s in strikes if s >= spot]
        if above:
            call_strike = min(above)
    if put_strike > spot:
        below = [s for s in strikes if s <= spot]
        if below:
            put_strike = max(below)
    call = _pick_contract(chain.contracts, expiry=expiry, strike=call_strike, right="C")
    put = _pick_contract(chain.contracts, expiry=expiry, strike=put_strike, right="P")
    return call, put, expiry


def premium_per_structure(call: OptionContract, put: OptionContract) -> float:
    """Debit to buy one long straddle/strangle (per share, before multiplier)."""
    return call.mid + put.mid


def size_contracts(
    *,
    premium_per_share: float,
    lane_b_balance: float,
    max_notional_pct: float,
    multiplier: int = CONTRACT_MULTIPLIER,
) -> int:
    """Convert lane-B balance cap into whole contracts.

    ``estimated_notional = premium_per_share * multiplier * contracts``.
    """
    if lane_b_balance <= 0:
        raise OptionChainError("lane_b_balance must be positive")
    if max_notional_pct <= 0 or max_notional_pct > 1:
        raise OptionChainError(
            f"max_notional_pct must be in (0, 1], got {max_notional_pct}"
        )
    if premium_per_share <= 0:
        raise OptionChainError("premium_per_share must be positive")
    if multiplier <= 0:
        raise OptionChainError("contract multiplier must be positive")

    max_notional = lane_b_balance * max_notional_pct
    cost_one = premium_per_share * multiplier
    qty = int(max_notional // cost_one)
    if qty < 1:
        raise OptionChainError(
            "insufficient notional for 1 contract: "
            f"need {cost_one:.2f}, cap {max_notional:.2f}"
        )
    return qty


def build_structure_plan(
    chain: OptionChain,
    *,
    earnings_at: date | datetime,
    lane_b_balance: float,
    config: StructureConfig | None = None,
) -> StructurePlan:
    """Select structure legs and size to the notional cap."""
    cfg = config or StructureConfig()
    call, put, expiry = select_legs(chain, earnings_at=earnings_at, config=cfg)
    premium = premium_per_structure(call, put)
    qty = size_contracts(
        premium_per_share=premium,
        lane_b_balance=lane_b_balance,
        max_notional_pct=cfg.max_notional_pct,
        multiplier=cfg.contract_multiplier,
    )
    legs = (
        OptionLegOrder(
            contract=call, side="BUY", qty=qty, limit_price=call.mid
        ),
        OptionLegOrder(
            contract=put, side="BUY", qty=qty, limit_price=put.mid
        ),
    )
    estimated = round(sum(leg.premium_notional for leg in legs), 2)
    max_notional = round(lane_b_balance * cfg.max_notional_pct, 2)
    if estimated > max_notional + 1e-6:
        raise OptionChainError(
            f"estimated notional {estimated} exceeds cap {max_notional}"
        )
    return StructurePlan(
        structure=cfg.structure,
        underlying=chain.underlying,
        spot=chain.spot,
        expiry=expiry,
        legs=legs,
        contracts=qty,
        estimated_notional=estimated,
        max_notional=max_notional,
    )


def structure_config_from_mapping(raw: dict | None) -> StructureConfig:
    data = raw or {}
    structure = str(data.get("structure", "straddle")).strip().lower()
    if structure not in {"straddle", "strangle"}:
        raise OptionChainError(f"unsupported structure: {structure!r}")
    return StructureConfig(
        structure=structure,  # type: ignore[arg-type]
        strangle_otm_pct=float(data.get("strangle_otm_pct", 0.05)),
        strike_step=float(data.get("strike_step", 0.0)),
        max_notional_pct=float(data.get("max_notional_pct", 0.25)),
        contract_multiplier=int(data.get("contract_multiplier", CONTRACT_MULTIPLIER)),
    )
