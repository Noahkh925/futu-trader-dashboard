"""Streamlit interactive / ops dashboard for futu-trader."""

from __future__ import annotations

import os
import time
from pathlib import Path

import streamlit as st

from core.portfolio import load_portfolio_config
from dashboard.ops import (
    build_ops_snapshot,
    expected_dashboard_password,
    format_money,
    human_lane_a,
    human_lane_b,
)
from dashboard.state import (
    LANE_A_STATES,
    LANE_B_STATES,
    DashboardState,
)
from data.futu_feed import QuoteSnapshot, get_qqq_snapshot

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "portfolio.yaml"

_HOME_CSS = """
<style>
/* First-screen calm: larger status blocks, readable on phone */
div[data-testid="stAlert"] p { font-size: 1.05rem; line-height: 1.45; }
div[data-testid="stMetricValue"] { font-size: 1.35rem !important; }
section.main > div { padding-top: 0.6rem; }
@media (max-width: 768px) {
  div[data-testid="stMetricValue"] { font-size: 1.2rem !important; }
  h1 { font-size: 1.55rem !important; }
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


def _require_password() -> bool:
    expected = expected_dashboard_password()
    if expected is None:
        return True
    if st.session_state.get("dashboard_authed"):
        return True
    st.title("交易指挥室")
    st.caption("输入密码后可查看运营概况。")
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
    st.markdown(_HOME_CSS, unsafe_allow_html=True)

    st.title("交易指挥室")
    st.caption("五秒看懂：能不能真下单 · 练到哪 · 最近赚亏 · 要不要担心")

    if snap.data_mode == "demo_fixtures":
        st.warning("当前是**演示数据**（示例练兵），不是实盘实况。")
    elif snap.data_mode == "empty":
        st.info("还没有可读的日报。跑完模拟交易日后，接上报告目录就会自动显示。")

    # 1) Kill switch — impossible to miss
    if snap.trading_enabled:
        st.error("**真下单总开关：开着** — 真钱通道是打开的。若不是你开的，请立刻找人关掉。")
    else:
        st.success("**真下单总开关：关闭** — 不会用真钱下单，可以安心看练兵。")

    # 2) Worry + one-line health
    if snap.worry_level == "urgent":
        st.error(snap.worry_label)
    elif snap.worry_level == "watch":
        st.warning(snap.worry_label)
    else:
        st.info(snap.worry_label)
    st.caption(snap.alert)

    # 3) Progress + environment (2-col = mobile friendly)
    m1, m2 = st.columns(2)
    m1.metric("练兵进度", f"{snap.counting_streak} / {snap.required_n}")
    m2.metric("练兵结果", snap.promotion_label)
    m3, m4 = st.columns(2)
    m3.metric("运行环境", snap.futu_env_label)
    m4.metric("配置总资金", f"${snap.total_capital:,.0f}")

    st.divider()

    # 4) Lane plain-language status
    st.subheader("两条路线现在在干嘛")
    l1, l2 = st.columns(2)
    l1.metric("路线 A（趋势）", snap.lane_a_label)
    l2.metric("路线 B（财报期权）", snap.lane_b_label)

    st.divider()

    # 5) Yesterday PnL — total first
    st.subheader("最近一天赚亏")
    if snap.last_session_date:
        st.metric("合计", format_money(snap.last_pnl_total), help=f"日期 {snap.last_session_date}")
        p1, p2, p3 = st.columns(3)
        p1.metric("日期", str(snap.last_session_date))
        p2.metric("路线 A", format_money(snap.last_pnl_a))
        p3.metric("路线 B", format_money(snap.last_pnl_b))
        if snap.last_halts:
            st.caption(f"当天停手 {snap.last_halts} 次")
        else:
            st.caption("当天没有停手记录")
    else:
        st.write("暂无日报。")

    with st.expander("更多技术细节（一般不用看）"):
        st.write(f"实验编号：`{snap.experiment_id}`")
        st.write(f"晋级原文：`{snap.promotion_verdict}`")
        st.write(f"环境原文：`{snap.futu_env}`")
        st.code(snap.reports_dir)


def _render_quote(config_host: str, config_port: int) -> QuoteSnapshot:
    st.subheader("QQQ 参考报价")
    col_refresh, col_auto = st.columns([1, 2])
    with col_refresh:
        refresh = st.button("刷新报价", use_container_width=True)
    with col_auto:
        auto = st.toggle("自动刷新（10 秒）", value=False)

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
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    if not _require_password():
        return

    if _readonly():
        _render_ops_home()
        with st.expander("实验室视图（只读，可折叠）"):
            _render_lab()
        return

    tab_ops, tab_lab = st.tabs(["指挥室", "实验室"])
    with tab_ops:
        _render_ops_home()
    with tab_lab:
        _render_lab()


if __name__ == "__main__":
    main()
