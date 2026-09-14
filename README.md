# futu-trader dashboard (public deploy mirror)

Read-only T3 ops command center for Render (PROH-126/128/129).

- Shell: FastAPI + `web/console`（总览 / 策略共驾 / 市场脉搏 / 日复盘）
- Demo fixtures only until `REPORTS_REMOTE_BASE` is set
- No live trading credentials; no endpoints that enable live / `trading_enabled`
- Password gate via `DASHBOARD_PASSWORD`

Hosted: https://futu-trader-dashboard.onrender.com

Local smoke:

```bash
pip install -e ".[console]"
set DASHBOARD_PASSWORD=changeme
set REPORTS_DIR=fixtures/staging/promotion_ok
uvicorn api.app:create_app --factory --host 0.0.0.0 --port 8787
```
