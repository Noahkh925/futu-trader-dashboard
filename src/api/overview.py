"""Project OpsSnapshot into the PROH-120 overview view model."""

from __future__ import annotations

from typing import Any

from dashboard.ops import OpsSnapshot, format_clock_hhmm_zh, format_last_updated_zh

ENV_LABELS: dict[str, str] = {
    "staging": "练兵 · 模拟",
    "paper": "模拟盘",
    "prod": "正式环境",
    "production": "正式环境",
}

PROMOTION_LABELS: dict[str, str] = {
    "PASS": "达标，可讨论下一步",
    "FAIL": "还没达标",
    "PENDING": "统计中",
}

LANE_NAMES: dict[str, str] = {
    "A": "A · 趋势",
    "B": "B · 财报",
}


def format_money(value: float | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+${value:,.2f}"
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def _env_pill(env: str) -> str:
    return ENV_LABELS.get(str(env).strip().lower(), str(env))


def _promo_label(verdict: str) -> str:
    return PROMOTION_LABELS.get(str(verdict).upper(), str(verdict))


def _pnl_total(snap: OpsSnapshot) -> float | None:
    if snap.last_pnl_a is None and snap.last_pnl_b is None:
        return None
    return (snap.last_pnl_a or 0.0) + (snap.last_pnl_b or 0.0)


def _worry(snap: OpsSnapshot) -> tuple[str, str]:
    """Return (level, title) for the safety verdict band."""
    if snap.trading_enabled:
        return "urgent", "要担心：真钱通道开着"
    if snap.sync_status == "failed":
        return "urgent", "要担心：日报同步失败"
    if snap.sync_status == "stale":
        return "watch", "留意一下：数据可能过期"
    if snap.data_mode == "empty":
        return "watch", "先别慌：还没有练兵日报"
    if snap.opend_mode == "opend_sim_fallback_mock":
        return "watch", "留意一下：虚拟盘连接不顺"
    if snap.last_halts:
        return "watch", "留意一下：最近有停手记录"
    return "calm", "今天安全：真下单仍关着"


def _lane_tone(state: str) -> str:
    if state == "HALT":
        return "halt"
    if state in {"LOCKED", "DEPLOY", "SCOUT", "HUNT"}:
        return "busy"
    return "idle"


def _freshness_tone(sync_status: str) -> str:
    if sync_status == "failed":
        return "risk"
    if sync_status == "stale":
        return "watch"
    if sync_status == "ok":
        return "safe"
    return "watch"


def _freshness_label(snap: OpsSnapshot) -> str:
    tone_word = {
        "ok": "正常",
        "stale": "可能过期",
        "failed": "同步失败",
        "empty": "暂无数据",
    }.get(str(snap.sync_status), str(snap.sync_status))
    path = snap.data_path_zh or snap.connection_label_zh or ""
    bits = [f"数据新鲜度 · {tone_word}"]
    if path:
        bits.append(path)
    return " · ".join(bits)


def _lane_meta(snap: OpsSnapshot, lane: str) -> str:
    summary = snap.lane_a_summary if lane == "A" else snap.lane_b_summary
    if not isinstance(summary, dict):
        summary = {}
    if summary.get("halt_reason_zh"):
        return str(summary["halt_reason_zh"])
    if summary.get("note_zh"):
        return str(summary["note_zh"])
    positions = [p for p in (snap.positions or []) if str(p.get("lane")) == lane]
    if positions:
        syms = ", ".join(str(p.get("symbol") or "?") for p in positions[:3])
        return f"持仓 {syms}"
    status = snap.lane_a_status_zh if lane == "A" else snap.lane_b_status_zh
    return status or "暂无数据"


def _data_as_of_display(snap: OpsSnapshot) -> str:
    clock = format_clock_hhmm_zh(snap.data_as_of) or format_clock_hhmm_zh(snap.synced_at)
    if clock:
        return f"数据截至 今天 {clock}"
    full = format_last_updated_zh(snap.data_as_of or snap.synced_at)
    if full and full != "—":
        return f"数据截至 {full}"
    if snap.last_session_date:
        return f"数据截至 {snap.last_session_date}"
    return "数据截至 —"


def build_overview_view(
    snap: OpsSnapshot,
    *,
    dual_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the clickable overview payload (Chinese copy, read-only)."""
    level, title = _worry(snap)
    total = _pnl_total(snap)
    if total is None:
        pnl_tone = "flat"
    elif total > 0:
        pnl_tone = "gain"
    elif total < 0:
        pnl_tone = "loss"
    else:
        pnl_tone = "flat"

    remaining = max(0, int(snap.required_n) - int(snap.counting_streak))
    pct = 0.0
    if snap.required_n > 0:
        pct = min(100.0, 100.0 * float(snap.counting_streak) / float(snap.required_n))

    if snap.trading_enabled:
        kill_label = "真下单总开关：开着 — 真钱通道已打开（只读）"
        kill_tone = "risk"
    else:
        kill_label = "真下单总开关：关闭 — 不会真钱下单"
        kill_tone = "safe"

    demo = snap.data_mode == "demo_fixtures"
    alert_level = level if level != "calm" else None
    # Demo fixture banner must stay prominent (PROH-120).
    if demo and alert_level is None:
        alert_level = "watch"

    dual_badge = None
    if dual_run and dual_run.get("present"):
        dual_badge = {
            "label": "双跑对照中",
            "verdict": dual_run.get("verdict"),
            "session_date": dual_run.get("session_date"),
        }

    return {
        "schema": "overview_v1",
        "product": "作战台",
        "subtitle": "只读监盘",
        "env_pill": _env_pill(snap.futu_env),
        "data_as_of_zh": _data_as_of_display(snap),
        "demo": demo,
        "demo_banner": (
            "当前是演示数据（示例），不是真实日报" if demo else None
        ),
        "verdict": {
            "level": level,
            "title": title,
            "detail": snap.alert,
        },
        "kill_switch": {
            "trading_enabled": bool(snap.trading_enabled),
            "tone": kill_tone,
            "label": kill_label,
            "hint": "只读 · 本页不能打开",
            # Explicit: UI must never render a write control for this.
            "writable": False,
        },
        "drill": {
            "counting_streak": int(snap.counting_streak),
            "required_n": int(snap.required_n),
            "remaining": remaining,
            "pct": round(pct, 1),
            "verdict": snap.promotion_verdict,
            "verdict_zh": _promo_label(snap.promotion_verdict),
            "caption": (
                f"结果：{_promo_label(snap.promotion_verdict)}"
                + (f" · 还差 {remaining} 天" if remaining else "")
            ),
        },
        "pnl": {
            "total": total,
            "total_zh": format_money(total),
            "lane_a": snap.last_pnl_a,
            "lane_b": snap.last_pnl_b,
            "lane_a_zh": format_money(snap.last_pnl_a),
            "lane_b_zh": format_money(snap.last_pnl_b),
            "session_date": snap.last_session_date,
            "tone": pnl_tone,
        },
        "lanes": [
            {
                "id": "A",
                "label": LANE_NAMES["A"],
                "state": snap.lane_a_state,
                "status_zh": snap.lane_a_status_zh or snap.lane_a_label,
                "meta": _lane_meta(snap, "A"),
                "tone": _lane_tone(snap.lane_a_state),
            },
            {
                "id": "B",
                "label": LANE_NAMES["B"],
                "state": snap.lane_b_state,
                "status_zh": snap.lane_b_status_zh or snap.lane_b_label,
                "meta": _lane_meta(snap, "B"),
                "tone": _lane_tone(snap.lane_b_state),
            },
        ],
        "freshness": {
            "sync_status": snap.sync_status,
            "tone": _freshness_tone(str(snap.sync_status)),
            "label_zh": _freshness_label(snap),
            "health_hint_zh": snap.health_hint_zh,
            "hosting_mode": snap.hosting_mode,
            "data_mode": snap.data_mode,
            "opend_mode": snap.opend_mode,
            "opend_reachable": snap.opend_reachable,
        },
        "dual_run": dual_badge,
        "alert_level": alert_level,
        "secondary": {
            "positions": list(snap.positions or []),
            "fills_count": len(snap.fills or []),
            "halts": int(snap.last_halts),
            "experiment_id": snap.experiment_id,
            "reports_dir": snap.reports_dir,
        },
        # Raw ops fields kept for Stage 4/5 pages; overview SPA may ignore.
        "ops": snap.to_dict(),
    }
