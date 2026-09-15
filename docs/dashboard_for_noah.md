# 看板怎么看（给人话版）

打开看板后，首页要能在几秒内回答这些事：

1. **今天安全吗** — 真下单关着吗？有没有要留意的黄/红提示？
2. **赚亏多少** — 最近一个交易日 A+B 合计
3. **双路怎样** — A（趋势）/ B（财报）各自人话状态
4. **持仓是什么** — 标的、数量、均价、名义金额、浮动盈亏
5. **成交明细** — 最近买了什么、大概多少钱（已平仓也会留在表里）
6. **今日活动** — 日跑/侦察/成交/心跳，避免整页像「停住」

## 练兵纪元（PROH-190）

**练兵纪元起点 = 2026-09-15（day 0）。**

- 此日之前的美股纸面仓 / 旧 staging / live_reports **不计入**当前晋级进度。
- 看板「练兵 X / N」从 **0** 起算；今日结束后若是有效模拟日再进 1。
- 旧日报可归档到 `artifacts/archive/us_pre_epoch_2026-09-15/`，或保留但标记 `pre_epoch_excluded`。

云端地址（只读）：https://futu-trader-dashboard.onrender.com  
本机预览：`streamlit run src/dashboard/app.py` → http://localhost:8501

**这个网页不能打开真下单。** 总开关关着 = 不会真钱下单。

---

## 成交明细怎么读

| 列 | 意思 |
|----|------|
| 路线 | A 趋势 / B 财报 |
| 买/卖 | 方向 |
| 数量 · 价格 · 名义 | 买了多少、单价、大约花了多少钱 |
| 模式 | **真模拟** = OpenD 模拟盘成交；**假成交** = 本地 mock |

有成交但已平仓时，持仓区可能空白，**成交明细仍会留下该笔**。  
B 路无财报事件日可以是 0 笔——看「今日活动」确认系统做过侦察/日跑。

---

## 颜色与条幅

| 你看到的 | 意思 | 通常怎么办 |
|----------|------|------------|
| 绿 / 「放心」类结论 | 开关关着、数据大致正常 | 继续看盈亏与持仓即可 |
| 黄条「演示数据」 | 还在看**示例**，不是你的真日报 | 本机日跑 + 同步后会换实况 |
| 黄条「可能过期 / stale」 | 快照旧了 | 本机跑小时心跳或日跑，再 sync |
| 红条「同步失败」 | 云端拉不到远程日报 | 查 `REPORTS_REMOTE_BASE`、本机 `--push` |
| 红条「真下单开着」 | 配置里真钱通道开了 | 立刻找人确认；看板本身改不了 |

---

## 本机预览 vs 云端

| | 本机预览 | 云端 Render |
|--|----------|-------------|
| 能不能连你电脑上的 OpenD | 可以探测 | **不能** |
| 数据从哪来 | `REPORTS_DIR` / `logs/staging` | 远程 `REPORTS_REMOTE_BASE` 快照 |
| 「截至时间旧」 | 本机日报/心跳没更新 | 本机没同步，**不是**「云端 OpenD 没开」 |

数据链一句话：

```text
本机 OpenD → RTH 常驻模拟 → 日终收口/小时心跳 →（可选同步到公开仓）→ 看板只读展示
```

---

## 日跑与刷新（你怎么操作）

### 交易日自动跑（推荐）

```powershell
# 开市常驻（主入口：盘中模拟成交）
.\scripts\run_rth_runner.ps1
.\scripts\register_rth_runner_task.ps1 -At "21:25"
# 人话一页：docs/boot_opend_rth_auto.md

# 收盘后日终收口（不开仓；保留日报 + 可选 sync）
.\scripts\run_unattended_day.ps1
.\scripts\register_unattended_task.ps1 -At "16:15"
```

细节：[`rth_sim_runner.md`](rth_sim_runner.md)、[`unattended_day_run.md`](unattended_day_run.md)

### 约每小时刷新云看板

```powershell
.\scripts\run_hourly_ops_refresh.ps1 -Sync -Push
.\scripts\register_hourly_ops_task.ps1 -EveryMinutes 60 -WithSync -WithPush
```

细节：[`dashboard_hourly_refresh.md`](dashboard_hourly_refresh.md)

### 看板页上

- **手动刷新**：立刻重读最新快照
- **自动刷新**：默认约 1 小时由浏览器重载（不卡服务器）
- 间隔：`DASHBOARD_REFRESH_SECONDS`（默认 3600）

### 本机预览看板

```powershell
$env:DASHBOARD_HOSTING = "local"
$env:REPORTS_DIR = "logs/staging"
streamlit run src/dashboard/app.py
```

---

## 常见失败怎么办

| 现象 | 先查什么 |
|------|----------|
| 一直「演示数据」 | 云端是否设了 `REPORTS_REMOTE_BASE`？本机是否 `--push` 成功？ |
| 云端说「不可达 / 未连上」且你本机 OpenD 明明开着 | 云端**本来就连不上**本机；看「截至」时间和同步，不要纠结本机开关文案 |
| `run_status=failed` | OpenD 没登录模拟盘，或端口不是 `11111` |
| `run_status=degraded` / `opend_sim_fallback_mock` | 跑完了但是 mock，**不算**有效练兵 |
| 计划任务显示 Failed | 看 `logs/.../run_status.json`；小时任务默认软失败（OpenD 关着也可 exit 0） |
| 持仓空白 | 当日无仓，或旧日报没有 `open_positions`（会尽量从状态推断）；已平仓请看成交明细 |
| 只有「成交 N 笔」、看不到金额 | 旧日报缺 `fills[]`；新日跑会写入；本机也可读 `lane_*.jsonl` |
| 整页像停住 | 看「今日活动」：日跑完成 / B 侦察 / 小时心跳 |

更长练兵步骤：[`opend_staging_day_drill.md`](opend_staging_day_drill.md)。  
迁云准备（哪些做了 / 没做）：[`cloud_migration_checklist.md`](cloud_migration_checklist.md)。
