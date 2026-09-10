"""Lane A / Lane B risk rules (isolated per-lane balances)."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.ledger import Ledger


@dataclass(frozen=True)
class RiskLimits:
    max_loss_per_trade_pct: float = 0.01
    max_daily_loss_pct: float = 0.03
    max_position_pct: float = 0.25
    allow_add_to_position: bool = False


@dataclass(frozen=True)
class LaneBRiskLimits:
    """B-lane limits: notional vs ``lane_b_balance``; event/daily loss in absolute USD."""

    max_notional_pct: float = 0.25
    max_event_loss: float = 150.0
    daily_loss_limit: float = 200.0


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    should_halt: bool = False


@dataclass
class RiskManager:
    """Evaluates order and PnL constraints; callers halt the state machine on breach."""

    limits: RiskLimits
    lane_a_balance: float
    daily_realized_pnl: float = 0.0
    open_position_notional: float = 0.0
    open_side: str | None = None  # LONG / SHORT / None
    events: list[dict] = field(default_factory=list)

    def _max_trade_loss(self) -> float:
        return abs(self.lane_a_balance) * self.limits.max_loss_per_trade_pct

    def _max_daily_loss(self) -> float:
        return abs(self.lane_a_balance) * self.limits.max_daily_loss_pct

    def _max_position_notional(self) -> float:
        return abs(self.lane_a_balance) * self.limits.max_position_pct

    def check_new_order(
        self,
        *,
        side: str,
        notional: float,
        stop_loss_distance: float | None = None,
    ) -> RiskDecision:
        if notional <= 0:
            return RiskDecision(False, "non_positive_notional", should_halt=False)

        if self.open_position_notional > 0:
            if not self.limits.allow_add_to_position:
                decision = RiskDecision(False, "add_to_position_forbidden", should_halt=False)
                self.events.append({"type": "risk_reject", **decision.__dict__})
                return decision
            if self.open_side and self.open_side != side:
                decision = RiskDecision(False, "flip_while_open_forbidden", should_halt=False)
                self.events.append({"type": "risk_reject", **decision.__dict__})
                return decision

        projected = self.open_position_notional + notional
        if projected > self._max_position_notional() + 1e-9:
            decision = RiskDecision(False, "position_limit", should_halt=False)
            self.events.append({"type": "risk_reject", **decision.__dict__})
            return decision

        if stop_loss_distance is not None and stop_loss_distance > self._max_trade_loss() + 1e-9:
            decision = RiskDecision(False, "per_trade_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", **decision.__dict__})
            return decision

        # Daily loss already breached before new risk
        if self.daily_realized_pnl <= -self._max_daily_loss():
            decision = RiskDecision(False, "daily_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", **decision.__dict__})
            return decision

        return RiskDecision(True, "ok", should_halt=False)

    def record_fill(self, *, side: str, notional: float) -> None:
        self.open_position_notional = round(self.open_position_notional + notional, 2)
        self.open_side = side

    def record_flat(self, realized_pnl: float) -> RiskDecision | None:
        """Close position and apply realized PnL; may request HALT on daily loss."""
        self.daily_realized_pnl = round(self.daily_realized_pnl + realized_pnl, 2)
        self.open_position_notional = 0.0
        self.open_side = None
        if self.daily_realized_pnl <= -self._max_daily_loss():
            decision = RiskDecision(False, "daily_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", **decision.__dict__})
            return decision
        if realized_pnl < 0 and abs(realized_pnl) > self._max_trade_loss() + 1e-9:
            decision = RiskDecision(False, "per_trade_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", **decision.__dict__})
            return decision
        return None

    def reset_day(self, lane_a_balance: float | None = None) -> None:
        if lane_a_balance is not None:
            self.lane_a_balance = lane_a_balance
        self.daily_realized_pnl = 0.0
        self.open_position_notional = 0.0
        self.open_side = None


@dataclass
class LaneBRiskManager:
    """B-lane risk vs ``lane_b_balance``; never mutates lane A.

    Callers apply cash via :meth:`apply_realized_pnl` (ledger B only) and should
    ``state.halt(reason)`` when ``RiskDecision.should_halt`` is true.
    """

    limits: LaneBRiskLimits
    lane_b_balance: float
    daily_realized_pnl: float = 0.0
    event_realized_pnl: float = 0.0
    open_notional: float = 0.0
    events: list[dict] = field(default_factory=list)

    def _max_notional(self) -> float:
        return abs(self.lane_b_balance) * self.limits.max_notional_pct

    def check_new_order(self, *, notional: float) -> RiskDecision:
        """Pre-trade gate for DEPLOY sizing (premium notional)."""
        if notional <= 0:
            decision = RiskDecision(False, "non_positive_notional", should_halt=False)
            self.events.append({"type": "risk_reject", "lane": "B", **decision.__dict__})
            return decision

        if self.daily_realized_pnl <= -self.limits.daily_loss_limit:
            decision = RiskDecision(False, "daily_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision

        projected = self.open_notional + notional
        if projected > self._max_notional() + 1e-9:
            # Parent P2 draft: notional overrun → HALT (active states).
            decision = RiskDecision(False, "max_notional_pct", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision

        # Max loss if the whole premium is lost must fit event budget.
        if notional > self.limits.max_event_loss + 1e-9:
            decision = RiskDecision(False, "max_event_loss", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision

        return RiskDecision(True, "ok", should_halt=False)

    def record_open(self, notional: float) -> None:
        self.open_notional = round(self.open_notional + notional, 2)

    def record_flat(self) -> None:
        self.open_notional = 0.0

    def record_event_pnl(self, realized_pnl: float) -> RiskDecision | None:
        """Accumulate event + daily PnL; may request HALT (does not touch ledger)."""
        self.event_realized_pnl = round(self.event_realized_pnl + realized_pnl, 2)
        self.daily_realized_pnl = round(self.daily_realized_pnl + realized_pnl, 2)
        self.open_notional = 0.0

        if self.daily_realized_pnl <= -self.limits.daily_loss_limit:
            decision = RiskDecision(False, "daily_loss_limit", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision
        if realized_pnl < 0 and abs(realized_pnl) > self.limits.max_event_loss + 1e-9:
            decision = RiskDecision(False, "max_event_loss", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision
        if self.event_realized_pnl < 0 and abs(self.event_realized_pnl) > (
            self.limits.max_event_loss + 1e-9
        ):
            decision = RiskDecision(False, "max_event_loss", should_halt=True)
            self.events.append({"type": "risk_halt", "lane": "B", **decision.__dict__})
            return decision
        return None

    def apply_realized_pnl(self, ledger: Ledger, realized_pnl: float) -> RiskDecision | None:
        """Credit/debit **only** ``lane_b_balance``, then evaluate halt rules.

        Lane A is left unchanged (ledger isolation).
        """
        lane_a_before = ledger.lane_a_balance
        ledger.update_lane_b(realized_pnl)
        if ledger.lane_a_balance != lane_a_before:
            raise RuntimeError("lane_a_balance mutated during lane-B PnL apply")
        self.lane_b_balance = ledger.lane_b_balance
        return self.record_event_pnl(realized_pnl)

    def reset_event(self) -> None:
        self.event_realized_pnl = 0.0
        self.open_notional = 0.0

    def reset_day(self, lane_b_balance: float | None = None) -> None:
        if lane_b_balance is not None:
            self.lane_b_balance = lane_b_balance
        self.daily_realized_pnl = 0.0
        self.event_realized_pnl = 0.0
        self.open_notional = 0.0


def lane_b_risk_limits_from_mapping(raw: dict | None) -> LaneBRiskLimits:
    data = raw or {}
    return LaneBRiskLimits(
        max_notional_pct=float(data.get("max_notional_pct", 0.25)),
        max_event_loss=float(data.get("max_event_loss", 150.0)),
        daily_loss_limit=float(data.get("daily_loss_limit", 200.0)),
    )
