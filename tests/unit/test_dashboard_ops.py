"""Unit tests for dashboard ops snapshot helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from dashboard.ops import (
    build_activity_timeline,
    build_fills,
    build_ops_snapshot,
    build_positions,
    connection_label_zh,
    detect_hosting_mode,
    extract_events,
    health_hint_zh,
    human_fill_mode,
    human_lane_a,
    human_lane_b,
    lane_a_status_zh,
    lane_b_status_zh,
    should_probe_local_opend,
    sync_remote_reports,
)
from dashboard.ops_export import main as ops_export_main

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "portfolio.yaml"
FIXTURES_OK = ROOT / "fixtures" / "staging" / "promotion_ok"


def test_human_labels():
    assert "找机会" in human_lane_a("HUNT")
    assert "暂停" in human_lane_b("HALT")


def test_detect_hosting_mode_cloud_vs_local(monkeypatch):
    monkeypatch.delenv("DASHBOARD_HOSTING", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("DASHBOARD_READONLY", raising=False)
    assert detect_hosting_mode() == "local"

    monkeypatch.setenv("DASHBOARD_READONLY", "1")
    assert detect_hosting_mode() == "cloud"

    monkeypatch.delenv("DASHBOARD_READONLY", raising=False)
    monkeypatch.setenv("RENDER", "true")
    assert detect_hosting_mode() == "cloud"

    monkeypatch.setenv("DASHBOARD_HOSTING", "local")
    assert detect_hosting_mode() == "local"


def test_should_probe_local_opend(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PROBE_OPEND", raising=False)
    assert should_probe_local_opend(hosting_mode="local", probe_opend=True) is True
    assert should_probe_local_opend(hosting_mode="cloud", probe_opend=True) is False
    assert should_probe_local_opend(hosting_mode="local", probe_opend=False) is False
    monkeypatch.setenv("DASHBOARD_PROBE_OPEND", "1")
    assert should_probe_local_opend(hosting_mode="cloud", probe_opend=True) is True


def test_health_hint_cloud_never_blames_local_opend():
    hint = health_hint_zh(
        opend_reachable=False,
        opend_mode="opend",
        sync_status="ok",
        hosting_mode="cloud",
    )
    assert "云端不直连" in hint
    assert "请确认 OpenD 已启动" not in hint
    assert "请确认本机" not in hint

    fallback = health_hint_zh(
        opend_reachable=False,
        opend_mode="opend_sim_fallback_mock",
        sync_status="ok",
        hosting_mode="cloud",
    )
    assert "与云端能否连 OpenD 无关" in fallback
    assert "请确认 OpenD 已启动" not in fallback

    local = health_hint_zh(
        opend_reachable=False,
        opend_mode="opend",
        sync_status="ok",
        hosting_mode="local",
    )
    assert "请确认 OpenD 已启动" in local


def test_connection_label_cloud():
    assert "不直连" in connection_label_zh(hosting_mode="cloud", opend_reachable=False)
    assert "可达" in connection_label_zh(hosting_mode="local", opend_reachable=True)


def test_ops_snapshot_from_fixtures():
    with patch("dashboard.ops.detect_hosting_mode", return_value="local"):
        snap = build_ops_snapshot(
            CONFIG,
            reports_dir=FIXTURES_OK,
            probe_opend=False,
        )
    assert snap.trading_enabled is False
    assert snap.futu_env == "staging"
    assert snap.counting_streak >= 1
    assert snap.last_session_date is not None
    assert snap.data_mode == "demo_fixtures"
    assert "演示" in snap.alert
    assert snap.sync_status == "ok"
    assert snap.data_as_of is not None
    assert snap.synced_at is not None
    assert len(snap.promotion_calendar) >= 3
    assert len(snap.pnl_points) >= 3
    assert snap.pnl_points[-1]["date"] == snap.last_session_date
    assert snap.lane_a_summary.get("symbol") == "US.QQQ"
    assert snap.lane_b_summary.get("prefer_symbol") == "US.AAPL"
    assert snap.lane_b_summary.get("day_mode") == "deploy"
    assert isinstance(snap.events, list)
    assert "OpenD" in snap.health_hint_zh or "mock" in snap.health_hint_zh.lower()
    assert snap.hosting_mode == "local"
    assert snap.connection_label_zh
    assert "数据" in snap.data_path_zh
    assert len(snap.positions) >= 1
    assert any(p.get("lane") == "A" and p.get("symbol") == "US.QQQ" for p in snap.positions)
    assert "持仓" in snap.lane_a_status_zh or "锁定" in snap.lane_a_status_zh
    assert "部署" in snap.lane_b_status_zh or "持仓" in snap.lane_b_status_zh
    assert len(snap.fills) >= 1
    assert any(f.get("notional") for f in snap.fills)
    assert any(f.get("mode_zh") in {"真模拟", "假成交"} for f in snap.fills)
    assert len(snap.activity) >= 1
    payload = snap.to_dict()
    assert "sync_status" in payload
    assert "pnl_points" in payload
    assert "promotion_calendar" in payload
    assert "hosting_mode" in payload
    assert "positions" in payload
    assert "fills" in payload
    assert "activity" in payload


def test_ops_snapshot_cloud_skips_opend_probe(tmp_path: Path):
    """Cloud hosting must not TCP-probe localhost OpenD or blame local OpenD."""
    day = tmp_path / "2026-09-09"
    day.mkdir()
    report = {
        "schema_version": "1.0",
        "session_date": "2026-09-09",
        "generated_at": "2026-09-09T20:00:00+00:00",
        "opend_mode": "opend",
        "futu_env": "staging",
        "capital": {
            "total": 5000.0,
            "lane_a_start": 3500.0,
            "lane_b_start": 1500.0,
            "lane_a_end": 3510.0,
            "lane_b_end": 1495.0,
            "lane_a_pnl": 10.0,
            "lane_b_pnl": -5.0,
        },
        "lane_a": {
            "state": "HALT",
            "halt_reason": "daily_loss_limit",
            "bars_seen": 10,
            "fills_count": 1,
            "signals_count": 1,
            "realized_pnl": 10.0,
            "paper_mode": "mock_fill",
            "symbol": "US.QQQ",
        },
        "lane_b": {
            "state": "SCOUT",
            "halt_reason": None,
            "day_mode": "scout_only",
            "fills_count": 0,
            "scout_hits": 0,
            "realized_pnl": -5.0,
            "paper_mode": "mock_fill",
            "prefer_symbol": "US.MSFT",
        },
        "halts": [],
        "disconnects": [],
        "errors": [],
        "fills_summary": {"lane_a": 1, "lane_b": 0, "total": 1},
        "artifact_paths": {
            "daily_report": "",
            "lane_a_log": "",
            "lane_b_log": "",
        },
        "counts_for_promotion": True,
        "experiment_id": "baseline",
    }
    (day / "daily_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (
        patch("dashboard.ops.detect_hosting_mode", return_value="cloud"),
        patch("dashboard.ops.probe_opend_reachable") as probe,
    ):
        snap = build_ops_snapshot(CONFIG, reports_dir=tmp_path, probe_opend=True)
    probe.assert_not_called()
    assert snap.hosting_mode == "cloud"
    assert snap.opend_reachable is False
    assert "不直连" in snap.connection_label_zh
    assert "请确认 OpenD 已启动" not in snap.health_hint_zh
    assert "旧快照" in snap.data_path_zh or "云端" in snap.data_path_zh
    assert snap.trading_enabled is False


def test_ops_snapshot_empty_dir(tmp_path: Path):
    empty = tmp_path / "no_reports"
    empty.mkdir()
    with patch("dashboard.ops.detect_hosting_mode", return_value="local"):
        snap = build_ops_snapshot(CONFIG, reports_dir=empty, probe_opend=False)
    assert snap.data_mode == "empty"
    assert snap.sync_status == "empty"
    assert snap.last_session_date is None
    assert snap.events == []
    assert snap.promotion_calendar == []
    assert snap.pnl_points == []
    assert snap.sync_error


def test_ops_snapshot_live_reports_style(tmp_path: Path):
    """Minimal live_reports-shaped tree (non-fixtures path → live_virtual)."""
    day = tmp_path / "2026-09-09"
    day.mkdir()
    report = {
        "schema_version": "1.0",
        "session_date": "2026-09-09",
        "generated_at": "2026-09-09T20:00:00+00:00",
        "opend_mode": "opend",
        "futu_env": "staging",
        "capital": {
            "total": 5000.0,
            "lane_a_start": 3500.0,
            "lane_b_start": 1500.0,
            "lane_a_end": 3510.0,
            "lane_b_end": 1495.0,
            "lane_a_pnl": 10.0,
            "lane_b_pnl": -5.0,
        },
        "lane_a": {
            "state": "HALT",
            "halt_reason": "daily_loss_limit",
            "bars_seen": 10,
            "fills_count": 1,
            "signals_count": 1,
            "realized_pnl": 10.0,
            "paper_mode": "mock_fill",
            "symbol": "US.QQQ",
        },
        "lane_b": {
            "state": "SCOUT",
            "halt_reason": None,
            "day_mode": "scout_only",
            "fills_count": 0,
            "scout_hits": 0,
            "realized_pnl": -5.0,
            "paper_mode": "mock_fill",
            "prefer_symbol": "US.MSFT",
        },
        "halts": [
            {"lane": "A", "reason": "daily_loss_limit", "state": "HALT"},
        ],
        "disconnects": [],
        "errors": [{"where": "opend_probe", "message": "timeout"}],
        "fills_summary": {"lane_a": 1, "lane_b": 0, "total": 1},
        "artifact_paths": {
            "daily_report": "",
            "lane_a_log": "",
            "lane_b_log": "",
        },
        "counts_for_promotion": True,
        "experiment_id": "baseline",
    }
    (day / "daily_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with patch("dashboard.ops.detect_hosting_mode", return_value="local"):
        snap = build_ops_snapshot(CONFIG, reports_dir=tmp_path, probe_opend=False)
    assert snap.data_mode == "live_virtual"
    assert snap.sync_status == "ok"
    assert snap.data_as_of == "2026-09-09T20:00:00+00:00"
    assert snap.last_pnl_a == 10.0
    assert snap.last_pnl_b == -5.0
    assert snap.pnl_points == [
        {"date": "2026-09-09", "pnl_a": 10.0, "pnl_b": -5.0, "pnl_total": 5.0}
    ]
    assert snap.promotion_calendar[0]["date"] == "2026-09-09"
    assert snap.promotion_calendar[0]["halt_count"] == 1
    assert snap.lane_a_summary["halt_reason_zh"] and "亏损" in snap.lane_a_summary["halt_reason_zh"]
    assert snap.lane_b_summary["prefer_symbol"] == "US.MSFT"
    assert any(e["severity"] == "critical" for e in snap.events)
    assert any("触及当日亏损" in e["reason_zh"] for e in snap.events)
    assert any("timeout" in e["reason_zh"] for e in snap.events)


def test_extract_events_disconnect():
    events = extract_events(
        {
            "generated_at": "2026-09-09T12:00:00+00:00",
            "session_date": "2026-09-09",
            "halts": [],
            "errors": [],
            "disconnects": [
                {
                    "lane": "feed",
                    "detected": True,
                    "detail": "OpenD down",
                    "opend_mode": "mock",
                }
            ],
        }
    )
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert "断连" in events[0]["reason_zh"]


def test_sync_remote_failed_with_cache(tmp_path: Path):
    day = tmp_path / "2026-09-08"
    day.mkdir()
    (day / "daily_report.json").write_text("{}", encoding="utf-8")
    with patch("dashboard.ops._http_get_json", side_effect=TimeoutError("boom")):
        result = sync_remote_reports("https://example.invalid/live_reports", tmp_path)
    assert result.status == "stale"
    assert result.error and "缓存" in result.error


def test_sync_remote_failed_empty(tmp_path: Path):
    dest = tmp_path / "cache"
    with patch("dashboard.ops._http_get_json", side_effect=TimeoutError("boom")):
        result = sync_remote_reports("https://example.invalid/live_reports", dest)
    assert result.status == "failed"
    assert result.error


def test_ops_export_cli(tmp_path: Path, capsys):
    code = ops_export_main(
        [
            "--config",
            str(CONFIG),
            "--reports-dir",
            str(FIXTURES_OK),
            "--no-opend-probe",
            "--indent",
            "0",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["sync_status"] == "ok"
    assert payload["lane_a_summary"]["symbol"] == "US.QQQ"
    assert isinstance(payload["pnl_points"], list)
    assert "hosting_mode" in payload
    assert "connection_label_zh" in payload
    assert "data_path_zh" in payload
    assert isinstance(payload["positions"], list)
    assert payload["hosting_mode"] == "local"


def test_build_positions_explicit_and_infer():
    explicit = {
        "open_positions": [
            {
                "lane": "A",
                "symbol": "US.QQQ",
                "side": "LONG",
                "qty": 3,
                "unrealized_pnl": 1.5,
            }
        ]
    }
    rows = build_positions(explicit)
    assert len(rows) == 1
    assert rows[0]["side_zh"] == "做多"
    assert rows[0]["unrealized_pnl"] == 1.5

    inferred = build_positions(
        {
            "lane_a": {"state": "LOCKED", "symbol": "US.SPY"},
            "lane_b": {"state": "DEPLOY", "prefer_symbol": "US.MSFT"},
        }
    )
    assert {r["lane"] for r in inferred} == {"A", "B"}
    assert inferred[0]["symbol"] == "US.SPY"


def test_lane_status_zh_idle_empty_and_halt():
    assert "无财报" in lane_b_status_zh(
        {"lane_b": {"state": "IDLE", "day_mode": "idle_empty"}}
    )
    assert "亏损" in lane_a_status_zh(
        {"lane_a": {"state": "HALT", "halt_reason": "daily_loss_limit"}}
    )


def test_cloud_health_hint_does_not_blame_local_opend():
    hint = health_hint_zh(
        opend_reachable=False,
        opend_mode="opend",
        sync_status="ok",
        hosting_mode="cloud",
    )
    assert "不直连" in hint
    assert "请确认 OpenD 已启动" not in hint


def test_build_fills_from_report_and_jsonl_fallback(tmp_path: Path):
    report = {
        "session_date": "2024-07-30",
        "fills": [
            {
                "lane": "A",
                "ts": "2024-07-30T14:02:00+00:00",
                "symbol": "US.QQQ",
                "side": "BUY",
                "qty": 2,
                "price": 100.0,
                "notional": 200.0,
                "mode": "opend_sim",
            }
        ],
    }
    rows = build_fills(report)
    assert len(rows) == 1
    assert rows[0]["side_zh"] == "买入"
    assert rows[0]["mode_zh"] == "真模拟"
    assert human_fill_mode("mock_fill") == "假成交"

    day = tmp_path / "2024-08-01"
    day.mkdir()
    (day / "lane_a.jsonl").write_text(
        json.dumps(
            {
                "ts": "2024-08-01T15:00:00+00:00",
                "type": "fill",
                "symbol": "US.SPY",
                "side": "SELL",
                "qty": 1,
                "price": 50,
                "notional": 50,
                "mode": "mock_fill",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    legacy = {
        "session_date": "2024-08-01",
        "fills_summary": {"lane_a": 1, "lane_b": 0, "total": 1},
        "artifact_paths": {
            "daily_report": "",
            "lane_a_log": "lane_a.jsonl",
            "lane_b_log": "",
        },
    }
    fallback = build_fills(legacy, reports_dir=tmp_path)
    assert len(fallback) == 1
    assert fallback[0]["symbol"] == "US.SPY"
    assert fallback[0]["mode_zh"] == "假成交"


def test_activity_timeline_scout_day_without_fills():
    report = {
        "session_date": "2024-07-28",
        "generated_at": "2024-07-28T20:00:00+00:00",
        "lane_a": {"state": "HUNT", "fills_count": 0},
        "lane_b": {
            "state": "SCOUT",
            "day_mode": "scout_only",
            "fills_count": 0,
            "scout_hits": 0,
            "prefer_symbol": "US.AAPL",
        },
        "halts": [],
    }
    items = build_activity_timeline(
        report,
        fills=[],
        lane_a_status="正在找机会",
        lane_b_status="今日只扫财报、未开仓",
    )
    texts = " ".join(str(i.get("text") or "") for i in items)
    assert "日跑" in texts
    assert "侦察" in texts or "扫" in texts or "A：" in texts


def test_premarket_decision_from_fixture():
    from core.portfolio import load_portfolio_config
    from dashboard.ops import build_premarket_decision

    cfg = load_portfolio_config(CONFIG)
    pm = build_premarket_decision(cfg)
    assert pm["status"] == "ok"
    assert pm["as_of"]
    assert pm["schema_version"] == "lane_a_tech_1.0"
    assert pm["data_label"] == "demo_fixtures"
    symbols = [r["symbol"] for r in pm["deployable"]]
    assert "US.QQQ" in symbols
    assert any(r["symbol"] == "US.PENNY" for r in pm["vetoed"])
    assert "名单" in pm["footnote_zh"]
    assert pm["generated_at_zh"] and "北京时间" in pm["generated_at_zh"]


def test_premarket_decision_empty_universe(tmp_path: Path):
    from core.portfolio import load_portfolio_config
    from dashboard.ops import build_premarket_decision

    empty = ROOT / "fixtures" / "lane_a" / "empty_lane_a_tech_watchlist.json"
    target = tmp_path / "tech_watchlist.json"
    target.write_text(empty.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_portfolio_config(CONFIG)
    with patch.dict("os.environ", {"TECH_WATCHLIST_PATH": str(target)}):
        pm = build_premarket_decision(cfg)
    assert pm["status"] == "empty_universe"
    assert pm["no_trade"] is True
    assert "no_trade" in (pm["no_trade_reason_zh"] or "").lower() or "不交易" in (
        pm["no_trade_reason_zh"] or ""
    )
    assert pm["deployable"] == []


def test_premarket_decision_bad_schema(tmp_path: Path):
    from core.portfolio import load_portfolio_config
    from dashboard.ops import build_premarket_decision

    bad = ROOT / "fixtures" / "lane_a" / "invalid_lane_a_tech_watchlist.json"
    target = tmp_path / "tech_watchlist.json"
    target.write_text(bad.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_portfolio_config(CONFIG)
    with patch.dict("os.environ", {"TECH_WATCHLIST_PATH": str(target)}):
        pm = build_premarket_decision(cfg)
    assert pm["status"] == "bad_schema"
    assert pm["no_trade"] is True
    assert "不交易" in (pm["no_trade_reason_zh"] or "") or "schema" in (
        pm["detail_zh"] or ""
    ).lower()


def test_premarket_decision_waiting_when_missing(tmp_path: Path, monkeypatch):
    from core.portfolio import load_portfolio_config
    from dashboard.ops import build_premarket_decision

    missing = tmp_path / "no_such_watchlist.json"
    monkeypatch.setenv("TECH_WATCHLIST_PATH", str(missing))
    # Also block config fixture path by pointing at missing only — resolve
    # still falls through to logs; force all candidates missing via empty dir.
    monkeypatch.setenv("TECH_WATCHLIST_PATH", str(missing))
    cfg = load_portfolio_config(CONFIG)

    # Patch resolver candidates to only the missing env path.
    with patch(
        "dashboard.ops.resolve_tech_watchlist_path",
        return_value=None,
    ):
        pm = build_premarket_decision(cfg)
    assert pm["status"] == "waiting"
    assert pm["no_trade"] is True
    assert "等待" in pm["headline_zh"] or "缺文件" in (pm["no_trade_reason_zh"] or "")


def test_ops_snapshot_includes_premarket():
    with patch("dashboard.ops.detect_hosting_mode", return_value="local"):
        snap = build_ops_snapshot(
            CONFIG,
            reports_dir=FIXTURES_OK,
            probe_opend=False,
        )
    assert isinstance(snap.premarket, dict)
    assert snap.premarket.get("status") in {
        "ok",
        "empty_universe",
        "waiting",
        "bad_schema",
        "missing",
    }
    assert snap.premarket.get("schema_version") == "lane_a_tech_1.0" or snap.premarket.get(
        "status"
    ) != "ok"

