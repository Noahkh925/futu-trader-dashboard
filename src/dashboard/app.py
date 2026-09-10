"""Streamlit interactive / ops dashboard for futu-trader."""

from __future__ import annotations

import os
import time
from pathlib import Path

import streamlit as st

from core.portfolio import load_portfolio_config
from dashboard.ops import (
    OpsSnapshot,
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

# Calm ops palette: slate + teal (not purple / not cream-serif cliché)
_THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,450;9..40,600;9..40,700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

html, body, [class*="css"] {
  font-family: "DM Sans", "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.stApp {
  background:
    radial-gradient(1200px 600px at 12% -10%, #d9efe8 0%, transparent 55%),
    radial-gradient(900px 500px at 100% 0%, #e7eef6 0%, transparent 50%),
    linear-gradient(180deg, #f4f7fa 0%, #eef2f6 100%);
}
header[data-testid="stHeader"] { background: transparent; }
section.main > div { padding-top: 0.35rem; max-width: 980px; }
div[data-testid="stToolbar"] { display: none; }

div[data-testid="stVerticalBlockBorderWrapper"] {
  background: rgba(255,255,255,0.82) !important;
  border: 1px solid rgba(20, 40, 60, 0.08) !important;
  border-radius: 16px !important;
  box-shadow: 0 6px 18px rgba(20, 40, 60, 0.045);
  padding: 0.15rem 0.2rem 0.35rem;
}

.ops-hero {
  border-radius: 18px;
  padding: 1.15rem 1.25rem 1.05rem;
  margin: 0.25rem 0 0.85rem;
  border: 1px solid rgba(15, 40, 50, 0.08);
  box-shadow: 0 10px 28px rgba(20, 40, 60, 0.06);
}
.ops-hero.safe {
  background: linear-gradient(135deg, #e8f7f1 0%, #f3faf7 55%, #ffffff 100%);
  border-left: 5px solid #1f8a5b;
}
.ops-hero.watch {
  background: linear-gradient(135deg, #fff6e8 0%, #fffaf2 55%, #ffffff 100%);
  border-left: 5px solid #c47c16;
}
.ops-hero.urgent {
  background: linear-gradient(135deg, #fdeceb 0%, #fff5f4 55%, #ffffff 100%);
  border-left: 5px solid #c23b2e;
}
.ops-kicker {
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #5b6b76;
  margin: 0 0 0.35rem;
  font-weight: 600;
}
.ops-hero h2 {
  margin: 0;
  font-size: 1.55rem;
  line-height: 1.25;
  color: #13232d;
  font-weight: 700;
}
.ops-hero p {
  margin: 0.45rem 0 0;
  color: #3d4f5c;
  font-size: 1.02rem;
  line-height: 1.45;
}
.ops-pill-row { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-top: 0.85rem; }
.ops-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.28rem 0.7rem;
  border-radius: 999px;
  font-size: 0.84rem;
  font-weight: 600;
  background: rgba(255,255,255,0.72);
  border: 1px solid rgba(20,40,60,0.08);
  color: #243743;
}
.lane-card {
  background: #fff;
  border-radius: 14px;
  padding: 0.9rem 1rem;
  border: 1px solid rgba(20,40,60,0.07);
  border-left: 4px solid #2f7d6d;
  min-height: 92px;
  margin-bottom: 0.35rem;
}
.lane-card.halt { border-left-color: #c23b2e; }
.lane-card.busy { border-left-color: #2f7d6d; }
.lane-card.idle { border-left-color: #7e8d99; }
.lane-card .label { color: #6a7a86; font-size: 0.82rem; margin: 0; }
.lane-card .value { color: #13232d; font-size: 1.18rem; font-weight: 700; margin: 0.28rem 0 0; }
.pnl-big {
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0.15rem 0 0.35rem;
  color: #13232d;
}
.pnl-big.up { color: #1f8a5b; }
.pnl-big.down { color: #c23b2e; }
.ops-gate {
  max-width: 420px;
  margin: 12vh auto 0;
  background: rgba(255,255,255,0.92);
  border: 1px solid rgba(20,40,60,0.08);
  border-radius: 18px;
  padding: 1.4rem 1.35rem 1.15rem;
  box-shadow: 0 16px 40px rgba(20,40,60,0.08);
}
.ops-gate h1 { font-size: 1.55rem; margin: 0 0 0.35rem; color: #13232d; }
.ops-gate p { color: #5b6b76; margin: 0 0 0.35rem; }

div[data-testid="stMetricValue"] { font-size: 1.28rem !important; font-weight: 650 !important; }
div[data-testid="stMetricLabel"] { color: #5b6b76 !important; }
div[data-testid="stAlert"] { border-radius: 12px; }

div[data-testid="stRadio"] > label { display: none; }
div[data-testid="stRadio"] div[role="radiogroup"] {
  gap: 0.4rem;
  background: rgba(255,255,255,0.7);
  border: 1px solid rgba(20,40,60,0.08);
  border-radius: 999px;
  padding: 0.28rem;
}
div[data-testid="stRadio"] label[data-baseweb="radio"] {
  background: transparent;
  border-radius: 999px !important;
  padding: 0.28rem 0.85rem !important;
}

@media (max-width: 768px) {
  .ops-hero h2 { font-size: 1.28rem; }
  .pnl-big { font-size: 1.65rem; }
  section.main > div { padding-left: 0.35rem; padding-right: 0.35rem; }
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


def _tone(snap: OpsSnapshot) -> str:
    if snap.trading_enabled or snap.worry_level == "urgent":
        return "urgent"
    if snap.worry_level == "watch":
        return "watch"
    return "safe"


def _lane_tone(state: str) -> str:
    if state == "HALT":
        return "halt"
    if state in {"LOCKED", "DEPLOY", "SCOUT"}:
        return "busy"
    return "idle"


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


def _render_hero(snap: OpsSnapshot) -> None:
    tone = _tone(snap)
    switch = "开着 · 真钱通道已打开" if snap.trading_enabled else "关闭 · 不会真钱下单"
    title = snap.worry_label
    detail = snap.alert
    st.markdown(
        f"""
<div class="ops-hero {tone}">
  <div class="ops-kicker">现在安全吗</div>
  <h2>{title}</h2>
  <p>{detail}</p>
  <div class="ops-pill-row">
    <span class="ops-pill">真下单：{switch}</span>
    <span class="ops-pill">{snap.futu_env_label}</span>
    <span class="ops-pill">资金 ${snap.total_capital:,.0f}</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_drill(snap: OpsSnapshot) -> None:
    ratio = 0.0
    if snap.required_n > 0:
        ratio = min(1.0, max(0.0, snap.counting_streak / snap.required_n))
    with st.container(border=True):
        st.markdown("**练兵进度**")
        st.caption("连续合格模拟日越多，越接近可以讨论真钱。")
        c1, c2 = st.columns([1.2, 1])
        with c1:
            st.metric("已完成", f"{snap.counting_streak} / {snap.required_n} 天")
            st.progress(ratio, text=f"完成度 {ratio:.0%}")
        with c2:
            st.metric("结果", snap.promotion_label)
            if snap.promotion_verdict == "PASS":
                st.caption("门槛已够。下一步仍要人工决定，网页不会自动开真下单。")
            else:
                remain = max(0, snap.required_n - snap.counting_streak)
                st.caption(f"大约还差 {remain} 个合格日。继续练即可。")


def _render_lanes(snap: OpsSnapshot) -> None:
    with st.container(border=True):
        st.markdown("**两条路线现在在干嘛**")
        st.caption("看人话状态就好，不用记英文代码。")
        a, b = st.columns(2)
        with a:
            st.markdown(
                f"""
<div class="lane-card {_lane_tone(snap.lane_a_state)}">
  <p class="label">路线 A · 趋势</p>
  <p class="value">{snap.lane_a_label}</p>
</div>
""",
                unsafe_allow_html=True,
            )
        with b:
            st.markdown(
                f"""
<div class="lane-card {_lane_tone(snap.lane_b_state)}">
  <p class="label">路线 B · 财报期权</p>
  <p class="value">{snap.lane_b_label}</p>
</div>
""",
                unsafe_allow_html=True,
            )


def _render_pnl(snap: OpsSnapshot) -> None:
    with st.container(border=True):
        st.markdown("**最近一天赚亏**")
        st.caption("先看合计，再看两条路线。正数是赚，负数是亏。")
        if not snap.last_session_date:
            st.info("暂无日报。跑完模拟日后会自动出现。")
            return

        total = snap.last_pnl_total
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
        if snap.last_halts:
            st.caption("有停手时，先弄清当天发生了什么，再决定要不要担心。")
        else:
            st.caption("当天没有停手记录。")


def _render_ops_home() -> None:
    snap = build_ops_snapshot(CONFIG_PATH)
    st.markdown(_THEME_CSS, unsafe_allow_html=True)

    head_l, head_r = st.columns([3.2, 1])
    with head_l:
        st.markdown("### 交易指挥室")
        st.caption("给非技术同学：一眼看懂安不安全、练到哪、最近赚亏。")
    with head_r:
        if st.button("刷新概况", use_container_width=True, help="重新读取最新日报与配置"):
            st.session_state["ops_refreshed_at"] = time.time()
            st.rerun()
        refreshed = st.session_state.get("ops_refreshed_at")
        if refreshed:
            st.caption(time.strftime("更新于 %H:%M:%S", time.localtime(refreshed)))
        else:
            st.caption("点一下可刷新")

    if snap.data_mode == "demo_fixtures":
        st.warning("当前是**演示数据**（示例练兵），不是实盘实况。")
    elif snap.data_mode == "live_virtual":
        mode = snap.opend_mode or "未知"
        st.success(f"当前是**本机虚拟盘同步实况**（连接标记：`{mode}`）。")
    elif snap.data_mode == "empty":
        st.info("还没有可读的日报。跑完模拟交易日后会自动显示。")

    focus = st.radio(
        "我现在想看",
        options=["一眼总览", "练兵进度", "最近赚亏"],
        horizontal=True,
        help="切到你最关心的一块；总览会把关键信息都排好。",
        key="ops_focus",
    )

    _render_hero(snap)

    if focus == "一眼总览":
        _render_drill(snap)
        _render_lanes(snap)
        _render_pnl(snap)
    elif focus == "练兵进度":
        _render_drill(snap)
        with st.expander("顺带看一眼两条路线"):
            _render_lanes(snap)
    else:
        _render_pnl(snap)
        with st.expander("顺带看一眼练兵进度"):
            _render_drill(snap)

    with st.expander("这是什么意思？常见问题"):
        st.markdown(
            """
- **真下单总开关**：关着就不会真钱下单。这个网页**不能**把它打开。
- **练兵进度**：连续合格的模拟交易日。没满目标前，先练、别急。
- **路线 A / B**：两条不同打法。看中文状态即可。
- **演示数据**：样例，用来熟悉页面；接上真实日报后会换成实况。
- **虚拟盘实况**：本机/同步过来的模拟交易日报，仍不是真钱下单。
"""
        )

    with st.expander("更多技术细节（一般不用看）"):
        st.write(f"实验编号：`{snap.experiment_id}`")
        st.write(f"晋级原文：`{snap.promotion_verdict}`")
        st.write(f"环境原文：`{snap.futu_env}`")
        st.write(f"连接标记：`{snap.opend_mode or '—'}`")
        st.code(snap.reports_dir)


def _render_quote(config_host: str, config_port: int) -> QuoteSnapshot:
    st.subheader("QQQ 参考报价")
    col_refresh, col_auto = st.columns([1, 2])
    with col_refresh:
        refresh = st.button("刷新报价", use_container_width=True, key="lab_quote_refresh")
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
        page_icon="🧭",
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
