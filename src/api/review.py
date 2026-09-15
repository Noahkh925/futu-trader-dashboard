"""Daily review one-pager view (PROH-129).

Answers: how did yesterday go, does it count for drill, any halt reasons,
and a copyable Chinese summary. No live-enable surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.overview import format_money
from dashboard.ops import (
    SIDE_ZH,
    build_fills,
    build_positions,
    build_promotion_calendar,
    human_excluded_reason,
    human_halt_reason,
    lane_a_status_zh,
    lane_b_status_zh,
    load_report_rows,
)
from research.promotion import evaluate_day


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_by_date(
    reports: list[dict[str, Any]],
    session_date: str | None,
) -> dict[str, Any] | None:
    if not reports:
        return None
    if session_date:
        for row in reports:
            if str(row.get("session_date") or "") == session_date:
                return row
        return None
    return reports[-1]


def _lane_block(report: dict[str, Any], lane: str) -> dict[str, Any]:
    raw = report.get("lane_a") if lane == "A" else report.get("lane_b")
    raw = raw if isinstance(raw, dict) else {}
    state = str(raw.get("state") or "—")
    status = lane_a_status_zh(report) if lane == "A" else lane_b_status_zh(report)
    halt = raw.get("halt_reason")
    return {
        "lane": lane,
        "state": state,
        "status_zh": status,
        "fills_count": int(raw.get("fills_count") or 0),
        "realized_pnl": _as_float(raw.get("realized_pnl")),
        "realized_pnl_zh": format_money(_as_float(raw.get("realized_pnl"))),
        "halt_reason": halt,
        "halt_reason_zh": human_halt_reason(halt) if halt else None,
        "symbol": raw.get("symbol") or raw.get("prefer_symbol"),
    }


def _halt_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for halt in report.get("halts") or []:
        if not isinstance(halt, dict):
            continue
        reason = halt.get("reason") or halt.get("halt_reason") or "unspecified"
        lane = str(halt.get("lane") or "?").upper()
        rows.append(
            {
                "lane": lane,
                "reason": reason,
                "reason_zh": human_halt_reason(str(reason)),
                "detail_zh": str(halt.get("detail") or halt.get("detail_zh") or ""),
            }
        )
    # Also surface lane-level halt_reason when halts[] empty.
    if not rows:
        for lane in ("A", "B"):
            block = _lane_block(report, lane)
            if block.get("halt_reason"):
                rows.append(
                    {
                        "lane": lane,
                        "reason": block["halt_reason"],
                        "reason_zh": block["halt_reason_zh"],
                        "detail_zh": "",
                    }
                )
    return rows


def _fill_summaries(report: dict[str, Any], *, reports_dir: Path | None) -> list[str]:
    fills = build_fills(report, reports_dir=reports_dir)
    lines: list[str] = []
    for fill in fills[:8]:
        lane = str(fill.get("lane") or "?")
        side = fill.get("side_zh") or SIDE_ZH.get(str(fill.get("side") or "").upper()) or ""
        sym = fill.get("symbol") or "未标明"
        money = ""
        if fill.get("notional") is not None:
            money = f" · 约 ${float(fill['notional']):,.0f}"
        lines.append(f"路线 {lane}：{side} {sym}{money}")
    return lines


def build_day_sheet(
    report: dict[str, Any],
    *,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    capital = report.get("capital") if isinstance(report.get("capital"), dict) else {}
    pnl_a = _as_float(capital.get("lane_a_pnl"))
    pnl_b = _as_float(capital.get("lane_b_pnl"))
    total = None if pnl_a is None and pnl_b is None else (pnl_a or 0.0) + (pnl_b or 0.0)
    try:
        verdict = evaluate_day(report)
        counts = bool(verdict.counts)
        excluded = None if counts else human_excluded_reason(verdict.reason)
        opend_mode = verdict.opend_mode
        halt_count = int(verdict.halts)
    except (KeyError, TypeError, ValueError):
        counts = bool(report.get("counts_for_promotion"))
        excluded = None if counts else human_excluded_reason("unknown")
        opend_mode = str(report.get("opend_mode") or "—")
        halt_count = len(report.get("halts") or [])

    if total is None:
        tone = "flat"
    elif total > 0:
        tone = "gain"
    elif total < 0:
        tone = "loss"
    else:
        tone = "flat"

    positions = build_positions(report)
    halts = _halt_rows(report)
    session = str(report.get("session_date") or "")

    if counts:
        drill_zh = "计入练兵日"
    else:
        drill_zh = f"不计入练兵 · {excluded or '原因未注明'}"

    headline = f"{session or '该日'}：{format_money(total)} · {drill_zh}"
    if halt_count:
        headline += f" · 停手 {halt_count} 次"

    return {
        "session_date": session,
        "headline_zh": headline,
        "pnl": {
            "total": total,
            "total_zh": format_money(total),
            "lane_a": pnl_a,
            "lane_b": pnl_b,
            "lane_a_zh": format_money(pnl_a),
            "lane_b_zh": format_money(pnl_b),
            "tone": tone,
        },
        "counts_for_promotion": counts,
        "drill_zh": drill_zh,
        "excluded_reason_zh": excluded,
        "opend_mode": opend_mode,
        "futu_env": report.get("futu_env"),
        "experiment_id": report.get("experiment_id"),
        "lanes": [_lane_block(report, "A"), _lane_block(report, "B")],
        "halts": halts,
        "halt_count": halt_count,
        "positions": positions,
        "fills_zh": _fill_summaries(report, reports_dir=reports_dir),
        "fills_count": len(report.get("fills") or [])
        or int((report.get("fills_summary") or {}).get("total") or 0),
    }


def build_summary_zh(sheet: dict[str, Any], *, calendar: list[dict[str, Any]]) -> str:
    """Human-readable one-pager text for copy/export (not a JSON dump)."""
    lines = [
        "【作战台 · 日复盘】",
        sheet.get("headline_zh") or "—",
        "",
        f"赚亏合计：{sheet['pnl']['total_zh']}",
        f"  A {sheet['pnl']['lane_a_zh']} · B {sheet['pnl']['lane_b_zh']}",
        f"练兵：{sheet.get('drill_zh')}",
        f"OpenD 模式：{sheet.get('opend_mode')}",
        "",
        "双路：",
    ]
    for lane in sheet.get("lanes") or []:
        lines.append(
            f"  {lane['lane']} · {lane['status_zh']}"
            + (f"（{lane['symbol']}）" if lane.get("symbol") else "")
        )
        if lane.get("halt_reason_zh"):
            lines.append(f"    停手：{lane['halt_reason_zh']}")
    if sheet.get("halts"):
        lines.append("")
        lines.append("停手明细：")
        for h in sheet["halts"]:
            lines.append(f"  路线 {h['lane']}：{h['reason_zh']}")
    if sheet.get("fills_zh"):
        lines.append("")
        lines.append("成交摘要：")
        lines.extend(f"  {x}" for x in sheet["fills_zh"])
    counted = sum(1 for d in calendar if d.get("counts_for_promotion"))
    lines.append("")
    lines.append(f"近期日历：计入 {counted} / 展示 {len(calendar)} 天")
    lines.append("（本摘要给人看；真下单默认关，调参只进练兵。）")
    return "\n".join(lines)


def build_review_view(
    reports_dir: Path | None,
    *,
    session_date: str | None = None,
    market: str | None = None,
) -> dict[str, Any]:
    reports = (
        load_report_rows(reports_dir, market=market) if reports_dir else []
    )
    available = [str(r.get("session_date") or "") for r in reports if r.get("session_date")]
    report = _report_by_date(reports, session_date)
    calendar = build_promotion_calendar(reports) if reports else []

    if report is None:
        return {
            "schema": "review_v1",
            "product": "作战台",
            "page": "复盘",
            "subtitle": "日复盘一页纸",
            "market": market,
            "available_dates": available,
            "selected_date": session_date,
            "empty": True,
            "empty_zh": "还没有可复盘的日报。跑完练兵日或接上 reports 目录后再看。",
            "sheet": None,
            "calendar": calendar,
            "summary_zh": "还没有可复盘的日报。",
            "demo": bool(reports_dir and "fixtures" in str(reports_dir).replace("\\", "/")),
        }

    sheet = build_day_sheet(report, reports_dir=reports_dir)
    summary = build_summary_zh(sheet, calendar=calendar)
    demo = bool(reports_dir and "fixtures" in str(reports_dir).replace("\\", "/"))
    return {
        "schema": "review_v1",
        "product": "作战台",
        "page": "复盘",
        "subtitle": "日复盘一页纸",
        "market": market,
        "available_dates": list(reversed(available)),
        "selected_date": sheet["session_date"],
        "empty": False,
        "empty_zh": None,
        "sheet": sheet,
        "calendar": list(reversed(calendar)),
        "summary_zh": summary,
        "demo": demo,
        "demo_banner": "当前是演示数据（示例），不是真实日报" if demo else None,
        "copy_hint_zh": "一键复制下方中文摘要，可贴到笔记或发给自己。",
    }
