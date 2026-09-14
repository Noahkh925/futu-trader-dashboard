"""Strategy page view: decision transparency + param co-pilot summary (PROH-128).

Decision model is rule → evidence → conclusion in plain Chinese.
Param edits only bind paper/staging via ParamVersionService (never live).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dashboard.ops import (
    SIDE_ZH,
    build_fills,
    human_halt_reason,
    human_lane_a,
    human_lane_b,
    lane_a_status_zh,
    lane_b_status_zh,
    load_latest_report,
)
from params.model import ParamPayload
from params.service import ParamVersionService
from params.store import FileParamVersionStore, default_store_root

LANE_RULE_ZH: dict[str, str] = {
    "A": "开盘区间突破 + VWAP 过滤（趋势路）",
    "B": "财报事件窗口 + 期权结构部署（事件路）",
}

LANE_RULE_DETAIL: dict[str, str] = {
    "A": "规则：开盘后区间突破，且价格在 VWAP 同侧，才允许开仓。",
    "B": "规则：财报日前部署窗口内，用跨式/宽跨式结构部署；冷却期不开新仓。",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def param_store_root() -> Path:
    raw = __import__("os").environ.get("CONSOLE_PARAM_ROOT", "").strip()
    if raw:
        return Path(raw)
    return default_store_root(_repo_root())


def param_service(*, root: Path | None = None) -> ParamVersionService:
    return ParamVersionService(store=FileParamVersionStore(root or param_store_root()))


def load_portfolio_baseline(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or (_repo_root() / "config" / "portfolio.yaml")
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def summarize_params(payload: dict[str, Any] | ParamPayload | None) -> dict[str, Any]:
    """Readonly summary of co-pilot knobs for the strategy page."""
    if isinstance(payload, ParamPayload):
        body = payload.to_dict()
    else:
        body = dict(payload or {})
    lane_a = dict(body.get("lane_a") or {})
    lane_b = dict(body.get("lane_b") or {})
    risk = dict(body.get("risk") or {})
    la_risk = dict(lane_a.get("risk") or {})
    # Flat risk overlay may also carry max_position_pct
    max_pos = la_risk.get("max_position_pct", risk.get("max_position_pct"))
    max_daily = la_risk.get("max_daily_loss_pct", risk.get("max_daily_loss_pct"))
    max_trade = la_risk.get("max_loss_per_trade_pct", risk.get("max_loss_per_trade_pct"))
    watchlist = body.get("watchlist") if isinstance(body.get("watchlist"), dict) else {}
    symbols = watchlist.get("symbols") if isinstance(watchlist.get("symbols"), list) else None
    return {
        "experiment_id": str(body.get("experiment_id") or "baseline"),
        "lane_a_symbol": lane_a.get("symbol"),
        "max_position_pct": max_pos,
        "max_daily_loss_pct": max_daily,
        "max_loss_per_trade_pct": max_trade,
        "lane_b_max_notional_pct": lane_b.get("max_notional_pct"),
        "lane_b_daily_loss_limit": lane_b.get("daily_loss_limit"),
        "lane_b_max_event_loss": lane_b.get("max_event_loss"),
        "watchlist_symbols": symbols,
        "rows_zh": _summary_rows_zh(
            experiment_id=str(body.get("experiment_id") or "baseline"),
            max_pos=max_pos,
            max_daily=max_daily,
            max_trade=max_trade,
            lane_b_notional=lane_b.get("max_notional_pct"),
            lane_b_daily=lane_b.get("daily_loss_limit"),
            lane_b_event=lane_b.get("max_event_loss"),
            symbols=symbols,
            lane_a_symbol=lane_a.get("symbol"),
        ),
    }


def _summary_rows_zh(
    *,
    experiment_id: str,
    max_pos: Any,
    max_daily: Any,
    max_trade: Any,
    lane_b_notional: Any,
    lane_b_daily: Any,
    lane_b_event: Any,
    symbols: list[Any] | None,
    lane_a_symbol: Any,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = [
        {"label": "实验 ID", "value": experiment_id},
    ]
    if lane_a_symbol:
        rows.append({"label": "A 标的", "value": str(lane_a_symbol)})
    if symbols:
        rows.append({"label": "名单", "value": ", ".join(str(s) for s in symbols)})
    if max_pos is not None:
        rows.append({"label": "A 仓位上限", "value": f"{float(max_pos) * 100:.0f}%"})
    if max_daily is not None:
        rows.append({"label": "A 日亏上限", "value": f"{float(max_daily) * 100:.0f}%"})
    if max_trade is not None:
        rows.append({"label": "A 单笔亏上限", "value": f"{float(max_trade) * 100:.0f}%"})
    if lane_b_notional is not None:
        rows.append({"label": "B 名义本金上限", "value": f"{float(lane_b_notional) * 100:.0f}%"})
    if lane_b_daily is not None:
        rows.append({"label": "B 日亏限额", "value": f"${float(lane_b_daily):,.0f}"})
    if lane_b_event is not None:
        rows.append({"label": "B 事件亏限额", "value": f"${float(lane_b_event):,.0f}"})
    return rows


def baseline_payload_from_config(config: dict[str, Any]) -> dict[str, Any]:
    lane_a = dict(config.get("lane_a") or {})
    lane_b = dict(config.get("lane_b") or {})
    # Keep only co-pilot-relevant subsets for form defaults.
    la_out: dict[str, Any] = {}
    if lane_a.get("symbol"):
        la_out["symbol"] = lane_a["symbol"]
    if isinstance(lane_a.get("risk"), dict):
        la_out["risk"] = dict(lane_a["risk"])
    lb_out: dict[str, Any] = {}
    for key in (
        "max_notional_pct",
        "daily_loss_limit",
        "max_event_loss",
        "structure",
        "horizon_days",
        "deploy_window_days",
        "cooldown_days",
    ):
        if key in lane_b:
            lb_out[key] = lane_b[key]
    return {
        "experiment_id": str(config.get("experiment_id") or "baseline"),
        "lane_a": la_out,
        "lane_b": lb_out,
        "risk": dict(la_out.get("risk") or {}),
    }


def _money_bit(fill: dict[str, Any]) -> str:
    notional = fill.get("notional")
    qty = fill.get("qty")
    price = fill.get("price")
    if notional is not None:
        return f"约 ${float(notional):,.0f}"
    if qty is not None and price is not None:
        return f"{float(qty):g}×${float(price):,.2f}"
    return ""


def _decision_from_fill(fill: dict[str, Any], *, lane_conclusion: str) -> dict[str, Any]:
    lane = str(fill.get("lane") or "?").upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    side = fill.get("side_zh") or SIDE_ZH.get(str(fill.get("side") or "").upper()) or "成交"
    sym = fill.get("symbol") or "未标明"
    money = _money_bit(fill)
    mode = fill.get("mode_zh") or ""
    evidence_parts = [f"{side} {sym}"]
    if money:
        evidence_parts.append(money)
    if mode and mode != "—":
        evidence_parts.append(mode)
    if fill.get("reason"):
        evidence_parts.append(str(fill["reason"]))
    return {
        "id": f"fill:{lane}:{fill.get('ts')}:{sym}",
        "ts": fill.get("ts"),
        "lane": lane,
        "kind": "fill",
        "rule_zh": LANE_RULE_ZH.get(lane, "策略规则"),
        "rule_detail_zh": LANE_RULE_DETAIL.get(lane, ""),
        "evidence_zh": " · ".join(evidence_parts),
        "conclusion_zh": lane_conclusion or f"路线 {lane} 已执行成交",
        "symbol": sym,
        "side_zh": side,
    }


def _decision_from_halt(halt: dict[str, Any], *, session_ts: str | None) -> dict[str, Any]:
    lane_raw = str(halt.get("lane") or "?").upper()
    lane = "A" if lane_raw in {"A", "LANE_A"} else "B" if lane_raw in {"B", "LANE_B"} else lane_raw
    reason = human_halt_reason(halt.get("reason"))
    return {
        "id": f"halt:{lane}:{halt.get('reason')}:{session_ts}",
        "ts": halt.get("ts") or session_ts,
        "lane": lane,
        "kind": "halt",
        "rule_zh": "风控限额（停手）",
        "rule_detail_zh": "规则：触及日亏 / 单笔 / 名义本金等限额时停手，不再开新仓。",
        "evidence_zh": reason,
        "conclusion_zh": f"路线 {lane} 已暂停（停手）— {reason}",
        "symbol": None,
        "side_zh": None,
    }


def _decision_lane_state(
    *,
    lane: str,
    state_zh: str,
    session_ts: str | None,
    extra_evidence: str | None = None,
) -> dict[str, Any]:
    evidence = extra_evidence or "当日无成交明细；以下为路线状态摘要。"
    return {
        "id": f"state:{lane}:{session_ts}",
        "ts": session_ts,
        "lane": lane,
        "kind": "state",
        "rule_zh": LANE_RULE_ZH.get(lane, "策略规则"),
        "rule_detail_zh": LANE_RULE_DETAIL.get(lane, ""),
        "evidence_zh": evidence,
        "conclusion_zh": state_zh,
        "symbol": None,
        "side_zh": None,
    }


def build_decisions(
    report: dict[str, Any] | None,
    *,
    reports_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build rule → evidence → conclusion timeline for the latest session."""
    if not report:
        return []

    session_ts = str(report.get("generated_at") or report.get("session_date") or "") or None
    fills = build_fills(report, reports_dir=reports_dir)
    a_zh = lane_a_status_zh(report)
    b_zh = lane_b_status_zh(report)
    decisions: list[dict[str, Any]] = []

    for fill in fills:
        lane = str(fill.get("lane") or "").upper()
        conclusion = a_zh if lane == "A" else b_zh if lane == "B" else "已执行"
        decisions.append(_decision_from_fill(fill, lane_conclusion=conclusion))

    for halt in report.get("halts") or []:
        if isinstance(halt, dict):
            decisions.append(_decision_from_halt(halt, session_ts=session_ts))

    # Lane state cards when quiet / for context
    lane_a = report.get("lane_a") if isinstance(report.get("lane_a"), dict) else {}
    lane_b = report.get("lane_b") if isinstance(report.get("lane_b"), dict) else {}
    has_a_fill = any(str(d.get("lane")) == "A" and d.get("kind") == "fill" for d in decisions)
    has_b_fill = any(str(d.get("lane")) == "B" and d.get("kind") == "fill" for d in decisions)

    if not has_a_fill:
        bars = lane_a.get("bars_seen")
        signals = lane_a.get("signals_count")
        bits = []
        if bars is not None:
            bits.append(f"看过 {bars} 根 K 线")
        if signals is not None:
            bits.append(f"信号计数 {signals}")
        state = str(lane_a.get("state") or "")
        if state:
            bits.append(f"状态码对照：{human_lane_a(state)}")
        decisions.append(
            _decision_lane_state(
                lane="A",
                state_zh=a_zh,
                session_ts=session_ts,
                extra_evidence="；".join(bits) if bits else None,
            )
        )

    if not has_b_fill:
        scout = lane_b.get("scout_hits")
        day_mode = lane_b.get("day_mode")
        bits = []
        if scout is not None:
            bits.append(f"侦察命中 {scout} 次")
        if day_mode:
            bits.append(f"日模式 {day_mode}")
        state = str(lane_b.get("state") or "")
        if state:
            bits.append(f"状态码对照：{human_lane_b(state)}")
        decisions.append(
            _decision_lane_state(
                lane="B",
                state_zh=b_zh,
                session_ts=session_ts,
                extra_evidence="；".join(bits) if bits else None,
            )
        )

    decisions.sort(key=lambda d: str(d.get("ts") or ""), reverse=True)
    return decisions


def build_strategy_view(
    *,
    reports_dir: Path | None = None,
    svc: ParamVersionService | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Full strategy page payload for GET /api/v1/strategy."""
    report = load_latest_report(reports_dir) if reports_dir is not None else None
    if report is None and reports_dir is not None:
        # Empty dir still ok
        report = None

    config = load_portfolio_baseline(config_path)
    service = svc or param_service()
    active = service.active_staging()
    baseline_payload = baseline_payload_from_config(config)

    if active and isinstance(active.get("payload"), dict):
        current_payload = active["payload"]
        source = "active_staging"
        active_id = active.get("id")
        target_env = active.get("target_env")
        causal = active.get("causal_summary") or active.get("note") or ""
    else:
        current_payload = baseline_payload
        source = "portfolio_baseline"
        active_id = None
        target_env = "staging"
        causal = "尚未写入参数版本；显示 portfolio.yaml 基线（仍不开真钱）。"

    versions = service.list_versions()
    decisions = build_decisions(report, reports_dir=reports_dir)
    session_date = str(report.get("session_date")) if report else None

    one_liner = "今天还没有可解读的决策。"
    if decisions:
        top = decisions[0]
        one_liner = (
            f"{top.get('conclusion_zh') or '系统在运行'} （依据：{top.get('rule_zh') or '规则'}）"
        )
    elif report:
        one_liner = f"A：{lane_a_status_zh(report)}；B：{lane_b_status_zh(report)}"

    return {
        "session_date": session_date,
        "one_liner_zh": one_liner,
        "decisions": decisions,
        "params": {
            "source": source,
            "active_version_id": active_id,
            "target_env": target_env,
            "causal_summary": causal,
            "summary": summarize_params(current_payload),
            "form_defaults": _form_defaults(current_payload),
            "disclaimer_zh": "调参只会进练兵环境（paper/staging），不会碰到真钱。",
            "save_button_zh": "保存到练兵环境",
            "confirm_zh": "只会进练兵环境，不会碰到真钱。确认保存为新版本？",
            "success_zh": "已进 staging 队列，未开真钱。",
        },
        "versions": versions,
        "safety": {
            "live_enable_endpoints": False,
            "trading_enabled_writable": False,
            "allowed_target_envs": ["paper", "staging"],
        },
    }


def _form_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload or {})
    lane_a = dict(body.get("lane_a") or {})
    lane_b = dict(body.get("lane_b") or {})
    risk = dict(lane_a.get("risk") or body.get("risk") or {})
    watchlist = body.get("watchlist") if isinstance(body.get("watchlist"), dict) else {}
    symbols = watchlist.get("symbols") if isinstance(watchlist.get("symbols"), list) else []
    return {
        "experiment_id": str(body.get("experiment_id") or "baseline"),
        "max_position_pct": risk.get("max_position_pct"),
        "max_daily_loss_pct": risk.get("max_daily_loss_pct"),
        "max_loss_per_trade_pct": risk.get("max_loss_per_trade_pct"),
        "lane_b_max_notional_pct": lane_b.get("max_notional_pct"),
        "lane_b_daily_loss_limit": lane_b.get("daily_loss_limit"),
        "lane_b_max_event_loss": lane_b.get("max_event_loss"),
        "watchlist_symbols_text": ", ".join(str(s) for s in symbols) if symbols else "",
        "note": "",
        "causal_summary": "",
    }


def payload_from_form(form: dict[str, Any]) -> dict[str, Any]:
    """Map co-pilot form fields into a ParamPayload-shaped dict."""

    def _float(key: str) -> float | None:
        raw = form.get(key)
        if raw is None or raw == "":
            return None
        return float(raw)

    risk: dict[str, Any] = {}
    for src, dest in (
        ("max_position_pct", "max_position_pct"),
        ("max_daily_loss_pct", "max_daily_loss_pct"),
        ("max_loss_per_trade_pct", "max_loss_per_trade_pct"),
    ):
        val = _float(src)
        if val is not None:
            risk[dest] = val

    lane_b: dict[str, Any] = {}
    notional = _float("lane_b_max_notional_pct")
    if notional is not None:
        lane_b["max_notional_pct"] = notional
    daily = _float("lane_b_daily_loss_limit")
    if daily is not None:
        lane_b["daily_loss_limit"] = daily
    event = _float("lane_b_max_event_loss")
    if event is not None:
        lane_b["max_event_loss"] = event

    symbols_text = str(form.get("watchlist_symbols_text") or "").strip()
    watchlist = None
    if symbols_text:
        symbols = [s.strip() for s in symbols_text.replace(";", ",").split(",") if s.strip()]
        if symbols:
            watchlist = {"symbols": symbols}

    out: dict[str, Any] = {
        "experiment_id": str(form.get("experiment_id") or "baseline").strip() or "baseline",
        "lane_a": {"risk": risk} if risk else {},
        "lane_b": lane_b,
        "risk": dict(risk),
    }
    if watchlist is not None:
        out["watchlist"] = watchlist
    return out
