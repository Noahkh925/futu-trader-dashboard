"""Ops snapshot helpers for the read-only web dashboard."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from core.portfolio import PortfolioConfig, load_portfolio_config
from research.promotion import DayVerdict, check_promotion, discover_reports, evaluate_day

LANE_A_LABELS: dict[str, str] = {
    "HUNT": "正在找机会",
    "LOCKED": "已锁定/持仓中",
    "HALT": "已暂停（停手）",
}

LANE_B_LABELS: dict[str, str] = {
    "IDLE": "空闲待命",
    "SCOUT": "在扫财报机会",
    "DEPLOY": "已部署/持仓中",
    "COOLDOWN": "冷却中（暂不开新仓）",
    "HALT": "已暂停（停手）",
}

LANE_B_DAY_MODE_STATUS: dict[str, str] = {
    "scout": "在扫财报机会",
    "scout_only": "今日只扫财报、未开仓",
    "deploy": "已部署/持仓中",
    "idle": "空闲待命",
    "idle_empty": "今日无财报事件",
    "cooldown": "冷却中（暂不开新仓）",
    "halt": "已暂停（停手）",
    "event_cycle": "财报事件循环中",
}

SIDE_ZH: dict[str, str] = {
    "LONG": "做多",
    "SHORT": "做空",
    "BUY": "买入",
    "SELL": "卖出",
    "CALL": "看涨",
    "PUT": "看跌",
}

HostingMode = Literal["local", "cloud"]

HALT_REASON_ZH: dict[str, str] = {
    "daily_loss_limit": "触及当日亏损限额",
    "per_trade_loss_limit": "触及单笔亏损限额",
    "max_event_loss": "触及单事件亏损限额",
    "max_notional_pct": "名义本金占比超限",
    "unspecified": "未注明原因停手",
    "unknown": "未知原因停手",
}

EXCLUDED_REASON_ZH: dict[str, str] = {
    "fallback_mock_excluded": "虚拟盘失败回退假成交，不计入练兵",
    "unknown_opend_mode": "OpenD 模式未知，不计入练兵",
    "futu_env_not_staging": "环境不是 staging/simulate，不计入练兵",
    "halt_cap_exceeded": "当日停手次数超限，不计入练兵",
}

SyncStatus = Literal["ok", "stale", "failed", "empty"]
SYNC_META_NAME = ".sync_meta.json"
HEARTBEAT_NAME = "heartbeat.json"
DEFAULT_CALENDAR_DAYS = 20
DEFAULT_PNL_DAYS = 20
STALE_AFTER_DAYS = 3
# Live-virtual boards: warn when hourly heartbeat is older than this (hours).
HOURLY_STALE_AFTER_HOURS = 3
DEFAULT_PAGE_REFRESH_SECONDS = 3600
# Display-only timezone for Noah (storage / cron stay UTC or America/New_York).
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def detect_hosting_mode() -> HostingMode:
    """Distinguish local Streamlit preview from cloud-hosted dashboard.

    Cloud processes cannot reach Noah's PC localhost OpenD. Prefer explicit
    ``DASHBOARD_HOSTING``; otherwise treat Render / read-only deploys as cloud.
    """
    explicit = os.environ.get("DASHBOARD_HOSTING", "").strip().lower()
    if explicit in {"cloud", "remote", "hosted"}:
        return "cloud"
    if explicit in {"local", "preview", "desktop"}:
        return "local"
    if os.environ.get("RENDER", "").strip():
        return "cloud"
    if _env_truthy("DASHBOARD_READONLY"):
        return "cloud"
    return "local"


def should_probe_local_opend(*, hosting_mode: HostingMode, probe_opend: bool) -> bool:
    """Only TCP-probe OpenD on local preview (optional override via env)."""
    if not probe_opend:
        return False
    raw = os.environ.get("DASHBOARD_PROBE_OPEND", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return hosting_mode == "local"


def connection_label_zh(*, hosting_mode: HostingMode, opend_reachable: bool) -> str:
    if hosting_mode == "cloud":
        return "连接：云端不直连本机 OpenD"
    if opend_reachable:
        return "连接：本机预览 · OpenD 可达"
    return "连接：本机预览 · OpenD 不可达"


def data_path_caption_zh(hosting_mode: HostingMode) -> str:
    if hosting_mode == "cloud":
        return (
            "数据路径：本机日跑产物 →（若有）同步 → 云看板只读快照。"
            "「截至」时间旧 = 看的是旧快照，不是 OpenD 开关问题。"
        )
    return "数据路径：本机日报目录直读；本机预览才会探测本机 OpenD。"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _http_get_json(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "futu-trader-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_sync_meta(dest: Path, *, status: str, error: str | None, synced_at: str | None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "error": error,
        "synced_at": synced_at,
        "updated_at": _utc_now_iso(),
    }
    (dest / SYNC_META_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_sync_meta(dest: Path) -> dict[str, Any]:
    path = dest / SYNC_META_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _has_daily_reports(path: Path) -> bool:
    if not path.exists():
        return False
    return bool(list(path.glob("**/daily_report.json")))


@dataclass
class SyncResult:
    path: Path
    status: SyncStatus
    error: str | None = None
    synced_at: str | None = None


def sync_remote_reports(base_url: str, dest: Path) -> SyncResult:
    """Pull manifest + daily reports from a GitHub raw (or static) base URL.

    Unlike the old silent fallback, callers always get an explicit sync status.
    """
    base = base_url.rstrip("/")
    dest.mkdir(parents=True, exist_ok=True)
    prior = _read_sync_meta(dest)
    prior_synced = prior.get("synced_at") if isinstance(prior.get("synced_at"), str) else None
    had_cache = _has_daily_reports(dest)

    try:
        manifest = _http_get_json(f"{base}/manifest.json")
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        if had_cache:
            err = f"远程同步失败，正在显示本地缓存：{exc}"
            _write_sync_meta(dest, status="stale", error=err, synced_at=prior_synced)
            return SyncResult(path=dest, status="stale", error=err, synced_at=prior_synced)
        err = f"远程同步失败，且没有可用缓存：{exc}"
        _write_sync_meta(dest, status="failed", error=err, synced_at=None)
        return SyncResult(path=dest, status="failed", error=err, synced_at=None)

    dates = manifest.get("dates") if isinstance(manifest, dict) else None
    if not isinstance(dates, list):
        if had_cache:
            err = "远程 manifest 无效，正在显示本地缓存"
            _write_sync_meta(dest, status="stale", error=err, synced_at=prior_synced)
            return SyncResult(path=dest, status="stale", error=err, synced_at=prior_synced)
        err = "远程 manifest 无效或缺少 dates"
        _write_sync_meta(dest, status="failed", error=err, synced_at=None)
        return SyncResult(path=dest, status="failed", error=err, synced_at=None)

    pulled = 0
    pull_errors: list[str] = []
    for day in dates:
        if not isinstance(day, str) or not day:
            continue
        day_dir = dest / day
        day_dir.mkdir(parents=True, exist_ok=True)
        try:
            payload = _http_get_json(f"{base}/{day}/daily_report.json")
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            pull_errors.append(f"{day}: {exc}")
            continue
        (day_dir / "daily_report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pulled += 1

    # Best-effort hourly heartbeat (optional; absent on older mirrors).
    try:
        hb = _http_get_json(f"{base}/{HEARTBEAT_NAME}")
        if isinstance(hb, dict):
            (dest / HEARTBEAT_NAME).write_text(
                json.dumps(hb, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        pass

    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if pulled == 0 and not _has_daily_reports(dest):
        err = "远程没有拉到任何日报" + (f"（{pull_errors[0]}）" if pull_errors else "")
        _write_sync_meta(dest, status="failed", error=err, synced_at=None)
        return SyncResult(path=dest, status="failed", error=err, synced_at=None)

    synced_at = _utc_now_iso()
    if pull_errors and pulled > 0:
        err = f"部分日期拉取失败（成功 {pulled} 天）：{pull_errors[0]}"
        _write_sync_meta(dest, status="stale", error=err, synced_at=synced_at)
        return SyncResult(path=dest, status="stale", error=err, synced_at=synced_at)

    _write_sync_meta(dest, status="ok", error=None, synced_at=synced_at)
    return SyncResult(path=dest, status="ok", error=None, synced_at=synced_at)


def _mtime_iso(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _local_reports_synced_at(reports_dir: Path) -> str | None:
    paths = list(reports_dir.glob("**/daily_report.json"))
    if not paths:
        return _mtime_iso(reports_dir) if reports_dir.exists() else None
    newest: str | None = None
    for p in paths:
        iso = _mtime_iso(p)
        if iso and (newest is None or iso > newest):
            newest = iso
    return newest


def resolve_reports(*, reports_dir: Path | None = None) -> SyncResult:
    """Resolve reports directory and attach sync freshness metadata."""
    if reports_dir is not None:
        rdir = Path(reports_dir)
        if not rdir.exists() or not _has_daily_reports(rdir):
            return SyncResult(
                path=rdir,
                status="empty",
                error="指定目录没有可读日报" if rdir.exists() else "指定目录不存在",
                synced_at=None,
            )
        return SyncResult(
            path=rdir,
            status="ok",
            error=None,
            synced_at=_local_reports_synced_at(rdir),
        )

    remote = os.environ.get("REPORTS_REMOTE_BASE", "").strip()
    if remote:
        cache = Path(tempfile.gettempdir()) / "futu_trader_live_reports"
        return sync_remote_reports(remote, cache)

    raw = os.environ.get("REPORTS_DIR", "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _repo_root() / path
        if not path.exists() or not _has_daily_reports(path):
            return SyncResult(
                path=path,
                status="empty",
                error="REPORTS_DIR 没有可读日报",
                synced_at=None,
            )
        return SyncResult(
            path=path,
            status="ok",
            error=None,
            synced_at=_local_reports_synced_at(path),
        )

    live = _repo_root() / "live_reports"
    if live.exists() and _has_daily_reports(live):
        return SyncResult(
            path=live,
            status="ok",
            error=None,
            synced_at=_local_reports_synced_at(live),
        )

    default = _repo_root() / "fixtures" / "staging" / "promotion_ok"
    if default.exists() and _has_daily_reports(default):
        return SyncResult(
            path=default,
            status="ok",
            error=None,
            synced_at=_local_reports_synced_at(default),
        )

    fallback = _repo_root() / "logs" / "staging"
    return SyncResult(
        path=fallback,
        status="empty",
        error="没有找到 live_reports 或演示 fixtures",
        synced_at=None,
    )


def resolve_reports_dir() -> Path:
    """Backward-compatible path-only resolver."""
    return resolve_reports().path


def human_lane_a(state: str | None) -> str:
    if not state:
        return "暂无数据"
    return LANE_A_LABELS.get(state, state)


def human_lane_b(state: str | None) -> str:
    if not state:
        return "暂无数据"
    return LANE_B_LABELS.get(state, state)


def human_side(side: str | None) -> str | None:
    if not side:
        return None
    key = str(side).upper()
    return SIDE_ZH.get(key, str(side))


def _normalize_position(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a position row for the dashboard (never raises)."""
    lane = str(row.get("lane") or "?").upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    side = row.get("side")
    side_s = str(side) if side not in (None, "") else None
    qty = _as_float(row.get("qty"))
    unrealized = _as_float(row.get("unrealized_pnl"))
    if unrealized is None:
        unrealized = _as_float(row.get("floating_pnl"))
    avg_price = _as_float(row.get("avg_price"))
    notional = _as_float(row.get("notional"))
    symbol = row.get("symbol") or row.get("prefer_symbol") or row.get("underlier")
    note = row.get("note") or row.get("detail")
    return {
        "lane": lane,
        "symbol": str(symbol) if symbol else None,
        "side": side_s,
        "side_zh": human_side(side_s),
        "qty": qty,
        "avg_price": avg_price,
        "notional": notional,
        "unrealized_pnl": unrealized,
        "note": str(note) if note else None,
    }


def _infer_positions_from_lanes(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort open positions when reports lack an explicit open_positions list."""
    rows: list[dict[str, Any]] = []
    lane_a = report.get("lane_a") if isinstance(report.get("lane_a"), dict) else {}
    lane_b = report.get("lane_b") if isinstance(report.get("lane_b"), dict) else {}

    nested_a = lane_a.get("position") if isinstance(lane_a.get("position"), dict) else None
    if nested_a and not _position_flat(nested_a):
        rows.append(
            _normalize_position(
                {
                    "lane": "A",
                    "symbol": nested_a.get("symbol") or lane_a.get("symbol"),
                    "side": nested_a.get("side"),
                    "qty": nested_a.get("qty"),
                    "avg_price": nested_a.get("avg_price"),
                    "notional": nested_a.get("notional"),
                    "unrealized_pnl": nested_a.get("unrealized_pnl"),
                }
            )
        )
    elif str(lane_a.get("state") or "") == "LOCKED":
        rows.append(
            _normalize_position(
                {
                    "lane": "A",
                    "symbol": lane_a.get("symbol"),
                    "side": lane_a.get("side") or "LONG",
                    "qty": lane_a.get("qty"),
                    "avg_price": lane_a.get("avg_price"),
                    "notional": lane_a.get("notional"),
                    "unrealized_pnl": lane_a.get("unrealized_pnl"),
                    "note": "日报未写明数量；状态为持仓中",
                }
            )
        )

    nested_b = lane_b.get("position") if isinstance(lane_b.get("position"), dict) else None
    if nested_b and not _position_flat(nested_b):
        rows.append(
            _normalize_position(
                {
                    "lane": "B",
                    "symbol": nested_b.get("symbol")
                    or lane_b.get("prefer_symbol")
                    or nested_b.get("underlier"),
                    "side": nested_b.get("side"),
                    "qty": nested_b.get("qty"),
                    "avg_price": nested_b.get("avg_price"),
                    "notional": nested_b.get("notional"),
                    "unrealized_pnl": nested_b.get("unrealized_pnl"),
                    "note": nested_b.get("note"),
                }
            )
        )
    elif str(lane_b.get("state") or "") == "DEPLOY":
        rows.append(
            _normalize_position(
                {
                    "lane": "B",
                    "symbol": lane_b.get("prefer_symbol"),
                    "side": lane_b.get("side"),
                    "qty": lane_b.get("qty"),
                    "notional": lane_b.get("notional") or lane_b.get("entry_notional"),
                    "unrealized_pnl": lane_b.get("unrealized_pnl"),
                    "note": "财报期权持仓中（腿明细见成交日志）",
                }
            )
        )
    return rows


def _position_flat(pos: dict[str, Any]) -> bool:
    qty = _as_float(pos.get("qty"))
    side = pos.get("side")
    if qty is None or qty == 0:
        return True
    if side in (None, "", "FLAT"):
        return True
    return False


def build_positions(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Open positions for the holdings panel — explicit list, else inferred."""
    if not report:
        return []
    raw = report.get("open_positions")
    if isinstance(raw, list):
        out = [_normalize_position(p) for p in raw if isinstance(p, dict)]
        # Drop empty / flat rows
        return [p for p in out if p.get("symbol") or (p.get("qty") or 0) > 0]
    return _infer_positions_from_lanes(report)


FILL_MODE_ZH: dict[str, str] = {
    "opend_sim": "真模拟",
    "mock_fill": "假成交",
}


def human_fill_mode(mode: str | None) -> str:
    if not mode:
        return "—"
    return FILL_MODE_ZH.get(str(mode), str(mode))


def _load_jsonl_fills(path: Path, *, default_lane: str) -> list[dict[str, Any]]:
    """Best-effort local fallback when daily_report lacks fills[]."""
    if not path.is_file():
        return []
    from staging.fills import normalize_fill_event

    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "fill":
            continue
        row = normalize_fill_event(event, default_lane=default_lane)
        if row:
            rows.append(row)
    return rows


def _normalize_fill_row(row: dict[str, Any]) -> dict[str, Any]:
    lane = str(row.get("lane") or "?").upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    side = row.get("side")
    side_s = str(side).upper() if side not in (None, "") else None
    mode = row.get("mode")
    mode_s = str(mode) if mode not in (None, "") else None
    return {
        "lane": lane,
        "ts": str(row.get("ts") or "") or None,
        "symbol": str(row["symbol"]) if row.get("symbol") else None,
        "side": side_s,
        "side_zh": human_side(side_s),
        "qty": _as_float(row.get("qty")),
        "price": _as_float(row.get("price")),
        "notional": _as_float(row.get("notional")),
        "mode": mode_s,
        "mode_zh": human_fill_mode(mode_s),
        "reason": str(row["reason"]) if row.get("reason") else None,
        "asset": str(row["asset"]) if row.get("asset") else None,
    }


def build_fills(
    report: dict[str, Any] | None,
    *,
    reports_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Per-trade rows for the ops board (embedded fills, else local jsonl)."""
    if not report:
        return []
    raw = report.get("fills")
    if isinstance(raw, list) and raw:
        out = [_normalize_fill_row(p) for p in raw if isinstance(p, dict) and p.get("symbol")]
        out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        return out

    # Local fallback: parse lane_*.jsonl when cloud sync only has counts.
    paths = report.get("artifact_paths") if isinstance(report.get("artifact_paths"), dict) else {}
    candidates: list[tuple[str, str]] = []
    session = str(report.get("session_date") or "")
    search_roots: list[Path] = []
    if reports_dir is not None:
        search_roots.append(Path(reports_dir))
        if session:
            search_roots.append(Path(reports_dir) / session)

    for lane_key, default_lane in (("lane_a_log", "A"), ("lane_b_log", "B")):
        rel = paths.get(lane_key) if isinstance(paths.get(lane_key), str) else ""
        found: Path | None = None
        if rel:
            # Try as-is relative to repo, then under reports_dir / session.
            for root in [_repo_root(), *search_roots]:
                cand = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
                if cand.is_file():
                    found = cand
                    break
        if found is None and session and reports_dir is not None:
            name = "lane_a.jsonl" if default_lane == "A" else "lane_b.jsonl"
            cand = Path(reports_dir) / session / name
            if cand.is_file():
                found = cand
        if found is not None:
            candidates.append((str(found), default_lane))

    rows: list[dict[str, Any]] = []
    for path_s, lane in candidates:
        rows.extend(_load_jsonl_fills(Path(path_s), default_lane=lane))
    rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return [_normalize_fill_row(r) for r in rows]


def build_activity_timeline(
    report: dict[str, Any] | None,
    *,
    fills: list[dict[str, Any]] | None = None,
    hourly_as_of: str | None = None,
    lane_a_status: str | None = None,
    lane_b_status: str | None = None,
) -> list[dict[str, Any]]:
    """Plain-language activity strip so a quiet board still shows the system moved."""
    items: list[dict[str, Any]] = []
    if not report and not hourly_as_of:
        return items

    if report:
        generated = str(report.get("generated_at") or "") or None
        session = str(report.get("session_date") or "") or None
        run_status = str(report.get("run_status") or "") or None
        summary = str(report.get("run_summary") or "") or None
        if generated or session:
            if run_status == "failed":
                text = summary or "日跑失败"
                kind = "warn"
            elif run_status == "degraded":
                text = summary or "日跑降级（非真模拟）"
                kind = "warn"
            else:
                text = "日跑完成"
                kind = "ok"
            items.append(
                {
                    "ts": generated or session,
                    "kind": kind,
                    "text": text,
                }
            )

        lane_b = report.get("lane_b") if isinstance(report.get("lane_b"), dict) else {}
        day_mode = str(lane_b.get("day_mode") or "").lower()
        scout_hits = int(lane_b.get("scout_hits") or 0)
        fills_b = int(lane_b.get("fills_count") or 0)
        sym = lane_b.get("prefer_symbol")
        if fills_b == 0:
            if day_mode == "idle_empty":
                scout_text = "B 今日无财报事件"
            elif day_mode in {"scout", "scout_only"} or scout_hits > 0:
                scout_text = (
                    f"B 今日仅侦察（{scout_hits} 次命中）" if scout_hits else "B 今日仅侦察无开仓"
                )
            else:
                scout_text = None
            if scout_text:
                if sym:
                    scout_text = f"{scout_text}（关注 {sym}）"
                items.append({"ts": generated or session, "kind": "info", "text": scout_text})

        if not (fills or []) and lane_a_status:
            items.append(
                {
                    "ts": generated or session,
                    "kind": "info",
                    "text": f"A：{lane_a_status}",
                }
            )
        if not (fills or []) and lane_b_status and fills_b > 0:
            items.append(
                {
                    "ts": generated or session,
                    "kind": "info",
                    "text": f"B：{lane_b_status}",
                }
            )

        for fill in (fills or [])[:5]:
            lane = fill.get("lane") or "?"
            side = fill.get("side_zh") or fill.get("side") or "成交"
            sym = fill.get("symbol") or "?"
            notional = fill.get("notional")
            qty = fill.get("qty")
            money = ""
            if notional is not None:
                money = f" · 约 ${float(notional):,.0f}"
            elif qty is not None and fill.get("price") is not None:
                money = f" · {float(qty):g}×${float(fill['price']):,.2f}"
            mode = fill.get("mode_zh") or ""
            mode_bit = f" · {mode}" if mode and mode != "—" else ""
            items.append(
                {
                    "ts": fill.get("ts") or generated or session,
                    "kind": "fill",
                    "text": f"{lane} {side} {sym}{money}{mode_bit}",
                }
            )

        for halt in report.get("halts") or []:
            if not isinstance(halt, dict):
                continue
            lane = halt.get("lane") or "?"
            reason = human_halt_reason(halt.get("reason"))
            items.append(
                {
                    "ts": generated or session,
                    "kind": "warn",
                    "text": f"{lane} 停手 — {reason}",
                }
            )

    if hourly_as_of:
        items.append(
            {
                "ts": hourly_as_of,
                "kind": "heartbeat",
                "text": f"小时心跳 {format_last_updated_zh(hourly_as_of)}",
            }
        )

    # Deduplicate identical text+ts while preserving order
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for it in items:
        key = (str(it.get("ts") or ""), str(it.get("text") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    unique.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return unique[:12]


def lane_a_status_zh(report: dict[str, Any] | None) -> str:
    """One plain-Chinese sentence for Lane A today."""
    if not report:
        return "暂无数据"
    lane = report.get("lane_a") if isinstance(report.get("lane_a"), dict) else {}
    state = str(lane.get("state") or "") or None
    label = human_lane_a(state)
    if state == "HALT":
        reason = human_halt_reason(lane.get("halt_reason"))
        return f"{label} — {reason}"
    if state == "LOCKED":
        sym = lane.get("symbol")
        return f"{label}" + (f"（{sym}）" if sym else "")
    if state == "HUNT":
        return label
    return label


def lane_b_status_zh(report: dict[str, Any] | None) -> str:
    """One plain-Chinese sentence for Lane B today (prefer day_mode when clearer)."""
    if not report:
        return "暂无数据"
    lane = report.get("lane_b") if isinstance(report.get("lane_b"), dict) else {}
    state = str(lane.get("state") or "") or None
    day_mode = lane.get("day_mode")
    day_key = str(day_mode).lower() if day_mode else ""
    if state == "HALT" or day_key == "halt":
        reason = human_halt_reason(lane.get("halt_reason"))
        return f"{human_lane_b('HALT')} — {reason}"
    if day_key in LANE_B_DAY_MODE_STATUS:
        status = LANE_B_DAY_MODE_STATUS[day_key]
        sym = lane.get("prefer_symbol")
        if day_key == "deploy" and sym:
            return f"{status}（关注 {sym}）"
        if day_key in {"scout", "scout_only"} and sym:
            return f"{status}（关注 {sym}）"
        return status
    return human_lane_b(state)


def human_halt_reason(reason: str | None) -> str:
    if not reason:
        return "未注明原因"
    key = str(reason)
    return HALT_REASON_ZH.get(key, key)


def human_excluded_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    key = str(reason)
    if key.startswith("load_error:"):
        return f"日报读取失败：{key.split(':', 1)[1]}"
    return EXCLUDED_REASON_ZH.get(key, key)


def load_latest_report(reports_dir: Path) -> dict[str, Any] | None:
    paths = sorted(reports_dir.glob("**/daily_report.json"))
    if not paths:
        flat = sorted(reports_dir.glob("*.json"))
        skip = {"sample_day_report.json", "manifest.json", SYNC_META_NAME}
        paths = [p for p in flat if p.name not in skip] or flat
    if not paths:
        return None
    latest = paths[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_report_rows(reports_dir: Path) -> list[dict[str, Any]]:
    """Load all daily reports sorted by session_date ascending."""
    rows: list[dict[str, Any]] = []
    for path in discover_reports(Path(reports_dir)):
        if path.name == SYNC_META_NAME:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if "session_date" not in data and path.parent.name:
            data = {**data, "session_date": path.parent.name}
        rows.append(data)
    rows.sort(key=lambda r: str(r.get("session_date") or ""))
    return rows


def extract_events(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Human-facing halt / error / disconnect stream for the latest session."""
    if not report:
        return []
    base_time = str(report.get("generated_at") or report.get("session_date") or "")
    events: list[dict[str, Any]] = []

    for halt in report.get("halts") or []:
        if not isinstance(halt, dict):
            continue
        events.append(
            {
                "time": base_time,
                "lane": str(halt.get("lane") or "?"),
                "reason_zh": human_halt_reason(halt.get("reason")),
                "severity": "critical",
            }
        )

    for err in report.get("errors") or []:
        if not isinstance(err, dict):
            continue
        where = str(err.get("where") or err.get("lane") or "system")
        msg = str(err.get("message") or err.get("detail") or "未知错误")
        events.append(
            {
                "time": base_time,
                "lane": where,
                "reason_zh": msg,
                "severity": "critical",
            }
        )

    for disc in report.get("disconnects") or []:
        if not isinstance(disc, dict):
            continue
        if not disc.get("detected"):
            continue
        detail = str(disc.get("detail") or "连接异常")
        events.append(
            {
                "time": base_time,
                "lane": str(disc.get("lane") or "feed"),
                "reason_zh": f"断连：{detail}",
                "severity": "warning",
            }
        )

    return events


def build_promotion_calendar(
    reports: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_CALENDAR_DAYS,
) -> list[dict[str, Any]]:
    """Recent N days: date, counts_for_promotion, excluded_reason_zh, pnl, halts."""
    calendar: list[dict[str, Any]] = []
    for report in reports[-limit:]:
        try:
            verdict: DayVerdict = evaluate_day(report)
            reason = verdict.reason
            counts = bool(verdict.counts)
            halt_count = int(verdict.halts)
            date = verdict.date
        except (KeyError, TypeError, ValueError):
            date = str(report.get("session_date") or "")
            counts = bool(report.get("counts_for_promotion"))
            reason = "" if counts else "unknown"
            halt_count = len(report.get("halts") or [])

        capital = report.get("capital") or {}
        pnl_a = _as_float(capital.get("lane_a_pnl")) or 0.0
        pnl_b = _as_float(capital.get("lane_b_pnl")) or 0.0
        calendar.append(
            {
                "date": date,
                "counts_for_promotion": counts,
                "excluded_reason_zh": None if counts else human_excluded_reason(reason),
                "pnl_total": round(pnl_a + pnl_b, 2),
                "halt_count": halt_count,
            }
        )
    return calendar


def build_pnl_points(
    reports: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_PNL_DAYS,
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for report in reports[-limit:]:
        capital = report.get("capital") or {}
        pnl_a = _as_float(capital.get("lane_a_pnl"))
        pnl_b = _as_float(capital.get("lane_b_pnl"))
        total = None
        if pnl_a is not None or pnl_b is not None:
            total = round((pnl_a or 0.0) + (pnl_b or 0.0), 2)
        points.append(
            {
                "date": str(report.get("session_date") or ""),
                "pnl_a": pnl_a,
                "pnl_b": pnl_b,
                "pnl_total": total,
            }
        )
    return points


def lane_a_day_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    lane = (report or {}).get("lane_a") or {}
    reason = lane.get("halt_reason")
    return {
        "symbol": lane.get("symbol"),
        "fills_count": int(lane.get("fills_count") or 0),
        "halt_reason": reason,
        "halt_reason_zh": human_halt_reason(reason) if reason else None,
        "state": lane.get("state"),
        "state_zh": human_lane_a(str(lane.get("state")) if lane.get("state") else None),
    }


def lane_b_day_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    lane = (report or {}).get("lane_b") or {}
    reason = lane.get("halt_reason")
    day_mode = lane.get("day_mode")
    day_mode_zh = {
        "scout": "扫描财报",
        "scout_only": "仅扫描财报",
        "deploy": "已部署",
        "idle": "空闲",
        "idle_empty": "空闲（无事件）",
        "cooldown": "冷却",
        "halt": "停手",
        "event_cycle": "事件循环",
    }.get(str(day_mode).lower() if day_mode else "", str(day_mode) if day_mode else None)
    return {
        "prefer_symbol": lane.get("prefer_symbol"),
        "day_mode": day_mode,
        "day_mode_zh": day_mode_zh,
        "fills_count": int(lane.get("fills_count") or 0),
        "halt_reason": reason,
        "halt_reason_zh": human_halt_reason(reason) if reason else None,
        "state": lane.get("state"),
        "state_zh": human_lane_b(str(lane.get("state")) if lane.get("state") else None),
    }


def probe_opend_reachable(host: str, port: int, *, timeout: float = 1.5) -> bool:
    """Read-only TCP probe — does not place orders or change trading_enabled."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def health_hint_zh(
    *,
    opend_reachable: bool,
    opend_mode: str | None,
    sync_status: str,
    run_status: str | None = None,
    hosting_mode: HostingMode = "local",
) -> str:
    if sync_status == "failed":
        return "日报同步失败，看板数据不可信，请检查 REPORTS_REMOTE_BASE / 网络。"
    if sync_status == "stale":
        return "日报可能过期（同步异常或用了缓存）。旧快照 ≠ OpenD 没开。"
    if sync_status == "empty":
        return "还没有日报。跑完模拟交易日后再看这里。"

    if hosting_mode == "cloud":
        if run_status == "failed":
            return (
                "最近无人值守日跑失败——问题在本机日跑，不在云看板。"
                "请查看日报 run_status / errors。"
            )
        if opend_mode == "opend_sim_fallback_mock" or run_status == "degraded":
            return (
                "该日报显示本机日跑当时连虚拟盘失败、已回退假成交——"
                "与云端能否连 OpenD 无关。"
            )
        if opend_mode == "opend":
            return "云端不直连本机 OpenD；当前快照来自已同步的本机日跑（当时模式正常）。"
        if opend_mode == "mock":
            return "云端不直连本机 OpenD；当前快照为 mock/演示类日报。"
        return (
            "云端不直连本机 OpenD；看板只读已同步快照。"
            "截至时间旧 = 快照旧，不是本机 OpenD 没开。"
        )

    if run_status == "failed":
        return "最近无人值守日跑失败——请查看日报 run_status / errors。"
    if opend_mode == "opend_sim_fallback_mock" or run_status == "degraded":
        return "最近一日连虚拟盘失败，已回退本地假成交——请检查本机 OpenD。"
    if opend_reachable:
        if opend_mode == "opend":
            return "本机 OpenD 可达，模式正常。"
        if opend_mode == "mock":
            return "本机 OpenD 端口可达，但最近日报标记为 mock（可能是强制 mock）。"
        return "本机 OpenD 端口可达。"
    if opend_mode == "mock":
        return "本机 OpenD 当前不可达；日报为 mock，属预期演示/离线模式。"
    return "本机 OpenD 不可达。若本机应在跑虚拟盘，请确认 OpenD 已启动。"


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_hourly_heartbeat(reports_dir: Path | None = None) -> dict[str, Any] | None:
    """Load ops hourly heartbeat from local, reports_dir, or live_reports copy."""
    override = os.environ.get("OPS_HEARTBEAT_PATH", "").strip()
    candidates: list[Path] = []
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = _repo_root() / path
        candidates.append(path if path.name == HEARTBEAT_NAME else path / HEARTBEAT_NAME)
    candidates.append(_repo_root() / "logs" / "ops_hourly" / HEARTBEAT_NAME)
    if reports_dir is not None:
        rdir = Path(reports_dir)
        candidates.extend([rdir / HEARTBEAT_NAME, rdir.parent / "ops_hourly" / HEARTBEAT_NAME])
    candidates.append(_repo_root() / "live_reports" / HEARTBEAT_NAME)

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def page_refresh_seconds() -> int:
    """Dashboard auto-refresh interval (seconds). Default 3600; override via env."""
    raw = os.environ.get("DASHBOARD_REFRESH_SECONDS", "").strip()
    if not raw:
        return DEFAULT_PAGE_REFRESH_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PAGE_REFRESH_SECONDS
    return max(30, value)


def format_last_updated_zh(iso_or_epoch: str | float | None) -> str:
    """Format a timestamp for the board in Beijing time (display only)."""
    if iso_or_epoch is None:
        return "—"
    if isinstance(iso_or_epoch, (int, float)):
        dt = datetime.fromtimestamp(float(iso_or_epoch), tz=timezone.utc)
    else:
        raw = str(iso_or_epoch).strip()
        # Date-only (session day) is a calendar label, not a clock — leave as-is.
        if len(raw) == 10 and raw[4:5] == "-" and raw[7:8] == "-":
            return raw
        dt = _parse_iso_dt(raw)
        if dt is None:
            return raw
    beijing = dt.astimezone(BEIJING_TZ)
    return beijing.strftime("%Y-%m-%d %H:%M:%S") + " 北京时间"


def format_clock_hhmm_zh(iso_or_epoch: str | float | None) -> str:
    """HH:MM in Beijing time for activity / caption clocks."""
    if iso_or_epoch is None:
        return ""
    if isinstance(iso_or_epoch, (int, float)):
        dt = datetime.fromtimestamp(float(iso_or_epoch), tz=timezone.utc)
    else:
        raw = str(iso_or_epoch).strip()
        if len(raw) == 10 and raw[4:5] == "-" and raw[7:8] == "-":
            return ""
        dt = _parse_iso_dt(raw)
        if dt is None:
            return ""
    return dt.astimezone(BEIJING_TZ).strftime("%H:%M")


def _maybe_mark_stale_by_hourly(
    status: SyncStatus,
    heartbeat: dict[str, Any] | None,
    *,
    data_mode: str,
) -> tuple[SyncStatus, str | None]:
    """If live data looks ok but hourly heartbeat is old, surface stale."""
    if status != "ok" or data_mode != "live_virtual" or not heartbeat:
        return status, None
    generated = _parse_iso_dt(str(heartbeat.get("generated_at") or "") or None)
    if generated is None:
        return status, None
    age_h = (datetime.now(timezone.utc) - generated).total_seconds() / 3600.0
    if age_h > HOURLY_STALE_AFTER_HOURS:
        return (
            "stale",
            f"小时心跳已超过 {HOURLY_STALE_AFTER_HOURS} 小时"
            f"（{format_last_updated_zh(heartbeat.get('generated_at'))}），"
            "快照可能不是最新实况",
        )
    return status, None


def _maybe_mark_stale_by_age(
    status: SyncStatus,
    report: dict[str, Any] | None,
    *,
    data_mode: str,
) -> tuple[SyncStatus, str | None]:
    """If live sync looks ok but the latest session is old, surface stale.

    Demo fixtures keep historical dates on purpose — do not mark them stale.
    """
    if status != "ok" or not report or data_mode != "live_virtual":
        return status, None
    session = str(report.get("session_date") or "")
    if not session:
        return status, None
    try:
        day = datetime.strptime(session, "%Y-%m-%d").date()
    except ValueError:
        return status, None
    age = (datetime.now(timezone.utc).date() - day).days
    if age > STALE_AFTER_DAYS:
        return "stale", f"最新日报日期 {session} 已超过 {STALE_AFTER_DAYS} 天，可能不是最新实况"
    return status, None


PremarketStatus = Literal[
    "ok",
    "empty_universe",
    "missing",
    "bad_schema",
    "waiting",
]


def _format_pct_metric(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}%"


def _format_rvol(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.2f}×"


def _name_to_premarket_row(name: Any) -> dict[str, Any]:
    """Serialize a LaneATechName (or duck-typed object) for the board."""
    reasons = list(getattr(name, "reasons", ()) or ())
    sources = list(getattr(name, "sources", ()) or ())
    reason = getattr(name, "reason", None)
    return {
        "symbol": str(getattr(name, "symbol", "") or ""),
        "score": float(getattr(name, "score", 0.0) or 0.0),
        "veto": bool(getattr(name, "veto", False)),
        "reasons": reasons,
        "sources": sources,
        "gap_pct": getattr(name, "gap_pct", None),
        "atr_pct": getattr(name, "atr_pct", None),
        "relative_volume": getattr(name, "relative_volume", None),
        "gap_pct_zh": _format_pct_metric(getattr(name, "gap_pct", None)),
        "atr_pct_zh": _format_pct_metric(getattr(name, "atr_pct", None)),
        "rvol_zh": _format_rvol(getattr(name, "relative_volume", None)),
        "last_price": getattr(name, "last_price", None),
        "regime_tag": getattr(name, "regime_tag", None),
        "reason": str(reason) if reason else None,
        "reason_text": (
            str(reason)
            if reason
            else ("; ".join(str(r) for r in reasons) if reasons else "—")
        ),
    }


def resolve_tech_watchlist_path(
    config: PortfolioConfig | None = None,
    *,
    reports_dir: Path | None = None,
) -> Path | None:
    """Resolve Lane A tech watchlist file (env → config → logs → live_reports)."""
    root = _repo_root()
    candidates: list[Path] = []

    for env_name in ("TECH_WATCHLIST_PATH", "LANE_A_TECH_WATCHLIST_PATH"):
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        candidates.append(path)

    if config is not None and config.lane_a.tech_watchlist_path:
        path = Path(config.lane_a.tech_watchlist_path)
        if not path.is_absolute():
            path = root / path
        candidates.append(path)

    candidates.append(root / "logs" / "lane_a" / "tech_watchlist.json")
    candidates.append(root / "live_reports" / "lane_a" / "tech_watchlist.json")
    if reports_dir is not None:
        candidates.append(Path(reports_dir) / "lane_a" / "tech_watchlist.json")
        candidates.append(Path(reports_dir).parent / "lane_a" / "tech_watchlist.json")
    # Labeled demo fixture last — never silent live; UI marks data_label=demo_fixtures.
    candidates.append(root / "fixtures" / "lane_a" / "valid_lane_a_tech_watchlist.json")

    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None


def empty_premarket_decision(
    *,
    status: PremarketStatus,
    headline_zh: str,
    detail_zh: str,
    no_trade_reason_zh: str | None = None,
    source_path: str | None = None,
    data_label: str = "missing",
) -> dict[str, Any]:
    return {
        "status": status,
        "as_of": None,
        "generated_at_zh": None,
        "schema_version": None,
        "generated_by": None,
        "universe_note": None,
        "timezone": None,
        "experiment_id": None,
        "source_path": source_path,
        "data_label": data_label,  # live | demo_fixtures | missing
        "deployable": [],
        "vetoed": [],
        "no_trade": True,
        "no_trade_reason_zh": no_trade_reason_zh or detail_zh,
        "headline_zh": headline_zh,
        "detail_zh": detail_zh,
        "footnote_zh": (
            "名单 ≠ 下单指令。入场仍由 ORB+VWAP 决定；真下单默认关闭；"
            "本页信息不构成投资建议（NFA）。"
        ),
    }


def build_premarket_decision(
    config: PortfolioConfig,
    *,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    """Board-facing Lane A premarket decision panel (lane_a_tech_1.0)."""
    from data.lane_a_tech_watchlist import (
        LaneATechWatchlistError,
        load_lane_a_tech_watchlist,
    )

    path = resolve_tech_watchlist_path(config, reports_dir=reports_dir)
    if path is None:
        return empty_premarket_decision(
            status="waiting",
            headline_zh="今日盘前决策：等待同步",
            detail_zh=(
                "还没有可读的 Lane A 技术面名单（logs/lane_a/tech_watchlist.json）。"
                "盘前 Autopilot / Analyst 产出后，或 Engineer 同步到云端后，这里会自动出现。"
            ),
            no_trade_reason_zh="缺文件 — 今日 Lane A 应按 no_trade_day 处理（不会静默假装有名单）",
            data_label="missing",
        )

    path_s = str(path).replace("\\", "/")
    data_label = "demo_fixtures" if "fixtures" in path_s else "live"
    generated_at_zh = format_last_updated_zh(_mtime_iso(path))

    try:
        wl = load_lane_a_tech_watchlist(path)
    except LaneATechWatchlistError as exc:
        return empty_premarket_decision(
            status="bad_schema",
            headline_zh="今日盘前决策：名单校验失败",
            detail_zh=f"读到了文件，但 schema / 内容不合格：{exc}",
            no_trade_reason_zh=f"坏 schema — 今日 Lane A 不交易（no_trade_day）：{exc}",
            source_path=path_s,
            data_label=data_label,
        )
    except (OSError, ValueError) as exc:
        return empty_premarket_decision(
            status="bad_schema",
            headline_zh="今日盘前决策：名单无法读取",
            detail_zh=f"文件存在但读失败：{exc}",
            no_trade_reason_zh=f"读失败 — 今日 Lane A 不交易（no_trade_day）：{exc}",
            source_path=path_s,
            data_label=data_label,
        )

    deployable = [_name_to_premarket_row(n) for n in wl.deployable()]
    vetoed = [_name_to_premarket_row(n) for n in wl.vetoed()]
    deployable.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    as_of = wl.as_of.isoformat()

    base = {
        "as_of": as_of,
        "generated_at_zh": generated_at_zh,
        "schema_version": wl.schema_version,
        "generated_by": wl.generated_by,
        "universe_note": wl.universe_note,
        "timezone": wl.timezone,
        "experiment_id": None,
        "source_path": path_s,
        "data_label": data_label,
        "deployable": deployable,
        "vetoed": vetoed,
        "footnote_zh": (
            "名单 ≠ 下单指令。入场仍由 ORB+VWAP 决定；真下单默认关闭；"
            "本页信息不构成投资建议（NFA）。"
        ),
    }
    # Optional experiment id if present on disk payload (not in dataclass).
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("experiment_id"):
            base["experiment_id"] = str(raw["experiment_id"])
    except (OSError, json.JSONDecodeError):
        pass

    if not deployable:
        note = wl.universe_note or "可交易名单为空"
        return {
            **base,
            "status": "empty_universe",
            "no_trade": True,
            "no_trade_reason_zh": f"今日 Lane A 不交易（no_trade_day）：{note}",
            "headline_zh": f"今日盘前决策：空日 · {as_of}（美东）",
            "detail_zh": note,
        }

    symbols = "、".join(r["symbol"] for r in deployable)
    return {
        **base,
        "status": "ok",
        "no_trade": False,
        "no_trade_reason_zh": None,
        "headline_zh": f"今日可交易：{symbols}",
        "detail_zh": (
            f"美东交易日 {as_of} · 选入 {len(deployable)} 只"
            f"（否决 {len(vetoed)} 只）· 规则 {wl.schema_version}"
        ),
    }


@dataclass
class OpsSnapshot:
    trading_enabled: bool
    futu_env: str
    experiment_id: str
    total_capital: float
    required_n: int
    counting_streak: int
    promotion_verdict: str
    lane_a_state: str
    lane_b_state: str
    lane_a_label: str
    lane_b_label: str
    last_session_date: str | None
    last_pnl_a: float | None
    last_pnl_b: float | None
    last_halts: int
    alert: str
    reports_dir: str
    data_mode: str  # demo_fixtures | live_virtual | empty
    # P0 freshness
    data_as_of: str | None = None
    sync_status: SyncStatus = "empty"
    sync_error: str | None = None
    synced_at: str | None = None
    # P0 / P1 enrichments
    events: list[dict[str, Any]] = field(default_factory=list)
    promotion_calendar: list[dict[str, Any]] = field(default_factory=list)
    pnl_points: list[dict[str, Any]] = field(default_factory=list)
    lane_a_summary: dict[str, Any] = field(default_factory=dict)
    lane_b_summary: dict[str, Any] = field(default_factory=dict)
    opend_mode: str | None = None
    opend_reachable: bool = False
    health_hint_zh: str = ""
    hosting_mode: HostingMode = "local"
    connection_label_zh: str = ""
    data_path_zh: str = ""
    positions: list[dict[str, Any]] = field(default_factory=list)
    lane_a_status_zh: str = "暂无数据"
    lane_b_status_zh: str = "暂无数据"
    # Hourly OpenD heartbeat (optional)
    hourly_as_of: str | None = None
    hourly_opend_reachable: bool | None = None
    hourly_quote_source: str | None = None
    # PROH-90: trade detail + activity sense
    fills: list[dict[str, Any]] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)
    # PROH-107: Lane A premarket decision transparency
    premarket: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_ops_snapshot(
    config_path: str | Path | None = None,
    *,
    reports_dir: Path | None = None,
    probe_opend: bool = True,
) -> OpsSnapshot:
    root = _repo_root()
    cfg_path = Path(config_path) if config_path else root / "config" / "portfolio.yaml"
    config: PortfolioConfig = load_portfolio_config(cfg_path)
    sync = resolve_reports(reports_dir=reports_dir)
    rdir = sync.path
    promo = check_promotion(rdir, n=int(config.promotion.n_days))
    report = load_latest_report(rdir)
    all_reports = load_report_rows(rdir)
    hosting_mode = detect_hosting_mode()

    path_s = str(rdir).replace("\\", "/")
    demo = "fixtures" in path_s
    emptyish = sync.status == "empty" or not rdir.exists()
    emptyish = emptyish or (promo.counting_streak == 0 and report is None)
    if emptyish:
        data_mode = "empty"
    elif demo:
        data_mode = "demo_fixtures"
    else:
        data_mode = "live_virtual"

    sync_status = sync.status
    sync_error = sync.error
    age_status, age_error = _maybe_mark_stale_by_age(
        sync_status, report, data_mode=data_mode
    )
    if age_status == "stale" and sync_status == "ok":
        sync_status = "stale"
        sync_error = age_error

    heartbeat = load_hourly_heartbeat(rdir)
    hourly_as_of = None
    hourly_opend_reachable: bool | None = None
    hourly_quote_source: str | None = None
    if heartbeat:
        hourly_as_of = str(heartbeat.get("generated_at") or "") or None
        opend_block = heartbeat.get("opend") if isinstance(heartbeat.get("opend"), dict) else {}
        if "reachable" in opend_block:
            hourly_opend_reachable = bool(opend_block.get("reachable"))
        quote_block = heartbeat.get("quote") if isinstance(heartbeat.get("quote"), dict) else {}
        if quote_block.get("source"):
            hourly_quote_source = str(quote_block.get("source"))

    hb_status, hb_error = _maybe_mark_stale_by_hourly(
        sync_status, heartbeat, data_mode=data_mode
    )
    if hb_status == "stale" and sync_status == "ok":
        sync_status = "stale"
        sync_error = hb_error

    lane_a = (report or {}).get("lane_a") or {}
    lane_b = (report or {}).get("lane_b") or {}
    capital = (report or {}).get("capital") or {}
    if report:
        lane_a_state = str(lane_a.get("state") or "HUNT")
        lane_b_state = str(lane_b.get("state") or "IDLE")
    else:
        lane_a_state = "—"
        lane_b_state = "—"
    halts = len((report or {}).get("halts") or [])
    opend_mode = str((report or {}).get("opend_mode") or "") or None

    generated_at = (report or {}).get("generated_at")
    session_date = (report or {}).get("session_date")
    data_as_of = str(generated_at or session_date) if (generated_at or session_date) else None

    # Cloud cannot reach laptop OpenD — skip probe to avoid false "unreachable".
    opend_reachable = False
    if should_probe_local_opend(hosting_mode=hosting_mode, probe_opend=probe_opend):
        opend_reachable = probe_opend_reachable(config.futu.host, int(config.futu.port))

    hint = health_hint_zh(
        opend_reachable=opend_reachable,
        opend_mode=opend_mode,
        sync_status=sync_status,
        run_status=str((report or {}).get("run_status") or "") or None,
        hosting_mode=hosting_mode,
    )
    conn_label = connection_label_zh(
        hosting_mode=hosting_mode, opend_reachable=opend_reachable
    )
    path_caption = data_path_caption_zh(hosting_mode)
    positions = build_positions(report)
    fills = build_fills(report, reports_dir=rdir)
    a_status = lane_a_status_zh(report)
    b_status = lane_b_status_zh(report)
    a_label = a_status if report else "暂无数据"
    b_label = b_status if report else "暂无数据"
    activity = build_activity_timeline(
        report,
        fills=fills,
        hourly_as_of=hourly_as_of,
        lane_a_status=a_status if report else None,
        lane_b_status=b_status if report else None,
    )
    premarket = build_premarket_decision(config, reports_dir=rdir)

    if config.trading_enabled:
        alert = "真下单总开关是开着的——请确认这是有意为之。"
    elif sync_status == "failed":
        alert = sync_error or "日报同步失败。"
    elif sync_status == "stale":
        alert = sync_error or "日报可能过期，请勿当成最新实况。"
    elif data_mode == "empty":
        alert = "还没有日报数据。系统在等模拟交易日产生报告。"
    elif data_mode == "demo_fixtures":
        alert = "当前仍是演示数据。本机同步虚拟盘日报后，这里会换成实况。"
    elif str((report or {}).get("run_status") or "") == "failed":
        alert = str(
            (report or {}).get("run_summary")
            or (
                "最近无人值守日跑失败——问题在本机日跑，不在云看板。"
                if hosting_mode == "cloud"
                else "最近无人值守日跑失败——请查看日报 errors / run_status。"
            )
        )
    elif opend_mode == "opend_sim_fallback_mock":
        if hosting_mode == "cloud":
            alert = (
                "该日报显示本机日跑当时连虚拟盘失败、回退成了假成交——"
                "与云端能否连 OpenD 无关。"
            )
        else:
            alert = "最近一日连虚拟盘失败，回退成了本地假成交——请检查本机 OpenD 是否在线。"
    elif str((report or {}).get("run_status") or "") == "degraded":
        alert = str(
            (report or {}).get("run_summary")
            or (
                "最近无人值守日跑降级（非真模拟）——问题在本机日跑，不在云看板。"
                if hosting_mode == "cloud"
                else "最近无人值守日跑降级（非真模拟）——请检查本机 OpenD。"
            )
        )
    elif promo.verdict != "PASS":
        alert = (
            f"虚拟盘练兵进度 {promo.counting_streak}/{promo.required_n}，"
            "还没达到可讨论真钱交易的门槛。"
        )
    elif halts:
        alert = f"最近一天有 {halts} 次停手记录，请留意。"
    else:
        alert = (
            "云端只读快照正常。真下单仍关闭。"
            if hosting_mode == "cloud"
            else "虚拟盘实况正常。真下单仍关闭。"
        )

    return OpsSnapshot(
        trading_enabled=bool(config.trading_enabled),
        futu_env=str(config.futu.env),
        experiment_id=str(config.experiment_id),
        total_capital=float(config.total_capital),
        required_n=int(promo.required_n),
        counting_streak=int(promo.counting_streak),
        promotion_verdict=str(promo.verdict),
        lane_a_state=lane_a_state,
        lane_b_state=lane_b_state,
        lane_a_label=a_label,
        lane_b_label=b_label,
        last_session_date=session_date,
        last_pnl_a=_as_float(capital.get("lane_a_pnl")),
        last_pnl_b=_as_float(capital.get("lane_b_pnl")),
        last_halts=halts,
        alert=alert,
        reports_dir=str(rdir),
        data_mode=data_mode,
        data_as_of=data_as_of,
        sync_status=sync_status,
        sync_error=sync_error,
        synced_at=sync.synced_at,
        events=extract_events(report),
        promotion_calendar=build_promotion_calendar(all_reports),
        pnl_points=build_pnl_points(all_reports),
        lane_a_summary=lane_a_day_summary(report),
        lane_b_summary=lane_b_day_summary(report),
        opend_mode=opend_mode,
        opend_reachable=opend_reachable,
        health_hint_zh=hint,
        hosting_mode=hosting_mode,
        connection_label_zh=conn_label,
        data_path_zh=path_caption,
        positions=positions,
        lane_a_status_zh=a_status,
        lane_b_status_zh=b_status,
        hourly_as_of=hourly_as_of,
        hourly_opend_reachable=hourly_opend_reachable,
        hourly_quote_source=hourly_quote_source,
        fills=fills,
        activity=activity,
        premarket=premarket,
    )


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def expected_dashboard_password() -> str | None:
    """Password from env; None means gate disabled (local default)."""
    raw = os.environ.get("DASHBOARD_PASSWORD")
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None
