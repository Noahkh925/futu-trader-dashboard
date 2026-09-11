"""Load portfolio configuration and bootstrap the ledger."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.costs import CostModel, cost_model_from_mapping
from core.ledger import Ledger
from core.risk import LaneBRiskLimits, RiskLimits, lane_b_risk_limits_from_mapping
from data.earnings_calendar import CalendarFilterConfig, calendar_filter_config_from_mapping
from strategy.orb_vwap import OrbVwapConfig


@dataclass(frozen=True)
class FutuConfig:
    host: str
    port: int
    env: str


@dataclass(frozen=True)
class PaperConfig:
    mode: str  # mock_fill | opend_sim
    fill_slippage_bps: float = 0.0


@dataclass(frozen=True)
class LaneAConfig:
    symbol: str
    orb: OrbVwapConfig
    risk: RiskLimits
    paper: PaperConfig
    # fixed: trade ``symbol`` only. watchlist: lane_a_tech_1.0 file (fail closed).
    universe_mode: str = "fixed"
    tech_watchlist_path: str | None = None
    empty_universe_policy: str = "no_trade"
    max_universe_names: int = 3


@dataclass(frozen=True)
class LaneBCalendarConfig:
    source: str  # fixture | opend | auto
    fixture_path: str


@dataclass(frozen=True)
class LaneBOptionChainConfig:
    source: str  # fixture | opend | auto
    fixture_path: str


@dataclass(frozen=True)
class LaneBConfig:
    horizon_days: int
    deploy_window_days: int
    cooldown_days: int
    max_notional_pct: float
    max_event_loss: float
    daily_loss_limit: float
    structure: str
    strangle_otm_pct: float
    strike_step: float
    filters: CalendarFilterConfig
    calendar: LaneBCalendarConfig
    option_chain: LaneBOptionChainConfig
    paper: PaperConfig
    analyst_watchlist_path: str | None = None

    @property
    def risk(self) -> LaneBRiskLimits:
        """Structured risk limits (same values as the flat config fields)."""
        return LaneBRiskLimits(
            max_notional_pct=self.max_notional_pct,
            max_event_loss=self.max_event_loss,
            daily_loss_limit=self.daily_loss_limit,
        )


@dataclass(frozen=True)
class PromotionConfig:
    n_days: int = 20
    max_halts_per_day: int = 2


@dataclass(frozen=True)
class PortfolioConfig:
    total_capital: float
    lane_a_ratio: float
    lane_b_ratio: float
    futu: FutuConfig
    lane_a: LaneAConfig
    lane_b: LaneBConfig
    costs: CostModel = CostModel()
    trading_enabled: bool = False
    allow_production_live: bool = False
    experiment_id: str = "baseline"
    promotion: PromotionConfig = PromotionConfig()

    def create_ledger(self) -> Ledger:
        return Ledger.from_allocation(
            self.total_capital,
            self.lane_a_ratio,
            self.lane_b_ratio,
        )


def _default_lane_a() -> dict[str, Any]:
    return {
        "symbol": "US.QQQ",
        "universe_mode": "fixed",
        "tech_watchlist_path": None,
        "empty_universe_policy": "no_trade",
        "max_universe_names": 3,
        "orb": {"window_minutes": 15, "breakout_buffer_pct": 0.0},
        "vwap": {"require_above_for_long": True, "require_below_for_short": True},
        "regime": {"enabled": False, "min_or_range_pct": 0.001},
        "risk": {
            "max_loss_per_trade_pct": 0.01,
            "max_daily_loss_pct": 0.03,
            "max_position_pct": 0.25,
            "allow_add_to_position": False,
        },
        "paper": {"mode": "mock_fill", "fill_slippage_bps": 0},
    }


def _default_lane_b() -> dict[str, Any]:
    return {
        "horizon_days": 14,
        "deploy_window_days": 3,
        "cooldown_days": 2,
        "max_notional_pct": 0.25,
        "max_event_loss": 150,
        "daily_loss_limit": 200,
        "structure": "straddle",
        "strangle_otm_pct": 0.05,
        "strike_step": 0.0,
        "min_price": 5.0,
        "min_avg_volume": 0,
        "require_us": True,
        "require_options_tradable": True,
        "analyst_watchlist_path": None,
        "calendar": {
            "source": "fixture",
            "fixture_path": "fixtures/earnings_calendar.json",
        },
        "option_chain": {
            "source": "fixture",
            "fixture_path": "fixtures/option_chains.json",
        },
        "paper": {"mode": "mock_fill", "fill_slippage_bps": 0},
    }


def load_portfolio_config(path: str | Path) -> PortfolioConfig:
    with Path(path).open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    futu_raw = raw.get("futu", {})
    lane_raw = {**_default_lane_a(), **(raw.get("lane_a") or {})}
    orb_raw = {**_default_lane_a()["orb"], **(lane_raw.get("orb") or {})}
    vwap_raw = {**_default_lane_a()["vwap"], **(lane_raw.get("vwap") or {})}
    regime_raw = {**_default_lane_a()["regime"], **(lane_raw.get("regime") or {})}
    risk_raw = {**_default_lane_a()["risk"], **(lane_raw.get("risk") or {})}
    paper_raw = {**_default_lane_a()["paper"], **(lane_raw.get("paper") or {})}

    lane_b_raw = {**_default_lane_b(), **(raw.get("lane_b") or {})}
    lane_b_cal_raw = {
        **_default_lane_b()["calendar"],
        **(lane_b_raw.get("calendar") or {}),
    }
    lane_b_chain_raw = {
        **_default_lane_b()["option_chain"],
        **(lane_b_raw.get("option_chain") or {}),
    }
    lane_b_paper_raw = {
        **_default_lane_b()["paper"],
        **(lane_b_raw.get("paper") or {}),
    }
    filter_cfg = calendar_filter_config_from_mapping(
        {
            "horizon_days": lane_b_raw.get("horizon_days", 14),
            "min_price": lane_b_raw.get("min_price", 5.0),
            "min_avg_volume": lane_b_raw.get("min_avg_volume", 0),
            "require_us": lane_b_raw.get("require_us", True),
            "require_options_tradable": lane_b_raw.get("require_options_tradable", True),
        }
    )

    lane_b_risk = lane_b_risk_limits_from_mapping(lane_b_raw)
    promo_raw = raw.get("promotion") or {}
    wl_path = lane_b_raw.get("analyst_watchlist_path")

    return PortfolioConfig(
        total_capital=float(raw["total_capital"]),
        lane_a_ratio=float(raw["lane_a_ratio"]),
        lane_b_ratio=float(raw["lane_b_ratio"]),
        futu=FutuConfig(
            host=str(futu_raw.get("host", "127.0.0.1")),
            port=int(futu_raw.get("port", 11111)),
            env=str(futu_raw.get("env", "staging")),
        ),
        lane_a=LaneAConfig(
            symbol=str(lane_raw.get("symbol", "US.QQQ")),
            orb=OrbVwapConfig(
                window_minutes=int(orb_raw["window_minutes"]),
                breakout_buffer_pct=float(orb_raw["breakout_buffer_pct"]),
                require_above_for_long=bool(vwap_raw["require_above_for_long"]),
                require_below_for_short=bool(vwap_raw["require_below_for_short"]),
                regime_enabled=bool(regime_raw.get("enabled", False)),
                min_or_range_pct=float(regime_raw.get("min_or_range_pct", 0.001)),
            ),
            risk=RiskLimits(
                max_loss_per_trade_pct=float(risk_raw["max_loss_per_trade_pct"]),
                max_daily_loss_pct=float(risk_raw["max_daily_loss_pct"]),
                max_position_pct=float(risk_raw["max_position_pct"]),
                allow_add_to_position=bool(risk_raw["allow_add_to_position"]),
            ),
            paper=PaperConfig(
                mode=str(paper_raw.get("mode", "mock_fill")),
                fill_slippage_bps=float(paper_raw.get("fill_slippage_bps", 0)),
            ),
            universe_mode=str(lane_raw.get("universe_mode", "fixed")),
            tech_watchlist_path=(
                str(lane_raw["tech_watchlist_path"])
                if lane_raw.get("tech_watchlist_path")
                else None
            ),
            empty_universe_policy=str(
                lane_raw.get("empty_universe_policy", "no_trade")
            ),
            max_universe_names=int(lane_raw.get("max_universe_names", 3)),
        ),
        lane_b=LaneBConfig(
            horizon_days=int(lane_b_raw["horizon_days"]),
            deploy_window_days=int(lane_b_raw["deploy_window_days"]),
            cooldown_days=int(lane_b_raw["cooldown_days"]),
            max_notional_pct=lane_b_risk.max_notional_pct,
            max_event_loss=lane_b_risk.max_event_loss,
            daily_loss_limit=lane_b_risk.daily_loss_limit,
            structure=str(lane_b_raw["structure"]),
            strangle_otm_pct=float(lane_b_raw.get("strangle_otm_pct", 0.05)),
            strike_step=float(lane_b_raw.get("strike_step", 0.0)),
            filters=filter_cfg,
            calendar=LaneBCalendarConfig(
                source=str(lane_b_cal_raw.get("source", "fixture")),
                fixture_path=str(
                    lane_b_cal_raw.get("fixture_path", "fixtures/earnings_calendar.json")
                ),
            ),
            option_chain=LaneBOptionChainConfig(
                source=str(lane_b_chain_raw.get("source", "fixture")),
                fixture_path=str(
                    lane_b_chain_raw.get("fixture_path", "fixtures/option_chains.json")
                ),
            ),
            paper=PaperConfig(
                mode=str(lane_b_paper_raw.get("mode", "mock_fill")),
                fill_slippage_bps=float(lane_b_paper_raw.get("fill_slippage_bps", 0)),
            ),
            analyst_watchlist_path=str(wl_path) if wl_path else None,
        ),
        costs=cost_model_from_mapping(raw.get("costs")),
        trading_enabled=bool(raw.get("trading_enabled", False)),
        allow_production_live=bool(raw.get("allow_production_live", False)),
        experiment_id=str(raw.get("experiment_id", "baseline")),
        promotion=PromotionConfig(
            n_days=int(promo_raw.get("n_days", 20)),
            max_halts_per_day=int(promo_raw.get("max_halts_per_day", 2)),
        ),
    )
