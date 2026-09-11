"""Streamlit interactive / ops dashboard for futu-trader."""

from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from core.portfolio import load_portfolio_config
from dashboard.ops import (
    OpsSnapshot,
    build_ops_snapshot,
    expected_dashboard_password,
    format_clock_hhmm_zh,
    format_last_updated_zh,
    human_lane_a,
    human_lane_b,
    page_refresh_seconds,
)
from dashboard.state import (
    LANE_A_STATES,
    LANE_B_STATES,
    DashboardState,
)
from data.futu_feed import QuoteSnapshot, get_qqq_snapshot

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "portfolio.yaml"

SYNC_STATUS_ZH: dict[str, str] = {
    "ok": "正常",
    "stale": "可能过期",
    "failed": "同步失败",
    "empty": "暂无数据",
    "watch": "需留意",
}

ENV_LABELS: dict[str, str] = {
    "staging": "练兵环境（模拟）",
    "paper": "模拟盘",
    "prod": "正式环境",
    "production": "正式环境",
}

PROMOTION_LABELS: dict[str, str] = {
    "PASS": "达标，可讨论下一步",
    "FAIL": "还没达标",
    "PENDING": "统计中",
}

_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.stApp {
  background: linear-gradient(180deg, #edf1f4 0%, #e4eaef 100%);
}
header[data-testid="stHeader"] { background: transparent; }
section.main > div { padding-top: 0.25rem; max-width: 1040px; }
div[data-testid="stToolbar"] { display: none; }

div[data-testid="stVerticalBlockBorderWrapper"] {
  background: #ffffff !important;
  border: 1px solid #cfd8e0 !important;
  border-radius: 6px !important;
  box-shadow: none;
  padding: 0.1rem 0.15rem 0.3rem;
}

.ops-hero {
  border-radius: 6px;
  padding: 1rem 1.1rem 0.95rem;
  margin: 0.2rem 0 0.75rem;
  border: 1px solid #cfd8e0;
  background: #fff;
}
.ops-hero.safe { border-left: 4px solid #1a7f4b; background: #f3faf6; }
.ops-hero.watch { border-left: 4px solid #b7791f; background: #fffaf0; }
.ops-hero.urgent { border-left: 4px solid #c53030; background: #fff5f5; }
.ops-kicker {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #5a6a78;
  margin: 0 0 0.3rem;
  font-weight: 600;
}
.ops-hero h2 {
  margin: 0;
  font-size: 1.4rem;
  line-height: 1.3;
  color: #0f1c24;
  font-weight: 700;
}
.ops-hero p {
  margin: 0.4rem 0 0;
  color: #3a4a56;
  font-size: 0.98rem;
  line-height: 1.45;
}
.ops-pill-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.75rem; }
.ops-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.22rem 0.55rem;
  border-radius: 3px;
  font-size: 0.8rem;
  font-weight: 600;
  background: #f4f7f9;
  border: 1px solid #d5dee6;
  color: #1d2c36;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
}
.kill-banner {
  border-radius: 6px;
  padding: 0.8rem 0.95rem;
  margin: 0 0 0.7rem;
  border: 1px solid #cfd8e0;
  font-weight: 650;
  font-size: 1.02rem;
}
.kill-banner.off {
  background: #eaf7f0;
  border-left: 4px solid #1a7f4b;
  color: #145a38;
}
.kill-banner.on {
  background: #fff5f5;
  border-left: 4px solid #c53030;
  color: #9b2c2c;
}
.fresh-bar {
  border-radius: 6px;
  padding: 0.6rem 0.85rem;
  margin: 0 0 0.7rem;
  font-size: 0.88rem;
  border: 1px solid #cfd8e0;
  background: #fff;
  color: #243743;
}
.fresh-bar.ok { border-left: 4px solid #1a7f4b; }
.fresh-bar.stale, .fresh-bar.watch { border-left: 4px solid #b7791f; background: #fffaf0; }
.fresh-bar.failed, .fresh-bar.urgent { border-left: 4px solid #c53030; background: #fff5f5; }
.fresh-bar.empty { border-left: 4px solid #718096; }
.lane-card {
  background: #fff;
  border-radius: 6px;
  padding: 0.85rem 0.95rem;
  border: 1px solid #cfd8e0;
  border-left: 4px solid #2c7a6b;
  min-height: 104px;
  margin-bottom: 0.3rem;
}
.lane-card.halt { border-left-color: #c53030; }
.lane-card.busy { border-left-color: #2c7a6b; }
.lane-card.idle { border-left-color: #718096; }
.lane-card .label {
  color: #5a6a78; font-size: 0.78rem; margin: 0;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  letter-spacing: 0.02em;
}
.lane-card .value {
  color: #0f1c24; font-size: 1.08rem; font-weight: 700; margin: 0.25rem 0 0.18rem;
}
.lane-card .meta { color: #5a6a78; font-size: 0.82rem; margin: 0; line-height: 1.4; }
.q-block { margin: 0.1rem 0 0.3rem; }
.q-block h3 {
  margin: 0 0 0.12rem;
  font-size: 1.02rem;
  color: #0f1c24;
  font-weight: 700;
}
.q-block .hint {
  color: #5a6a78;
  font-size: 0.84rem;
  margin: 0 0 0.5rem;
}
.pnl-big {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.85rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  margin: 0.12rem 0 0.3rem;
  color: #0f1c24;
}
.pnl-big.up { color: #1a7f4b; }
.pnl-big.down { color: #c53030; }
.hold-line {
  padding: 0.65rem 0.8rem;
  border-radius: 6px;
  margin: 0.3rem 0;
  background: #fff;
  border: 1px solid #cfd8e0;
  border-left: 4px solid #2c7a6b;
  color: #0f1c24;
  font-size: 0.98rem;
  font-weight: 600;
  line-height: 1.4;
}
.hold-line.empty { border-left-color: #718096; color: #3a4a56; font-weight: 500; }
.hold-line.halt { border-left-color: #c53030; }
.hold-table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.3rem 0 0.45rem;
  font-size: 0.9rem;
}
.hold-table th {
  text-align: left;
  color: #5a6a78;
  font-weight: 600;
  font-size: 0.74rem;
  padding: 0.32rem 0.4rem;
  border-bottom: 1px solid #d5dee6;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.hold-table td {
  padding: 0.5rem 0.4rem;
  border-bottom: 1px solid #e8eef2;
  color: #0f1c24;
  vertical-align: top;
}
.hold-table .sym {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-weight: 600;
}
.hold-table .pnl-up { color: #1a7f4b; font-weight: 650; }
.hold-table .pnl-down { color: #c53030; font-weight: 650; }
.hold-table .muted { color: #6a7a86; font-size: 0.8rem; }
.activity-bar {
  border-radius: 6px;
  padding: 0.65rem 0.85rem;
  margin: 0 0 0.75rem;
  border: 1px solid #cfd8e0;
  background: #fff;
}
.activity-bar .title {
  font-size: 0.74rem;
  color: #5a6a78;
  font-weight: 600;
  margin: 0 0 0.3rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.activity-bar ul { list-style: none; margin: 0; padding: 0; }
.activity-bar li {
  font-size: 0.9rem;
  color: #0f1c24;
  padding: 0.2rem 0;
  border-bottom: 1px solid #eef2f5;
  line-height: 1.35;
}
.activity-bar li:last-child { border-bottom: none; }
.activity-bar .when {
  color: #6a7a86;
  font-size: 0.76rem;
  margin-right: 0.35rem;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
}
.activity-bar .last-upd {
  margin-top: 0.35rem;
  font-size: 0.76rem;
  color: #6a7a86;
}
.event-row {
  padding: 0.4rem 0.5rem;
  border-radius: 4px;
  margin: 0.25rem 0;
  background: #f7fafc;
  border-left: 3px solid #718096;
  font-size: 0.88rem;
  color: #243743;
}
.event-row.critical { border-left-color: #c53030; background: #fff5f5; }
.event-row.warning { border-left-color: #b7791f; background: #fffaf0; }
.cal-chip {
  display: inline-block;
  min-width: 3rem;
  text-align: center;
  padding: 0.3rem 0.35rem;
  margin: 0.15rem;
  border-radius: 3px;
  font-size: 0.74rem;
  font-weight: 600;
  border: 1px solid #cfd8e0;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
}
.cal-chip.ok { background: #eaf7f0; color: #1a7f4b; }
.cal-chip.bad { background: #fff0ee; color: #9b2c2c; }
.ops-gate {
  max-width: 420px;
  margin: 12vh auto 0;
  background: #fff;
  border: 1px solid #cfd8e0;
  border-radius: 6px;
  padding: 1.35rem 1.25rem 1.1rem;
  box-shadow: none;
}
.ops-gate h1 { font-size: 1.45rem; margin: 0 0 0.3rem; color: #0f1c24; }
.ops-gate p { color: #5a6a78; margin: 0 0 0.3rem; }

.pm-panel {
  border-radius: 6px;
  border: 1px solid #cfd8e0;
  background: #fff;
  padding: 0.95rem 1rem 0.85rem;
  margin: 0 0 0.75rem;
}
.pm-panel.ok { border-left: 4px solid #1a7f4b; }
.pm-panel.empty, .pm-panel.waiting { border-left: 4px solid #b7791f; background: #fffaf0; }
.pm-panel.bad { border-left: 4px solid #c53030; background: #fff5f5; }
.pm-panel .pm-kicker {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #5a6a78;
  font-weight: 600;
  margin: 0 0 0.25rem;
}
.pm-panel h3 {
  margin: 0;
  font-size: 1.18rem;
  color: #0f1c24;
  font-weight: 700;
  line-height: 1.3;
}
.pm-panel .pm-meta {
  margin: 0.35rem 0 0.55rem;
  color: #3a4a56;
  font-size: 0.88rem;
  line-height: 1.4;
}
.pm-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin: 0 0 0.65rem;
}
.pm-chip {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.74rem;
  padding: 0.18rem 0.45rem;
  border: 1px solid #d5dee6;
  border-radius: 3px;
  background: #f4f7f9;
  color: #1d2c36;
  font-weight: 500;
}
.pm-name {
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  padding: 0.65rem 0.75rem;
  margin: 0.4rem 0;
  background: #fafbfc;
}
.pm-name.veto {
  background: #fff8f6;
  border-color: #f0d0c8;
}
.pm-name .sym {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.02rem;
  font-weight: 650;
  color: #0f1c24;
}
.pm-name .score {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.84rem;
  color: #2c7a6b;
  font-weight: 600;
  margin-left: 0.45rem;
}
.pm-name .metrics {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.78rem;
  color: #4a5a66;
  margin: 0.28rem 0 0.35rem;
}
.pm-name ul {
  margin: 0.15rem 0 0;
  padding-left: 1.1rem;
  color: #2d3c48;
  font-size: 0.86rem;
  line-height: 1.4;
}
.pm-name .src {
  margin-top: 0.3rem;
  font-size: 0.76rem;
  color: #6a7a86;
}
.pm-foot {
  margin-top: 0.65rem;
  padding-top: 0.55rem;
  border-top: 1px solid #e8eef2;
  font-size: 0.78rem;
  color: #5a6a78;
  line-height: 1.4;
}
.pm-section-label {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #5a6a78;
  font-weight: 600;
  margin: 0.55rem 0 0.25rem;
}

div[data-testid="stMetricValue"] {
  font-size: 1.2rem !important;
  font-weight: 650 !important;
  font-family: "IBM Plex Mono", ui-monospace, monospace !important;
}
div[data-testid="stMetricLabel"] { color: #5a6a78 !important; }
div[data-testid="stAlert"] { border-radius: 6px; }

@media (max-width: 768px) {
  .ops-hero h2 { font-size: 1.18rem; }
  .pnl-big { font-size: 1.5rem; }
  .pm-panel h3 { font-size: 1.05rem; }
  section.main > div { padding-left: 0.3rem; padding-right: 0.3rem; }
}
</style>
"""


def _readonly() -> bool:
    return os.environ.get("DASHBOARD_READONLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def format_money(value: float | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+${value:,.2f}"
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def _env_label(env: str) -> str:
    return ENV_LABELS.get(str(env).strip().lower(), str(env))


def _promo_label(verdict: str) -> str:
    return PROMOTION_LABELS.get(str(verdict).upper(), str(verdict))


def _pnl_total(snap: OpsSnapshot) -> float | None:
    if snap.last_pnl_a is None and snap.last_pnl_b is None:
        return None
    return (snap.last_pnl_a or 0.0) + (snap.last_pnl_b or 0.0)


def _worry(snap: OpsSnapshot) -> tuple[str, str]:
    """Return (level, label) for hero tone."""
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


def _tone(snap: OpsSnapshot) -> str:
    level, _ = _worry(snap)
    if snap.trading_enabled or level == "urgent":
        return "urgent"
    if level == "watch":
        return "watch"
    return "safe"


def _lane_tone(state: str) -> str:
    if state == "HALT":
        return "halt"
    if state in {"LOCKED", "DEPLOY", "SCOUT"}:
        return "busy"
    return "idle"


def _holding_rows(snap: OpsSnapshot) -> list[tuple[str, str]]:
    """Plain-Chinese holdings lines: (css_class, text). Prefer structured positions."""
    rows: list[tuple[str, str]] = []
    positions = list(snap.positions or [])
    if positions:
        for pos in positions:
            lane = pos.get("lane") or "?"
            lane_name = "路线 A" if lane == "A" else "路线 B" if lane == "B" else f"路线 {lane}"
            sym = pos.get("symbol") or "未标明标的"
            side = pos.get("side_zh") or pos.get("side")
            qty = pos.get("qty")
            bits = [f"{lane_name}：{sym}"]
            if side:
                bits.append(str(side))
            if qty is not None:
                bits.append(f"数量 {qty:g}" if isinstance(qty, float) else f"数量 {qty}")
            upnl = pos.get("unrealized_pnl")
            if upnl is not None:
                bits.append(f"浮动盈亏 {format_money(upnl)}")
            elif pos.get("note"):
                bits.append(str(pos["note"]))
            tone = "busy"
            rows.append((tone, " · ".join(bits)))
        return rows

    # Friendly empty / no-open-position copy by lane status
    a_sum: dict[str, Any] = snap.lane_a_summary or {}
    b_sum: dict[str, Any] = snap.lane_b_summary or {}
    if snap.lane_a_state == "HALT":
        reason = a_sum.get("halt_reason_zh")
        extra = f"（{reason}）" if reason else ""
        rows.append(("halt", f"路线 A：已停手，没有新仓{extra}"))
    else:
        rows.append(("empty", f"路线 A：暂无持仓 · {snap.lane_a_status_zh or snap.lane_a_label}"))

    if snap.lane_b_state == "HALT":
        reason = b_sum.get("halt_reason_zh")
        extra = f"（{reason}）" if reason else ""
        rows.append(("halt", f"路线 B：已停手，没有新仓{extra}"))
    else:
        rows.append(("empty", f"路线 B：暂无持仓 · {snap.lane_b_status_zh or snap.lane_b_label}"))
    return rows


def _position_table_rows(snap: OpsSnapshot) -> list[dict[str, str]]:
    """Rows for the holdings table (qty / avg / notional / floating PnL)."""
    table: list[dict[str, str]] = []
    for pos in snap.positions or []:
        lane = pos.get("lane") or "?"
        side = pos.get("side_zh") or pos.get("side") or "—"
        qty = pos.get("qty")
        if qty is None:
            direction = str(side)
        elif isinstance(qty, float):
            direction = f"{side} × {qty:g}"
        else:
            direction = f"{side} × {qty}"
        avg = pos.get("avg_price")
        avg_s = f"${avg:,.2f}" if avg is not None else "—"
        notion = pos.get("notional")
        notion_s = format_money(notion) if notion is not None else "—"
        note = pos.get("note")
        if note and ("估算" in str(note) or "估计" in str(note)):
            if avg_s != "—":
                avg_s = f"{avg_s}（估算）"
            if notion_s != "—" and "估算" not in notion_s:
                pass
        upnl = pos.get("unrealized_pnl")
        upnl_s = format_money(upnl) if upnl is not None else "—"
        if upnl is None and note:
            upnl_s = str(note)
        table.append(
            {
                "路线": "A · 趋势" if lane == "A" else "B · 财报" if lane == "B" else str(lane),
                "标的": str(pos.get("symbol") or "—"),
                "方向 / 数量": direction,
                "均价": avg_s,
                "名义金额": notion_s,
                "浮动盈亏": upnl_s,
            }
        )
    return table


def _fill_table_rows(snap: OpsSnapshot) -> list[dict[str, str]]:
    """Trade detail rows for the fills table."""
    table: list[dict[str, str]] = []
    for fill in snap.fills or []:
        lane = fill.get("lane") or "?"
        qty = fill.get("qty")
        qty_s = f"{qty:g}" if isinstance(qty, float) else (str(qty) if qty is not None else "—")
        price = fill.get("price")
        price_s = f"${price:,.2f}" if price is not None else "—"
        notion = fill.get("notional")
        notion_s = format_money(notion) if notion is not None else "—"
        ts = str(fill.get("ts") or "")
        # Prefer local-ish short time for mobile readability
        when = ts[11:16] if len(ts) >= 16 else (ts[:16] if ts else "—")
        table.append(
            {
                "路线": "A" if lane == "A" else "B" if lane == "B" else str(lane),
                "时间": when,
                "标的": str(fill.get("symbol") or "—"),
                "买/卖": str(fill.get("side_zh") or fill.get("side") or "—"),
                "数量": qty_s,
                "价格": price_s,
                "名义": notion_s,
                "模式": str(fill.get("mode_zh") or fill.get("mode") or "—"),
            }
        )
    return table


def _fills_summary(snap: OpsSnapshot) -> str:
    a = int((snap.lane_a_summary or {}).get("fills_count") or 0)
    b = int((snap.lane_b_summary or {}).get("fills_count") or 0)
    total = a + b
    if not snap.last_session_date:
        return "还没有成交摘要。"
    detail_n = len(snap.fills or [])
    base = (
        f"{snap.last_session_date}：一共成交 {total} 笔"
        f"（路线 A {a} 笔 · 路线 B {b} 笔）"
    )
    if detail_n:
        return f"{base}；下方明细 {detail_n} 笔。"
    if total > 0:
        return f"{base}。日报尚无逐笔明细（需新日跑导出 fills）。"
    return f"{base}。"


def _require_password() -> bool:
    expected = expected_dashboard_password()
    if expected is None:
        return True
    if st.session_state.get("dashboard_authed"):
        return True

    st.markdown(_THEME_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="ops-gate"><h1>交易指挥室</h1>'
        "<p>输入密码后查看运营概况。</p>"
        "<p>这个页面不会改真下单开关。</p></div>",
        unsafe_allow_html=True,
    )
    with st.form("gate", clear_on_submit=False):
        password = st.text_input("访问密码", type="password", placeholder="请输入密码")
        submitted = st.form_submit_button("进入指挥室", use_container_width=True, type="primary")
        if submitted:
            if password == expected:
                st.session_state.dashboard_authed = True
                st.rerun()
            st.error("密码不对。再试一次，或问发密码给你的人。")
    return False


def _init_session() -> DashboardState:
    if "dashboard" not in st.session_state:
        st.session_state.dashboard = DashboardState.bootstrap(str(CONFIG_PATH))
    return st.session_state.dashboard


def _render_kill_switch(snap: OpsSnapshot) -> None:
    if snap.trading_enabled:
        st.markdown(
            '<div class="kill-banner on">'
            "真下单总开关：开着 — 真钱通道已打开（此页只能看，不能改）"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="kill-banner off">'
            "真下单总开关：关闭 — 不会真钱下单（此页只能看，不能打开）"
            "</div>",
            unsafe_allow_html=True,
        )


def _mark_ops_page_refreshed() -> None:
    st.session_state["ops_page_refreshed_at"] = time.time()


def _render_ops_refresh_controls() -> bool:
    """Manual refresh + auto-refresh toggle. Returns whether auto mode is on."""
    interval = page_refresh_seconds()
    interval_min = max(1, interval // 60)
    if "ops_page_refreshed_at" not in st.session_state:
        _mark_ops_page_refreshed()

    c1, c2, c3 = st.columns([1, 2, 2])
    with c1:
        if st.button("手动刷新", use_container_width=True, help="立刻重新读取最新快照"):
            _mark_ops_page_refreshed()
            st.session_state["ops_manual_refresh_flash"] = True
            st.rerun()
    with c2:
        auto = st.toggle(
            f"自动刷新（约 {interval_min} 分钟）",
            value=True,
            help=(
                "到点后由浏览器重新加载页面（不阻塞服务器）。间隔可用环境变量 "
                "DASHBOARD_REFRESH_SECONDS 配置（默认 3600）。"
            ),
            key="ops_auto_refresh",
        )
    with c3:
        page_at = format_last_updated_zh(st.session_state.get("ops_page_refreshed_at"))
        st.caption(f"页面最后更新于 **{page_at}**")

    if st.session_state.pop("ops_manual_refresh_flash", False):
        st.success("已手动刷新：正在显示当前最新快照。")
    return bool(auto)


def _schedule_browser_auto_refresh(auto: bool) -> None:
    """Non-blocking auto-refresh: browser reloads after the interval.

    Avoids ``time.sleep`` + ``st.rerun`` which holds the Streamlit worker.
    """
    if not auto:
        return
    interval = page_refresh_seconds()
    elapsed = time.time() - float(st.session_state.get("ops_page_refreshed_at") or 0)
    remaining_ms = max(1_000, int((interval - elapsed) * 1000))
    # Reload parent (Streamlit iframe) so the whole app re-reads snapshots.
    components.html(
        f"""
<script>
setTimeout(function () {{
  try {{ window.parent.location.reload(); }}
  catch (e) {{ window.location.reload(); }}
}}, {remaining_ms});
</script>
""",
        height=0,
        width=0,
    )


def _render_activity(snap: OpsSnapshot) -> None:
    """Show that the system moved today — even on zero-fill scout days."""
    items = snap.activity or []
    raw_upd = snap.data_as_of or snap.hourly_as_of
    last_upd = format_last_updated_zh(raw_upd) if raw_upd else "—"
    if not items:
        st.markdown(
            f'<div class="activity-bar"><div class="title">今日活动</div>'
            f'<p style="margin:0;color:#5b6b76;font-size:0.9rem">'
            f"暂无活动记录。最后更新于 {html.escape(str(last_upd))}</p></div>",
            unsafe_allow_html=True,
        )
        return
    lis = []
    for it in items[:8]:
        when = str(it.get("ts") or "")
        short = format_clock_hhmm_zh(when) if when else ""
        text = html.escape(str(it.get("text") or ""))
        when_html = (
            f'<span class="when">{html.escape(short)}</span>' if short else ""
        )
        lis.append(f"<li>{when_html}{text}</li>")
    st.markdown(
        f'<div class="activity-bar"><div class="title">今日活动 · 系统在干活</div>'
        f"<ul>{''.join(lis)}</ul>"
        f'<div class="last-upd">最后更新于 {html.escape(str(last_upd))}</div></div>',
        unsafe_allow_html=True,
    )


def _render_freshness(snap: OpsSnapshot) -> None:
    status = snap.sync_status or "empty"
    cls = status if status in {"ok", "stale", "failed", "empty"} else "watch"
    status_zh = SYNC_STATUS_ZH.get(status, status)
    as_of = format_last_updated_zh(snap.data_as_of) if snap.data_as_of else "未知"
    synced = format_last_updated_zh(snap.synced_at) if snap.synced_at else "—"
    bits = [
        "<strong>数据新鲜度</strong>",
        f"状态 {html.escape(status_zh)}",
        f"截至 {html.escape(str(as_of))}",
        f"同步 {html.escape(str(synced))}",
    ]
    if snap.hosting_mode == "cloud":
        bits.append("托管 云端（不直连本机 OpenD）")
    else:
        bits.append(f"本机 OpenD {'可达' if snap.opend_reachable else '未连上'}")
    if snap.hourly_as_of:
        hb_reach = snap.hourly_opend_reachable
        hb_reach_zh = (
            "可达" if hb_reach is True else "不可达" if hb_reach is False else "—"
        )
        hb_bits = (
            f"小时心跳 {html.escape(format_last_updated_zh(snap.hourly_as_of))}"
            f"（本机 OpenD {hb_reach_zh}"
        )
        if snap.hourly_quote_source:
            hb_bits += f" · 报价 {html.escape(str(snap.hourly_quote_source))}"
        hb_bits += "）"
        bits.append(hb_bits)
    if snap.sync_error:
        bits.append(html.escape(str(snap.sync_error)))
    elif snap.health_hint_zh:
        bits.append(html.escape(str(snap.health_hint_zh)))
    st.markdown(
        f'<div class="fresh-bar {cls}">{" · ".join(bits)}</div>',
        unsafe_allow_html=True,
    )
    if status == "empty":
        st.info(
            "下一步：本机跑无人值守日跑（`futu-unattended-day` 或 "
            "`scripts/run_unattended_day.ps1`），再按需同步到云端。"
        )
    elif status == "failed":
        st.warning(
            "同步失败时云端会一直显示旧快照或演示数据。"
            "请检查 `REPORTS_REMOTE_BASE` / 本机 `sync_reports_to_cloud.py --push`。"
        )
    elif status == "stale" and snap.hosting_mode == "cloud":
        st.caption(
            "「截至时间旧」表示快照过期，不是「云端连不上你电脑上的 OpenD」。"
            "请在本机跑小时心跳或日跑后同步。"
        )


def _render_q_safe(snap: OpsSnapshot) -> None:
    tone = _tone(snap)
    _, worry_label = _worry(snap)
    with st.container(border=True):
        st.markdown(
            '<div class="q-block"><h3>① 今天安全吗</h3>'
            '<p class="hint">先看真下单开关，再看有没有要留意的事。</p></div>',
            unsafe_allow_html=True,
        )
        _render_kill_switch(snap)
        st.markdown(
            f"""
<div class="ops-hero {tone}">
  <div class="ops-kicker">一句话结论</div>
  <h2>{worry_label}</h2>
  <p>{snap.alert}</p>
  <div class="ops-pill-row">
    <span class="ops-pill">{_env_label(snap.futu_env)}</span>
    <span class="ops-pill">配置资金 ${snap.total_capital:,.0f}</span>
    <span class="ops-pill">练兵 {snap.counting_streak}/{snap.required_n} 天</span>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )


def _pm_name_card(row: dict[str, Any], *, veto: bool = False) -> str:
    sym = html.escape(str(row.get("symbol") or "—"))
    score = row.get("score")
    score_s = f"{float(score):.2f}" if score is not None else "—"
    metrics = (
        f"跳空 {html.escape(str(row.get('gap_pct_zh') or '—'))}"
        f" · ATR {html.escape(str(row.get('atr_pct_zh') or '—'))}"
        f" · 相对量 {html.escape(str(row.get('rvol_zh') or '—'))}"
    )
    if row.get("last_price") is not None:
        metrics += f" · 价 ${float(row['last_price']):,.2f}"
    if row.get("regime_tag"):
        metrics += f" · {html.escape(str(row['regime_tag']))}"

    reasons = list(row.get("reasons") or [])
    if not reasons and row.get("reason_text"):
        reasons = [str(row["reason_text"])]
    lis = "".join(f"<li>{html.escape(str(r))}</li>" for r in reasons[:8]) or "<li>—</li>"

    sources = list(row.get("sources") or [])
    src_html = ""
    if sources:
        src_html = (
            '<div class="src">数据来源：'
            + " · ".join(html.escape(str(s)) for s in sources[:4])
            + "</div>"
        )

    cls = "pm-name veto" if veto else "pm-name"
    score_label = "否决分" if veto else "得分"
    return (
        f'<div class="{cls}">'
        f'<span class="sym">{sym}</span>'
        f'<span class="score">{score_label} {html.escape(score_s)}</span>'
        f'<div class="metrics">{metrics}</div>'
        f"<ul>{lis}</ul>"
        f"{src_html}"
        "</div>"
    )


def _render_premarket(snap: OpsSnapshot) -> None:
    """今日盘前决策透明度 — PROH-107."""
    pm: dict[str, Any] = snap.premarket or {}
    status = str(pm.get("status") or "waiting")
    panel_cls = {
        "ok": "ok",
        "empty_universe": "empty",
        "waiting": "waiting",
        "missing": "waiting",
        "bad_schema": "bad",
    }.get(status, "waiting")

    chips: list[str] = []
    if pm.get("as_of"):
        chips.append(f"美东交易日 {html.escape(str(pm['as_of']))}")
    if pm.get("generated_at_zh"):
        chips.append(f"生成 {html.escape(str(pm['generated_at_zh']))}")
    if pm.get("schema_version"):
        chips.append(f"规则 {html.escape(str(pm['schema_version']))}")
    if pm.get("experiment_id"):
        chips.append(f"实验 {html.escape(str(pm['experiment_id']))}")
    if pm.get("generated_by"):
        chips.append(f"来源 {html.escape(str(pm['generated_by']))}")
    if pm.get("data_label") == "demo_fixtures":
        chips.append("演示数据")
    chip_html = "".join(f'<span class="pm-chip">{c}</span>' for c in chips)

    body_parts: list[str] = [
        f'<div class="pm-panel {panel_cls}">',
        '<div class="pm-kicker">今日盘前决策 · Lane A 技术面</div>',
        f"<h3>{html.escape(str(pm.get('headline_zh') or '今日盘前决策'))}</h3>",
        f'<p class="pm-meta">{html.escape(str(pm.get("detail_zh") or ""))}</p>',
    ]
    if chip_html:
        body_parts.append(f'<div class="pm-meta-row">{chip_html}</div>')

    deployable = list(pm.get("deployable") or [])
    vetoed = list(pm.get("vetoed") or [])

    if status == "ok" and deployable:
        body_parts.append('<div class="pm-section-label">可交易名单</div>')
        for row in deployable:
            body_parts.append(_pm_name_card(row, veto=False))
        if vetoed:
            body_parts.append('<div class="pm-section-label">被否决（审计透明）</div>')
            for row in vetoed:
                body_parts.append(_pm_name_card(row, veto=True))
    elif status == "empty_universe":
        reason = pm.get("no_trade_reason_zh") or "今日 Lane A 不交易（no_trade_day）"
        body_parts.append(
            f'<div class="hold-line empty">{html.escape(str(reason))}</div>'
        )
        if vetoed:
            body_parts.append('<div class="pm-section-label">被否决</div>')
            for row in vetoed:
                body_parts.append(_pm_name_card(row, veto=True))
    else:
        reason = pm.get("no_trade_reason_zh") or pm.get("detail_zh") or "等待同步"
        body_parts.append(
            f'<div class="hold-line empty">{html.escape(str(reason))}</div>'
        )

    if pm.get("universe_note") and status in {"ok", "empty_universe"}:
        with_note = html.escape(str(pm["universe_note"]))
        body_parts.append(
            f'<p class="pm-meta" style="margin-top:0.45rem">宇宙说明：{with_note}</p>'
        )

    foot = pm.get("footnote_zh") or (
        "名单 ≠ 下单指令。入场仍由 ORB+VWAP；真下单默认关闭；NFA。"
    )
    body_parts.append(f'<div class="pm-foot">{html.escape(str(foot))}</div>')
    body_parts.append("</div>")
    st.markdown("".join(body_parts), unsafe_allow_html=True)


def _render_q_pnl(snap: OpsSnapshot) -> None:
    with st.container(border=True):
        st.markdown(
            '<div class="q-block"><h3>② 赚亏多少</h3>'
            '<p class="hint">最近一个交易日的合计；正数是赚，负数是亏。</p></div>',
            unsafe_allow_html=True,
        )
        if not snap.last_session_date:
            st.info("暂无日报。跑完模拟日后会自动出现。")
            return

        total = _pnl_total(snap)
        cls = "up" if (total or 0) > 0 else "down" if (total or 0) < 0 else ""
        st.markdown(
            f'<div class="pnl-big {cls}">{format_money(total)}</div>',
            unsafe_allow_html=True,
        )
        st.caption(f"日期 {snap.last_session_date}")
        p1, p2, p3 = st.columns(3)
        p1.metric("路线 A", format_money(snap.last_pnl_a))
        p2.metric("路线 B", format_money(snap.last_pnl_b))
        p3.metric("停手次数", str(snap.last_halts))


def _render_q_holdings(snap: OpsSnapshot) -> None:
    with st.container(border=True):
        st.markdown(
            '<div class="q-block"><h3>③ 持仓是什么</h3>'
            '<p class="hint">标的、数量、均价、名义金额、浮动盈亏（有则显示）。</p></div>',
            unsafe_allow_html=True,
        )
        if not snap.last_session_date and snap.data_mode == "empty":
            st.info("还没有持仓可读。等日报出来后再看这里。")
            return

        table = _position_table_rows(snap)
        if table:
            cells: list[str] = []
            for row in table:
                pnl = row["浮动盈亏"]
                if pnl.startswith("+"):
                    pnl_html = f'<span class="pnl-up">{pnl}</span>'
                elif pnl.startswith("-") and pnl != "—":
                    pnl_html = f'<span class="pnl-down">{pnl}</span>'
                elif pnl == "—":
                    pnl_html = '<span class="muted">浮动盈亏未提供</span>'
                else:
                    pnl_html = f'<span class="muted">{html.escape(pnl)}</span>'
                cells.append(
                    "<tr>"
                    f"<td>{html.escape(row['路线'])}</td>"
                    f"<td><strong>{html.escape(row['标的'])}</strong></td>"
                    f"<td>{html.escape(row['方向 / 数量'])}</td>"
                    f"<td>{html.escape(row['均价'])}</td>"
                    f"<td>{html.escape(row['名义金额'])}</td>"
                    f"<td>{pnl_html}</td>"
                    "</tr>"
                )
            st.markdown(
                "<table class='hold-table'>"
                "<thead><tr>"
                "<th>路线</th><th>标的</th><th>方向 / 数量</th>"
                "<th>均价</th><th>名义金额</th><th>浮动盈亏</th>"
                "</tr></thead>"
                f"<tbody>{''.join(cells)}</tbody></table>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="hold-line empty">现在手里没有开着的仓'
                "（或日报里还没写持仓明细）。</div>",
                unsafe_allow_html=True,
            )
            if snap.last_session_date:
                st.caption(
                    f"最新日报日：{snap.last_session_date} · "
                    f"A：{snap.lane_a_status_zh or snap.lane_a_label} · "
                    f"B：{snap.lane_b_status_zh or snap.lane_b_label}"
                )
        st.caption(_fills_summary(snap))


def _render_fills_detail(snap: OpsSnapshot) -> None:
    """Primary trade detail — survives flat positions (closed fills still listed)."""
    with st.container(border=True):
        st.markdown(
            '<div class="q-block"><h3>④ 成交明细</h3>'
            '<p class="hint">最近一日逐笔：买/卖、数量、价格或名义、真模拟/假成交。'
            "已平仓的成交也会留在这里。</p></div>",
            unsafe_allow_html=True,
        )
        rows = _fill_table_rows(snap)
        if not rows:
            a = int((snap.lane_a_summary or {}).get("fills_count") or 0)
            b = int((snap.lane_b_summary or {}).get("fills_count") or 0)
            if a + b == 0:
                st.markdown(
                    '<div class="hold-line empty">今日尚无成交'
                    "（B 无事件日 0 笔是正常的；看上方活动条确认系统跑过）。</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.info(
                    f"日报写了成交 {a + b} 笔，但还没有逐笔明细。"
                    "新日跑会把 fills 写进 daily_report；本机也可读 lane_*.jsonl。"
                )
            return
        cells: list[str] = []
        for row in rows:
            cells.append(
                "<tr>"
                f"<td>{html.escape(row['路线'])}</td>"
                f"<td>{html.escape(row['时间'])}</td>"
                f"<td><strong>{html.escape(row['标的'])}</strong></td>"
                f"<td>{html.escape(row['买/卖'])}</td>"
                f"<td>{html.escape(row['数量'])}</td>"
                f"<td>{html.escape(row['价格'])}</td>"
                f"<td>{html.escape(row['名义'])}</td>"
                f"<td>{html.escape(row['模式'])}</td>"
                "</tr>"
            )
        st.markdown(
            "<table class='hold-table'>"
            "<thead><tr>"
            "<th>路线</th><th>时间</th><th>标的</th><th>买/卖</th>"
            "<th>数量</th><th>价格</th><th>名义</th><th>模式</th>"
            "</tr></thead>"
            f"<tbody>{''.join(cells)}</tbody></table>",
            unsafe_allow_html=True,
        )
        st.caption(_fills_summary(snap))


def _render_lanes(snap: OpsSnapshot) -> None:
    a_sum: dict[str, Any] = snap.lane_a_summary or {}
    b_sum: dict[str, Any] = snap.lane_b_summary or {}
    st.markdown("**双路人话状态**")
    st.caption("一眼分清 A / B 今天各自怎样；标的与成交次数来自最新日报。")
    a, b = st.columns(2)
    with a:
        a_meta = []
        if a_sum.get("symbol"):
            a_meta.append(f"标的 {a_sum['symbol']}")
        a_meta.append(f"成交 {int(a_sum.get('fills_count') or 0)} 笔")
        if a_sum.get("halt_reason_zh"):
            a_meta.append(f"停手：{a_sum['halt_reason_zh']}")
        st.markdown(
            f"""
<div class="lane-card {_lane_tone(snap.lane_a_state)}">
  <p class="label">路线 A · 趋势（ORB+VWAP）</p>
  <p class="value">{snap.lane_a_status_zh or snap.lane_a_label}</p>
  <p class="meta">{" · ".join(a_meta)}</p>
</div>
""",
            unsafe_allow_html=True,
        )
    with b:
        b_meta = []
        if b_sum.get("prefer_symbol"):
            b_meta.append(f"关注 {b_sum['prefer_symbol']}")
        if b_sum.get("day_mode_zh"):
            b_meta.append(str(b_sum["day_mode_zh"]))
        b_meta.append(f"成交 {int(b_sum.get('fills_count') or 0)} 笔")
        if b_sum.get("halt_reason_zh"):
            b_meta.append(f"停手：{b_sum['halt_reason_zh']}")
        st.markdown(
            f"""
<div class="lane-card {_lane_tone(snap.lane_b_state)}">
  <p class="label">路线 B · 财报期权</p>
  <p class="value">{snap.lane_b_status_zh or snap.lane_b_label}</p>
  <p class="meta">{" · ".join(b_meta)}</p>
</div>
""",
            unsafe_allow_html=True,
        )


def _render_pnl_curve(snap: OpsSnapshot) -> None:
    st.markdown("**盈亏曲线**")
    points = snap.pnl_points or []
    if len(points) < 2:
        st.caption("天数还不够画趋势线。至少两天日报后会出现。")
        return
    st.caption("最近赚亏趋势（合计，按交易日）")
    chart_data = {
        str(p.get("date") or f"#{i}"): float(p.get("pnl_total") or 0.0)
        for i, p in enumerate(points)
    }
    st.line_chart(chart_data, height=200)


def _render_fills_block(snap: OpsSnapshot) -> None:
    st.markdown("**成交摘要**")
    st.write(_fills_summary(snap))
    a = int((snap.lane_a_summary or {}).get("fills_count") or 0)
    b = int((snap.lane_b_summary or {}).get("fills_count") or 0)
    c1, c2, c3 = st.columns(3)
    c1.metric("合计成交", f"{a + b} 笔")
    c2.metric("路线 A", f"{a} 笔")
    c3.metric("路线 B", f"{b} 笔")
    if snap.fills:
        st.caption(f"首页「成交明细」已展开最近 {len(snap.fills)} 笔。")


def _render_drill(snap: OpsSnapshot) -> None:
    ratio = 0.0
    if snap.required_n > 0:
        ratio = min(1.0, max(0.0, snap.counting_streak / snap.required_n))
    st.markdown("**练兵进度**")
    st.caption("连续合格模拟日越多，越接近可以讨论真钱。网页不会自动开真下单。")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        st.metric("已完成", f"{snap.counting_streak} / {snap.required_n} 天")
        st.progress(ratio, text=f"完成度 {ratio:.0%}")
    with c2:
        st.metric("结果", _promo_label(snap.promotion_verdict))
        if snap.promotion_verdict == "PASS":
            st.caption("门槛已够。下一步仍要人工决定。")
        else:
            remain = max(0, snap.required_n - snap.counting_streak)
            st.caption(f"大约还差 {remain} 个合格日。")

    cal = snap.promotion_calendar or []
    if cal:
        st.caption("最近练兵日历（绿=计入 · 红=不计入）")
        chips = []
        for row in cal[-14:]:
            ok = bool(row.get("counts_for_promotion"))
            day = str(row.get("date") or "")[-5:] or "?"
            title = row.get("excluded_reason_zh") or "计入练兵"
            cls = "ok" if ok else "bad"
            chips.append(f'<span class="cal-chip {cls}" title="{title}">{day}</span>')
        st.markdown("".join(chips), unsafe_allow_html=True)


def _render_events(snap: OpsSnapshot) -> None:
    events = snap.events or []
    st.markdown("**最近异常 / 停手**")
    if not events:
        st.caption("最近一日没有停手或断连记录。")
        return
    st.caption("用人话说明「为什么停」，不用翻日志。")
    for ev in events[:12]:
        sev = str(ev.get("severity") or "info")
        cls = sev if sev in {"critical", "warning"} else ""
        lane = ev.get("lane") or "?"
        reason = ev.get("reason_zh") or "—"
        when = ev.get("time") or ""
        st.markdown(
            f'<div class="event-row {cls}"><strong>{lane}</strong> · {reason}'
            f'<br/><span style="color:#6a7a86;font-size:0.8rem">{when}</span></div>',
            unsafe_allow_html=True,
        )


def _render_secondary(snap: OpsSnapshot) -> None:
    if snap.last_halts:
        st.warning(
            f"最近一日有 **{snap.last_halts}** 次停手记录——"
            "可在下方「二级详情」展开查看原因。"
        )
    with st.expander("二级详情：盈亏曲线 · 成交摘要 · 练兵", expanded=False):
        _render_pnl_curve(snap)
        st.divider()
        _render_fills_block(snap)
        st.divider()
        _render_drill(snap)
        if snap.events:
            st.divider()
            _render_events(snap)


def _render_ops_home() -> bool:
    """Render ops home. Returns whether browser auto-refresh is enabled."""
    snap = build_ops_snapshot(CONFIG_PATH, probe_opend=True)
    st.markdown(_THEME_CSS, unsafe_allow_html=True)

    head_l, head_r = st.columns([3.2, 1])
    with head_l:
        st.markdown("### 交易指挥室")
        st.caption(
            "首页：今天安全吗 · 盘前选了谁 · 赚亏多少 · 双路怎样 · 持仓 · 成交明细。"
        )
    with head_r:
        st.caption("只读 · 不能真下单")

    auto = _render_ops_refresh_controls()

    st.caption(snap.data_path_zh)
    st.caption(
        "数据链：本机 OpenD → 小时心跳/日报 →（本机直读或同步到云端）→ 本页展示。"
        "云端不能直连你电脑上的 OpenD。"
    )

    if snap.data_mode == "demo_fixtures":
        st.warning(
            "当前是**演示数据**（示例练兵），不是你的真日报。"
            "接上真实日报目录或远程同步后会自动换成实况。"
        )
    elif snap.data_mode == "live_virtual":
        mode = snap.opend_mode or "未知"
        if snap.hosting_mode == "cloud":
            st.success(
                f"当前为**云端只读快照**（日报里的 OpenD 模式标记：`{mode}`）。"
                "云端不探测、也连不上你电脑上的 OpenD。"
            )
        else:
            reachable = "可达" if snap.opend_reachable else "不可达"
            st.success(
                f"当前为**本机预览 · 虚拟盘实况**（OpenD 模式：`{mode}`，探测：{reachable}）。"
            )
        st.caption(snap.health_hint_zh)
    elif snap.data_mode == "empty":
        st.info(
            "还没有可读的日报。下一步：本机跑 "
            "`futu-unattended-day` / `scripts/run_unattended_day.ps1`。"
        )

    _render_q_safe(snap)
    _render_premarket(snap)
    _render_freshness(snap)
    _render_activity(snap)
    _render_q_pnl(snap)
    with st.container(border=True):
        st.markdown(
            '<div class="q-block"><h3>双路今天怎样</h3>'
            '<p class="hint">A 趋势 / B 财报，分列人话状态。</p></div>',
            unsafe_allow_html=True,
        )
        _render_lanes(snap)
    _render_q_holdings(snap)
    _render_fills_detail(snap)
    _render_secondary(snap)

    with st.expander("这是什么意思？常见问题"):
        st.markdown(
            """
- **真下单总开关**：关着就不会真钱下单。这个网页**不能**把它打开。
- **今天安全吗**：一眼结论 + 有没有要留意的事。
- **今日盘前决策**：今天技术面选了谁、得分与理由、谁被否决；空日/缺文件会写明「不交易」。
- **赚亏多少**：最近一个交易日两条路线合计。
- **双路怎样**：A 是否在找机会/持仓/停手；B 是否有财报事件或已部署。
- **持仓是什么**：标的、数量、均价、名义金额、浮动盈亏（日报有则显示）。
- **成交明细**：最近一日逐笔买/卖与金额；已平仓也会留在表里。
- **今日活动**：日跑/侦察/成交/心跳，避免整页静默。
- **演示数据**：样例；接上真实日报后会换成实况。
- **云端看板**：只读已同步快照，**不会**连你电脑上的 OpenD。
- **黄条 / 红条**：黄=留意（过期/演示），红=要处理（同步失败或真下单开着）。
  详见 `docs/dashboard_for_noah.md`。
- **手动 / 自动刷新**：点「手动刷新」立刻重读；
  自动刷新约每小时由浏览器重载（不阻塞服务器）。
"""
        )

    with st.expander("更多技术细节（一般不用看）"):
        st.write(f"实验编号：`{snap.experiment_id}`")
        st.write(f"晋级原文：`{snap.promotion_verdict}`")
        st.write(f"环境原文：`{snap.futu_env}`")
        st.write(f"托管：`{snap.hosting_mode}`")
        st.write(f"连接标记：`{snap.opend_mode or '—'}`")
        st.write(f"同步状态：`{snap.sync_status}`")
        st.write(f"健康提示：{snap.health_hint_zh}")
        st.write(f"持仓条数：{len(snap.positions or [])}")
        pm = snap.premarket or {}
        st.write(
            f"盘前决策：`{pm.get('status')}` · "
            f"as_of=`{pm.get('as_of') or '—'}` · "
            f"可交易 {len(pm.get('deployable') or [])} · "
            f"否决 {len(pm.get('vetoed') or [])}"
        )
        if pm.get("source_path"):
            st.write(f"名单路径：`{pm.get('source_path')}`")
        hourly_zh = (
            format_last_updated_zh(snap.hourly_as_of) if snap.hourly_as_of else "—"
        )
        st.write(f"小时心跳：`{hourly_zh}`")
        st.code(snap.reports_dir)
        st.caption("稳定 JSON 出口：`python -m dashboard.ops_export`")
        st.caption(
            f"自动刷新间隔：{page_refresh_seconds()}s · "
            "小时采集：`futu-hourly-ops` / `scripts/run_hourly_ops_refresh.ps1`"
        )
    return auto


def _render_quote(config_host: str, config_port: int) -> QuoteSnapshot:
    st.subheader("QQQ 参考报价")
    col_refresh, col_auto = st.columns([1, 2])
    with col_refresh:
        refresh = st.button("刷新报价", use_container_width=True, key="lab_quote_refresh")
    with col_auto:
        auto = st.toggle("自动刷新（约 10 秒）", value=False)

    if "last_quote" not in st.session_state or refresh:
        st.session_state.last_quote = get_qqq_snapshot(host=config_host, port=config_port)
        st.session_state.quote_fetched_at = time.time()

    if auto:
        elapsed = time.time() - st.session_state.get("quote_fetched_at", 0)
        if elapsed >= 10:
            st.session_state.last_quote = get_qqq_snapshot(host=config_host, port=config_port)
            st.session_state.quote_fetched_at = time.time()
        # Browser-side timer — do not sleep the Streamlit worker.
        remaining_ms = max(500, int((10 - elapsed) * 1000))
        components.html(
            f"""
<script>
setTimeout(function () {{
  try {{ window.parent.location.reload(); }}
  catch (e) {{ window.location.reload(); }}
}}, {remaining_ms});
</script>
""",
            height=0,
            width=0,
        )

    quote: QuoteSnapshot = st.session_state.last_quote
    source_label = "富途实盘源" if quote.source == "futu" else "离线示例价"
    st.metric(label=quote.code, value=f"${quote.last_price:,.2f}", delta=source_label)
    return quote


def _render_ledger(state: DashboardState) -> None:
    st.subheader("资金分账（A / B）")
    a_pct, b_pct = state.lane_allocation_pct()

    m1, m2, m3 = st.columns(3)
    m1.metric("路线 A", f"${state.ledger.lane_a_balance:,.2f}", f"{a_pct}%")
    m2.metric("路线 B", f"${state.ledger.lane_b_balance:,.2f}", f"{b_pct}%")
    m3.metric("合计", f"${state.ledger.total:,.2f}")

    st.progress(a_pct / 100, text=f"A {a_pct}% · B {b_pct}%")

    if _readonly():
        st.caption("只读模式：网页上不能改余额。")
        return

    st.caption("调整余额（模拟成交 / 资金划转）")
    c1, c2, c3 = st.columns(3)
    with c1:
        delta_a = st.number_input("路线 A 变动", value=0.0, step=50.0, format="%.2f")
    with c2:
        delta_b = st.number_input("路线 B 变动", value=0.0, step=50.0, format="%.2f")
    with c3:
        st.write("")
        st.write("")
        if st.button("应用调整", use_container_width=True):
            state.ledger.update_lane_a(delta_a)
            state.ledger.update_lane_b(delta_b)
            st.rerun()

    if st.button("重置为默认分配（70/30）"):
        state.reset_ledger()
        st.rerun()


def _render_lane_states(state: DashboardState) -> None:
    st.subheader("路线状态（实验室）")
    if _readonly():
        st.info(f"**路线 A**：{human_lane_a(state.lane_a_state)}")
        st.info(f"**路线 B**：{human_lane_b(state.lane_b_state)}")
        return

    c1, c2 = st.columns(2)
    with c1:
        state.lane_a_state = st.selectbox(
            "路线 A 状态码",
            LANE_A_STATES,
            index=LANE_A_STATES.index(state.lane_a_state),
            help="HUNT → LOCKED → HALT",
        )
        st.info(f"**路线 A**：{human_lane_a(state.lane_a_state)}")
    with c2:
        state.lane_b_state = st.selectbox(
            "路线 B 状态码",
            LANE_B_STATES,
            index=LANE_B_STATES.index(state.lane_b_state),
            help="IDLE → SCOUT → DEPLOY → COOLDOWN → IDLE (+ HALT)",
        )
        st.info(f"**路线 B**：{human_lane_b(state.lane_b_state)}")


def _render_agents(state: DashboardState) -> None:
    st.subheader("组件心跳（演示）")
    if _readonly():
        st.caption("只读：这些灯是本地演示，不代表云端 Agent。")
    elif st.button("全部 Ping", use_container_width=False):
        state.ping_all_agents()
        st.rerun()

    cols = st.columns(2)
    for idx, (agent_id, agent) in enumerate(state.agents.items()):
        with cols[idx % 2]:
            status = "在线" if agent.is_alive else "未响应"
            st.markdown(f"**{agent.label}** · {status}")
            st.caption(agent.role)
            if agent.last_seen:
                st.caption(f"最后：{format_last_updated_zh(agent.last_seen)}")
            if not _readonly() and st.button("Ping", key=f"ping_{agent_id}"):
                state.ping_agent(agent_id)
                st.rerun()


def _render_lab() -> None:
    config = load_portfolio_config(CONFIG_PATH)
    state = _init_session()
    st.header("实验室视图")
    st.caption(
        f"总资金 ${config.total_capital:,.0f} · "
        f"A:B = {config.lane_a_ratio:.0%}:{config.lane_b_ratio:.0%} · "
        f"环境 {config.futu.env}"
    )
    left, right = st.columns([1, 1])
    with left:
        _render_quote(config.futu.host, config.futu.port)
        _render_lane_states(state)
    with right:
        _render_ledger(state)
    st.divider()
    _render_agents(state)


def main() -> None:
    st.set_page_config(
        page_title="交易指挥室",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    if not _require_password():
        return

    auto_ops = False
    if _readonly():
        auto_ops = _render_ops_home()
        with st.expander("实验室视图（只读，可折叠）"):
            _render_lab()
        _schedule_browser_auto_refresh(auto_ops)
        return

    tab_ops, tab_lab = st.tabs(["指挥室", "实验室"])
    with tab_ops:
        auto_ops = _render_ops_home()
    with tab_lab:
        _render_lab()
    _schedule_browser_auto_refresh(auto_ops)


if __name__ == "__main__":
    main()
