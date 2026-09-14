"""Market page view: position/candidate pulse + alert inbox (PROH-129).

Scoped to holdings and candidates — not a full-market noise feed.
No live-enable or trading_enabled write surface.
"""

from __future__ import annotations

from typing import Any

from api.overview import format_money
from dashboard.ops import SIDE_ZH, OpsSnapshot


def _side_zh(raw: Any) -> str:
    key = str(raw or "").upper()
    return SIDE_ZH.get(key, str(raw or ""))


def _pulse_from_position(pos: dict[str, Any]) -> dict[str, Any]:
    lane = str(pos.get("lane") or "?").upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    symbol = str(pos.get("symbol") or "未标明")
    side = _side_zh(pos.get("side"))
    qty = pos.get("qty")
    notional = pos.get("notional")
    upnl = pos.get("unrealized_pnl")
    note = pos.get("note")
    bits: list[str] = []
    if side:
        bits.append(side)
    if qty is not None:
        bits.append(f"数量 {float(qty):g}")
    if notional is not None:
        bits.append(f"名义约 ${float(notional):,.0f}")
    if upnl is not None:
        bits.append(f"浮盈亏 {format_money(float(upnl))}")
    if note:
        bits.append(str(note))
    return {
        "id": f"pos:{lane}:{symbol}",
        "kind": "position",
        "lane": lane,
        "symbol": symbol,
        "title_zh": f"持仓 · {symbol}",
        "status_zh": "持仓中",
        "detail_zh": " · ".join(bits) if bits else "持仓明细有限",
        "tone": "busy",
    }


def _pulse_from_candidate(row: dict[str, Any], *, vetoed: bool) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "未标明")
    score = row.get("score")
    reason = row.get("reason") or ""
    if isinstance(row.get("reasons"), list) and not reason:
        reason = "；".join(str(r) for r in row["reasons"][:3])
    if vetoed:
        return {
            "id": f"cand:veto:{symbol}",
            "kind": "candidate",
            "lane": "A",
            "symbol": symbol,
            "title_zh": f"候选 · {symbol}",
            "status_zh": "已否决",
            "detail_zh": reason or "盘前否决，今日不进场",
            "tone": "idle",
            "score": score,
        }
    score_bit = f"得分 {float(score):.2f}" if score is not None else "可交易"
    return {
        "id": f"cand:ok:{symbol}",
        "kind": "candidate",
        "lane": "A",
        "symbol": symbol,
        "title_zh": f"候选 · {symbol}",
        "status_zh": "留意",
        "detail_zh": " · ".join(x for x in [score_bit, reason] if x),
        "tone": "watch",
        "score": score,
    }


def build_pulse(snap: OpsSnapshot) -> list[dict[str, Any]]:
    """Positions first, then deployable candidates, then a few vetoed (context)."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pos in snap.positions or []:
        item = _pulse_from_position(pos)
        if item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        items.append(item)

    wl = snap.lane_a_tech_watchlist or {}
    for row in list(wl.get("deployable") or [])[:8]:
        item = _pulse_from_candidate(row, vetoed=False)
        if item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        items.append(item)
    for row in list(wl.get("vetoed") or [])[:3]:
        item = _pulse_from_candidate(row, vetoed=True)
        if item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        items.append(item)

    # Lane preference symbols when no explicit open positions / watchlist.
    if not items:
        la_sym = None
        lb_sym = None
        if isinstance(snap.lane_a_summary, dict):
            la_sym = snap.lane_a_summary.get("symbol")
        if isinstance(snap.lane_b_summary, dict):
            lb_sym = snap.lane_b_summary.get("prefer_symbol") or snap.lane_b_summary.get(
                "symbol"
            )
        for lane, sym, status in (
            ("A", la_sym, snap.lane_a_status_zh or snap.lane_a_label),
            ("B", lb_sym, snap.lane_b_status_zh or snap.lane_b_label),
        ):
            if not sym:
                continue
            items.append(
                {
                    "id": f"lane:{lane}:{sym}",
                    "kind": "lane_focus",
                    "lane": lane,
                    "symbol": str(sym),
                    "title_zh": f"路线 {lane} · {sym}",
                    "status_zh": status or "关注中",
                    "detail_zh": "今日关注标的（尚无持仓明细）",
                    "tone": "idle",
                }
            )
    return items


def build_alerts(snap: OpsSnapshot) -> list[dict[str, Any]]:
    """Inbox rows: severity + one-line reason + next step (Chinese)."""
    alerts: list[dict[str, Any]] = []

    def add(
        *,
        severity: str,
        reason_zh: str,
        next_zh: str,
        source: str,
    ) -> None:
        alerts.append(
            {
                "id": f"{source}:{len(alerts)}",
                "severity": severity,  # urgent | watch | info
                "reason_zh": reason_zh,
                "next_zh": next_zh,
                "source": source,
            }
        )

    if snap.trading_enabled:
        add(
            severity="urgent",
            reason_zh="真下单总开关是开着的",
            next_zh="若非有意为之，立刻在本机关闭真钱通道；本页不能改开关。",
            source="kill_switch",
        )
    if snap.sync_status == "failed":
        add(
            severity="urgent",
            reason_zh=snap.sync_error or "日报同步失败",
            next_zh="检查本机日跑与同步任务；不要把旧快照当最新实况。",
            source="sync",
        )
    elif snap.sync_status == "stale":
        add(
            severity="watch",
            reason_zh=snap.sync_error or "日报可能过期",
            next_zh="确认最近一次同步时间；必要时手动刷新。",
            source="sync",
        )
    if snap.data_mode == "demo_fixtures":
        add(
            severity="watch",
            reason_zh="当前是演示数据（示例），不是真实日报",
            next_zh="接真实 reports 目录后再做监盘决策。",
            source="demo",
        )
    if snap.data_mode == "empty":
        add(
            severity="watch",
            reason_zh="还没有练兵日报",
            next_zh="等模拟交易日跑完，或检查 reports 目录路径。",
            source="empty",
        )
    if snap.opend_mode == "opend_sim_fallback_mock":
        add(
            severity="watch",
            reason_zh="虚拟盘连接不顺，回退成了本地假成交",
            next_zh="检查本机 OpenD；该日通常不计入练兵。",
            source="opend",
        )
    if snap.last_halts:
        add(
            severity="watch",
            reason_zh=f"最近一天有 {snap.last_halts} 次停手记录",
            next_zh="打开复盘页看停手原因；必要时收紧风控参数（只进练兵）。",
            source="halts",
        )

    wl = snap.lane_a_tech_watchlist or {}
    if wl.get("no_trade_day"):
        add(
            severity="info",
            reason_zh=str(wl.get("reason_zh") or "今日 Lane A 不交易"),
            next_zh="看名单否决原因；不必强行开仓。",
            source="watchlist",
        )
    elif wl.get("status") in {"missing", "bad_schema"}:
        add(
            severity="watch",
            reason_zh=str(wl.get("reason_zh") or "Lane A 技术面名单不可用"),
            next_zh="确认盘前名单文件已生成且 schema 正确（fail-closed）。",
            source="watchlist",
        )

    for event in list(snap.events or [])[:5]:
        kind = str(event.get("kind") or event.get("type") or "event")
        detail = str(event.get("detail_zh") or event.get("detail") or event.get("message") or "")
        if not detail:
            continue
        if kind in {"halt", "error", "disconnect"}:
            sev = "watch" if kind != "error" else "urgent"
            add(
                severity=sev,
                reason_zh=detail,
                next_zh="对照复盘页与技术细节，确认是否需停手观察。",
                source=kind,
            )

    # Deduplicate by reason text while preserving order.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in alerts:
        key = row["reason_zh"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _one_liner(pulse: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> str:
    urgent = [a for a in alerts if a["severity"] == "urgent"]
    watch = [a for a in alerts if a["severity"] == "watch"]
    if urgent:
        return f"今天该先处理：{urgent[0]['reason_zh']}"
    if watch:
        return f"今天留意：{watch[0]['reason_zh']}"
    pos_n = sum(1 for p in pulse if p["kind"] == "position")
    cand_n = sum(1 for p in pulse if p["kind"] == "candidate" and p["status_zh"] == "留意")
    if pos_n or cand_n:
        return f"围绕持仓与候选：持仓 {pos_n} · 候选 {cand_n}，暂无紧急告警。"
    return "今天暂无持仓/候选脉搏，也没有紧急告警。"


def build_market_view(snap: OpsSnapshot) -> dict[str, Any]:
    pulse = build_pulse(snap)
    alerts = build_alerts(snap)
    wl = snap.lane_a_tech_watchlist or {}
    return {
        "schema": "market_v1",
        "product": "作战台",
        "page": "市场",
        "subtitle": "持仓/候选脉搏 · 告警收件箱",
        "session_date": snap.last_session_date,
        "env_pill": {
            "staging": "练兵 · 模拟",
            "paper": "模拟盘",
            "prod": "正式环境",
            "production": "正式环境",
        }.get(str(snap.futu_env).lower(), str(snap.futu_env)),
        "one_liner_zh": _one_liner(pulse, alerts),
        "pulse": pulse,
        "alerts": alerts,
        "alert_counts": {
            "urgent": sum(1 for a in alerts if a["severity"] == "urgent"),
            "watch": sum(1 for a in alerts if a["severity"] == "watch"),
            "info": sum(1 for a in alerts if a["severity"] == "info"),
        },
        "watchlist_meta": {
            "status": wl.get("status"),
            "reason_zh": wl.get("reason_zh"),
            "as_of": wl.get("as_of"),
            "no_trade_day": bool(wl.get("no_trade_day")),
        },
        "reference_indices": {
            "collapsed": True,
            "note_zh": "参考指数非本页主线；后续可接行情源。当前不做全市场噪音。",
            "items": [],
        },
        "demo": snap.data_mode == "demo_fixtures",
        "demo_banner": (
            "当前是演示数据（示例），不是真实日报"
            if snap.data_mode == "demo_fixtures"
            else None
        ),
    }
