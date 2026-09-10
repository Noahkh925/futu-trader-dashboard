"""Ops snapshot helpers for the read-only web dashboard."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.portfolio import PortfolioConfig, load_portfolio_config
from research.promotion import check_promotion

LANE_A_LABELS: dict[str, str] = {
    "HUNT": "正在找机会",
    "LOCKED": "已锁定/持仓中",
    "HALT": "已暂停（停手）",
}

LANE_B_LABELS: dict[str, str] = {
    "IDLE": "空闲待命",
    "SCOUT": "在扫财报机会",
    "DEPLOY": "已部署/持仓中",
    "COOLDOWN": "冷却中（暂不开新仓）",
    "HALT": "已暂停（停手）",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_reports_dir() -> Path:
    raw = os.environ.get("REPORTS_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _repo_root() / path
        return path
    default = _repo_root() / "fixtures" / "staging" / "promotion_ok"
    if default.exists():
        return default
    return _repo_root() / "logs" / "staging"


def human_lane_a(state: str | None) -> str:
    if not state:
        return "暂无数据"
    return LANE_A_LABELS.get(state, state)


def human_lane_b(state: str | None) -> str:
    if not state:
        return "暂无数据"
    return LANE_B_LABELS.get(state, state)


def load_latest_report(reports_dir: Path) -> dict[str, Any] | None:
    paths = sorted(reports_dir.glob("**/daily_report.json"))
    if not paths:
        flat = sorted(reports_dir.glob("*.json"))
        paths = [p for p in flat if p.name != "sample_day_report.json"] or flat
    if not paths:
        return None
    latest = paths[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class OpsSnapshot:
    trading_enabled: bool
    futu_env: str
    experiment_id: str
    total_capital: float
    required_n: int
    counting_streak: int
    promotion_verdict: str
    lane_a_state: str
    lane_b_state: str
    lane_a_label: str
    lane_b_label: str
    last_session_date: str | None
    last_pnl_a: float | None
    last_pnl_b: float | None
    last_halts: int
    alert: str
    reports_dir: str
    data_mode: str  # demo_fixtures | live_reports | empty


def build_ops_snapshot(
    config_path: str | Path | None = None,
    *,
    reports_dir: Path | None = None,
) -> OpsSnapshot:
    root = _repo_root()
    cfg_path = Path(config_path) if config_path else root / "config" / "portfolio.yaml"
    config: PortfolioConfig = load_portfolio_config(cfg_path)
    rdir = reports_dir or resolve_reports_dir()
    promo = check_promotion(rdir, n=int(config.promotion.n_days))
    report = load_latest_report(rdir)

    demo = "fixtures" in str(rdir).replace("\\", "/")
    if not rdir.exists() or (promo.counting_streak == 0 and report is None):
        data_mode = "empty"
    elif demo:
        data_mode = "demo_fixtures"
    else:
        data_mode = "live_reports"

    lane_a = (report or {}).get("lane_a") or {}
    lane_b = (report or {}).get("lane_b") or {}
    capital = (report or {}).get("capital") or {}
    lane_a_state = str(lane_a.get("state") or "HUNT")
    lane_b_state = str(lane_b.get("state") or "IDLE")
    halts = len((report or {}).get("halts") or [])

    if config.trading_enabled:
        alert = "真下单总开关是开着的——请确认这是有意为之。"
    elif data_mode == "empty":
        alert = "还没有日报数据。系统在等模拟交易日产生报告。"
    elif promo.verdict != "PASS":
        alert = (
            f"练兵进度 {promo.counting_streak}/{promo.required_n}，"
            "还没达到可讨论真钱交易的门槛。"
        )
    elif halts:
        alert = f"最近一天有 {halts} 次停手记录，请留意。"
    else:
        alert = "一切正常。真下单仍关闭。"

    return OpsSnapshot(
        trading_enabled=bool(config.trading_enabled),
        futu_env=str(config.futu.env),
        experiment_id=str(config.experiment_id),
        total_capital=float(config.total_capital),
        required_n=int(promo.required_n),
        counting_streak=int(promo.counting_streak),
        promotion_verdict=str(promo.verdict),
        lane_a_state=lane_a_state,
        lane_b_state=lane_b_state,
        lane_a_label=human_lane_a(lane_a_state),
        lane_b_label=human_lane_b(lane_b_state),
        last_session_date=(report or {}).get("session_date"),
        last_pnl_a=_as_float(capital.get("lane_a_pnl")),
        last_pnl_b=_as_float(capital.get("lane_b_pnl")),
        last_halts=halts,
        alert=alert,
        reports_dir=str(rdir),
        data_mode=data_mode,
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def expected_dashboard_password() -> str | None:
    """Password from env; None means gate disabled (local default)."""
    raw = os.environ.get("DASHBOARD_PASSWORD")
    if raw is None:
        return None
    return raw
