"""Streamlit interactive / ops dashboard for futu-trader."""

from __future__ import annotations

import os
import time
from pathlib import Path

import streamlit as st

from core.portfolio import load_portfolio_config
from dashboard.ops import build_ops_snapshot, expected_dashboard_password
from dashboard.state import (
    LANE_A_STATES,
    LANE_B_STATES,
    DashboardState,
)
from data.futu_feed import QuoteSnapshot, get_qqq_snapshot

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "portfolio.yaml"


def _readonly() -> bool:
    return os.environ.get("DASHBOARD_READONLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_password() -> bool:
    expected = expected_dashboard_password()
    if expected is None:
        return True
    if st.session_state.get("dashboard_authed"):
        return True
    st.title("futu-trader 指挥室")
    st.caption("请输入访问密码")
    password = st.text_input("密码", type="password")
    if st.button("进入", use_container_width=True):
        if password == expected:
            st.session_state.dashboard_authed = True
            st.rerun()
        st.error("密码不对，请重试。")
    return False


def _init_session() -> DashboardState:
    if "dashboard" not in st.session_state:
        st.session_state.dashboard = DashboardState.bootstrap(str(CONFIG_PATH))
    return st.session_state.dashboard


def _render_ops_home() -> None:
    snap = build_ops_snapshot(CONFIG_PATH)
    st.title("交易工具指挥室")
    st.caption("用大白话看清：现在能不能真下单、练到哪一步、最近一天怎样。")

    if snap.data_mode == "demo_fixtures":
        st.warning("当前显示的是**示例练兵数据**（演示用）。接上真实日报目录后会自动换成实况。")
    elif snap.data_mode == "live_virtual":
        mode = snap.opend_mode or "未知"
        st.success(f"当前为**本机虚拟盘同步实况**（OpenD 模式标记：`{mode}`）。")
    elif snap.data_mode == "empty":
        st.info("还没有可读的日报。跑完模拟交易日后，把报告目录指到 `REPORTS_DIR`。")

    st.subheader(snap.alert)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("真下单总开关", "开着 ⚠️" if snap.trading_enabled else "关闭 ✅")
    c2.metric("运行环境", snap.futu_env)
    c3.metric("练兵进度", f"{snap.counting_streak} / {snap.required_n}")
    c4.metric("晋级检查", snap.promotion_verdict)

    s1, s2, s3 = st.columns(3)
    s1.metric("路线 A（趋势）", snap.lane_a_label)
    s2.metric("路线 B（财报期权）", snap.lane_b_label)
    s3.metric("总资金（配置）", f"${snap.total_capital:,.0f}")

    st.divider()
    st.subheader("最近一次日报")
    if snap.last_session_date:
        p1, p2, p3 = st.columns(3)
        p1.metric("日期", str(snap.last_session_date))
        p2.metric(
            "路线 A 赚亏",
            "—" if snap.last_pnl_a is None else f"${snap.last_pnl_a:,.2f}",
        )
        p3.metric(
            "路线 B 赚亏",
            "—" if snap.last_pnl_b is None else f"${snap.last_pnl_b:,.2f}",
        )
        st.caption(f"当天停手次数：{snap.last_halts} · 实验 ID：{snap.experiment_id}")
    else:
        st.write("暂无日报。")

    with st.expander("给技术人员看的路径信息"):
        st.code(snap.reports_dir)


def _render_quote(config_host: str, config_port: int) -> QuoteSnapshot:
    st.subheader("US.QQQ 报价")
    col_refresh, col_auto = st.columns([1, 2])
    with col_refresh:
        refresh = st.button("刷新报价", use_container_width=True)
    with col_auto:
        auto = st.toggle("自动刷新（10s）", value=False)

    if "last_quote" not in st.session_state or refresh:
        st.session_state.last_quote = get_qqq_snapshot(host=config_host, port=config_port)
        st.session_state.quote_fetched_at = time.time()

    if auto:
        elapsed = time.time() - st.session_state.get("quote_fetched_at", 0)
        if elapsed >= 10:
            st.session_state.last_quote = get_qqq_snapshot(host=config_host, port=config_port)
            st.session_state.quote_fetched_at = time.time()
        time.sleep(0.5)
        st.rerun()

    quote: QuoteSnapshot = st.session_state.last_quote
    source_label = "富途 OpenD" if quote.source == "futu" else "Mock（OpenD 离线）"
    st.metric(label=quote.code, value=f"${quote.last_price:,.2f}", delta=source_label)
    return quote


def _render_ledger(state: DashboardState) -> None:
    st.subheader("A/B 分账 Ledger")
    a_pct, b_pct = state.lane_allocation_pct()

    m1, m2, m3 = st.columns(3)
    m1.metric("Lane A（ORB+VWAP）", f"${state.ledger.lane_a_balance:,.2f}", f"{a_pct}%")
    m2.metric("Lane B（财报期权）", f"${state.ledger.lane_b_balance:,.2f}", f"{b_pct}%")
    m3.metric("合计", f"${state.ledger.total:,.2f}")

    st.progress(a_pct / 100, text=f"Lane A {a_pct}% · Lane B {b_pct}%")

    if _readonly():
        st.caption("只读模式：不能在网页上改余额。")
        return

    st.caption("调整余额（模拟成交 / 资金划转）")
    c1, c2, c3 = st.columns(3)
    with c1:
        delta_a = st.number_input("Lane A Δ", value=0.0, step=50.0, format="%.2f")
    with c2:
        delta_b = st.number_input("Lane B Δ", value=0.0, step=50.0, format="%.2f")
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
    st.subheader("策略状态机")
    if _readonly():
        st.info(f"**Lane A**：**{state.lane_a_state}**")
        st.info(f"**Lane B**：**{state.lane_b_state}**")
        return

    c1, c2 = st.columns(2)
    with c1:
        state.lane_a_state = st.selectbox(
            "Lane A 状态",
            LANE_A_STATES,
            index=LANE_A_STATES.index(state.lane_a_state),
            help="HUNT → LOCKED → HALT",
        )
        st.info(f"**Lane A** 当前：**{state.lane_a_state}**")
    with c2:
        state.lane_b_state = st.selectbox(
            "Lane B 状态",
            LANE_B_STATES,
            index=LANE_B_STATES.index(state.lane_b_state),
            help="IDLE → SCOUT → DEPLOY → COOLDOWN → IDLE (+ HALT)",
        )
        st.info(f"**Lane B** 当前：**{state.lane_b_state}**")


def _render_agents(state: DashboardState) -> None:
    st.subheader("Agent 心跳")
    if _readonly():
        st.caption("只读模式：心跳灯为本地演示状态，不代表 Multica 云端 Agent。")
    elif st.button("全部 Ping", use_container_width=False):
        state.ping_all_agents()
        st.rerun()

    cols = st.columns(4)
    for idx, (agent_id, agent) in enumerate(state.agents.items()):
        with cols[idx % 4]:
            status = "🟢" if agent.is_alive else "⚪"
            st.markdown(f"**{status} {agent.label}**")
            st.caption(agent.role)
            if agent.last_seen:
                st.caption(f"最后：{time.strftime('%H:%M:%S', time.localtime(agent.last_seen))}")
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
        f"OpenD {config.futu.host}:{config.futu.port} ({config.futu.env})"
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
        page_title="futu-trader 指挥室",
        page_icon="📊",
        layout="wide",
    )
    if not _require_password():
        return

    if _readonly():
        _render_ops_home()
        with st.expander("实验室视图（只读）"):
            _render_lab()
        return

    tab_ops, tab_lab = st.tabs(["指挥室", "实验室"])
    with tab_ops:
        _render_ops_home()
    with tab_lab:
        _render_lab()


if __name__ == "__main__":
    main()
