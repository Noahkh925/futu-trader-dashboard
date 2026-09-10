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

FILL_MODE_ZH: dict[str, str] = {
    "mock_fill": "假成交",
    "opend_sim": "真模拟",
    "opend": "真模拟",
}

FILL_SIDE_ZH: dict[str, str] = {
    "BUY": "买入",
    "SELL": "卖出",
    "LONG": "买入",
    "SHORT": "卖出",
}

DEFAULT_FILL_HISTORY_DAYS = 5

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


def _http_get_text(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "futu-trader-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


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
        # Best-effort lane event logs (fills live here when daily_report lacks fills[]).
        for log_name in ("lane_a.jsonl", "lane_b.jsonl"):
            try:
                text = _http_get_text(f"{base}/{day}/{log_name}")
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
            ):
                continue
            if text.strip():
                (day_dir / log_name).write_text(text, encoding="utf-8")

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
        "estimated": bool(row.get("estimated")),
    }


def _position_flat(pos: dict[str, Any]) -> bool:
    qty = _as_float(pos.get("qty"))
    side = pos.get("side")
    if qty is None or qty == 0:
        return True
    if side in (None, "", "FLAT"):
        return True
    return False


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
                    "note": "财报期权持仓中（腿明细见成交明细）",
                }
            )
        )
    return rows


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


def human_fill_mode(mode: str | None) -> str:
    if not mode:
        return "未知模式"
    return FILL_MODE_ZH.get(str(mode), str(mode))


def human_fill_side(side: str | None) -> str | None:
    if not side:
        return None
    key = str(side).upper()
    return FILL_SIDE_ZH.get(key, human_side(side) or str(side))


def _short_clock_zh(ts: str | None) -> str:
    """Extract HH:MM from ISO timestamp for activity / fills."""
    if not ts:
        return ""
    text = str(ts).strip()
    if "T" in text:
        clock = text.split("T", 1)[1]
        return clock[:5] if len(clock) >= 5 else clock
    if len(text) >= 16 and text[10] == " ":
        return text[11:16]
    return text[-5:] if len(text) >= 5 else text


def _normalize_fill(
    row: dict[str, Any],
    *,
    default_lane: str | None = None,
    session_date: str | None = None,
) -> dict[str, Any] | None:
    """Normalize one fill for the board. Returns None if not fill-like."""
    event_type = str(row.get("type") or row.get("event") or "fill").lower()
    if event_type and event_type not in {"fill", "fills"}:
        return None
    lane_raw = row.get("lane") or default_lane or "?"
    lane = str(lane_raw).upper()
    if lane in {"LANE_A", "A"}:
        lane = "A"
    elif lane in {"LANE_B", "B"}:
        lane = "B"
    side_raw = row.get("side")
    side_s = str(side_raw).upper() if side_raw not in (None, "") else None
    qty = _as_float(row.get("qty"))
    price = _as_float(row.get("price") or row.get("avg_price") or row.get("fill_price"))
    notional = _as_float(row.get("notional"))
    if notional is None and qty is not None and price is not None:
        notional = round(qty * price, 2)
        estimated_notional = True
    else:
        estimated_notional = bool(row.get("estimated"))
    symbol = row.get("symbol") or row.get("prefer_symbol") or row.get("underlier")
    mode = row.get("mode") or row.get("paper_mode") or row.get("fill_mode")
    ts = row.get("ts") or row.get("time") or row.get("clock") or row.get("filled_at")
    day = row.get("session_date") or session_date
    if not symbol and qty is None and price is None:
        return None
    return {
        "lane": lane,
        "session_date": str(day) if day else None,
        "time": str(ts) if ts else None,
        "time_zh": _short_clock_zh(str(ts) if ts else None),
        "symbol": str(symbol) if symbol else None,
        "side": side_s,
        "side_zh": human_fill_side(side_s),
        "qty": qty,
        "price": price,
        "notional": notional,
        "mode": str(mode) if mode else None,
        "mode_zh": human_fill_mode(str(mode) if mode else None),
        "estimated": estimated_notional,
    }


def _read_jsonl_fills(
    path: Path,
    *,
    lane: str,
    session_date: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("type") or "").lower() != "fill":
            continue
        norm = _normalize_fill(payload, default_lane=lane, session_date=session_date)
        if norm:
            rows.append(norm)
    return rows


def _resolve_lane_log_path(
    reports_dir: Path,
    report: dict[str, Any],
    *,
    artifact_key: str,
    filename: str,
) -> Path | None:
    session = str(report.get("session_date") or "")
    candidates: list[Path] = []
    if session:
        candidates.append(Path(reports_dir) / session / filename)
    candidates.append(Path(reports_dir) / filename)
    art = (report.get("artifact_paths") or {}).get(artifact_key)
    if isinstance(art, str) and art.strip():
        p = Path(art)
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(_repo_root() / p)
            candidates.append(Path(reports_dir) / p)
            candidates.append(Path(reports_dir) / p.name)
            if session:
                candidates.append(Path(reports_dir) / session / p.name)
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.is_file():
            return cand
    return None


def build_fills(
    report: dict[str, Any] | None,
    reports_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Fill rows for one session — prefer embedded ``fills[]``, else lane_*.jsonl."""
    if not report:
        return []
    session = str(report.get("session_date") or "") or None
    embedded = report.get("fills")
    if isinstance(embedded, list) and embedded:
        out: list[dict[str, Any]] = []
        for row in embedded:
            if not isinstance(row, dict):
                continue
            # Embedded rows may omit type=fill
            payload = dict(row)
            payload.setdefault("type", "fill")
            norm = _normalize_fill(payload, session_date=session)
            if norm:
                out.append(norm)
        if out:
            return out

    if reports_dir is None:
        return []
    rdir = Path(reports_dir)
    rows: list[dict[str, Any]] = []
    path_a = _resolve_lane_log_path(
        rdir, report, artifact_key="lane_a_log", filename="lane_a.jsonl"
    )
    path_b = _resolve_lane_log_path(
        rdir, report, artifact_key="lane_b_log", filename="lane_b.jsonl"
    )
    if path_a:
        rows.extend(_read_jsonl_fills(path_a, lane="A", session_date=session))
    if path_b:
        rows.extend(_read_jsonl_fills(path_b, lane="B", session_date=session))
    return rows


def build_fills_history(
    reports: list[dict[str, Any]],
    reports_dir: Path | None = None,
    *,
    limit_days: int = DEFAULT_FILL_HISTORY_DAYS,
) -> list[dict[str, Any]]:
    """Recent N sessions of fills, newest session last (stable for UI slicing)."""
    out: list[dict[str, Any]] = []
    for report in reports[-limit_days:]:
        out.extend(build_fills(report, reports_dir))
    return out


def enrich_positions_amounts(
    positions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fill missing qty / avg / notional from same-lane buy fills; mark 估算."""
    if not positions:
        return positions
    enriched: list[dict[str, Any]] = []
    for pos in positions:
        row = dict(pos)
        lane = row.get("lane")
        symbol = row.get("symbol")
        needs_qty = row.get("qty") is None
        needs_px = row.get("avg_price") is None
        needs_notional = row.get("notional") is None
        if not (needs_qty or needs_px or needs_notional):
            enriched.append(row)
            continue
        buys = [
            f
            for f in fills
            if f.get("lane") == lane
            and (not symbol or f.get("symbol") == symbol)
            and str(f.get("side") or "").upper() in {"BUY", "LONG"}
        ]
        if not buys:
            # Any fill for lane if symbol missing on fills
            buys = [
                f
                for f in fills
                if f.get("lane") == lane
                and str(f.get("side") or "").upper() in {"BUY", "LONG"}
            ]
        if not buys:
            enriched.append(row)
            continue
        qty_sum = sum(float(f["qty"]) for f in buys if f.get("qty") is not None)
        notional_sum = sum(
            float(f["notional"]) for f in buys if f.get("notional") is not None
        )
        if needs_qty and qty_sum > 0:
            row["qty"] = qty_sum
            row["estimated"] = True
        if needs_notional and notional_sum > 0:
            row["notional"] = round(notional_sum, 2)
            row["estimated"] = True
        if needs_px and notional_sum > 0 and qty_sum > 0:
            row["avg_price"] = round(notional_sum / qty_sum, 4)
            row["estimated"] = True
        if row.get("estimated"):
            note = str(row.get("note") or "").strip()
            if "估算" not in note:
                row["note"] = (note + " · 估算" if note else "数量/金额为估算").strip(" ·")
        enriched.append(row)
    return enriched


def build_activity_timeline(
    report: dict[str, Any] | None,
    fills: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Plain-Chinese activity lines so a quiet day still shows the system worked."""
    if not report:
        return []
    items: list[dict[str, Any]] = []
    session = str(report.get("session_date") or "")
    generated = str(report.get("generated_at") or "")
    clock = _short_clock_zh(generated) or "—"

    run_status = str(report.get("run_status") or "")
    run_summary = str(report.get("run_summary") or "").strip()
    if run_status == "failed":
        items.append(
            {
                "time": clock,
                "text": run_summary or "日跑失败——请看异常说明",
                "kind": "halt",
            }
        )
    elif run_status == "degraded":
        items.append(
            {
                "time": clock,
                "text": run_summary or "日跑降级（非完整真模拟）",
                "kind": "watch",
            }
        )
    elif generated or session:
        items.append(
            {
                "time": clock,
                "text": f"日跑完成（{session or '当日'}）",
                "kind": "run",
            }
        )

    lane_a = report.get("lane_a") if isinstance(report.get("lane_a"), dict) else {}
    lane_b = report.get("lane_b") if isinstance(report.get("lane_b"), dict) else {}

    day_fills = [
        f
        for f in fills
        if not session or not f.get("session_date") or f.get("session_date") == session
    ]
    # Newest fills first for the activity strip
    for fill in reversed(day_fills[-6:]):
        lane = fill.get("lane") or "?"
        side = fill.get("side_zh") or fill.get("side") or "成交"
        sym = fill.get("symbol") or "未标明"
        money = fill.get("notional")
        money_s = f"约 ${money:,.0f}" if isinstance(money, (int, float)) else ""
        t = fill.get("time_zh") or clock
        bits = [f"路线 {lane} {side} {sym}"]
        if money_s:
            bits.append(money_s)
        mode = fill.get("mode_zh")
        if mode:
            bits.append(str(mode))
        items.append({"time": t, "text": " · ".join(bits), "kind": "fill"})

    if not any(f.get("lane") == "A" for f in day_fills):
        a_state = str(lane_a.get("state") or "")
        if a_state == "HUNT":
            items.append(
                {
                    "time": clock,
                    "text": "路线 A：今日在找机会，尚未成交",
                    "kind": "info",
                }
            )
        elif a_state == "LOCKED":
            sym = lane_a.get("symbol")
            items.append(
                {
                    "time": clock,
                    "text": "路线 A：持仓中" + (f"（{sym}）" if sym else ""),
                    "kind": "info",
                }
            )
        elif a_state == "HALT":
            items.append(
                {
                    "time": clock,
                    "text": f"路线 A：已停手 — {human_halt_reason(lane_a.get('halt_reason'))}",
                    "kind": "halt",
                }
            )

    if not any(f.get("lane") == "B" for f in day_fills):
        day_mode = str(lane_b.get("day_mode") or "").lower()
        if day_mode in {"scout", "scout_only"}:
            hits = int(lane_b.get("scout_hits") or 0)
            sym = lane_b.get("prefer_symbol")
            extra = f" · 关注 {sym}" if sym else ""
            items.append(
                {
                    "time": clock,
                    "text": f"路线 B：今日仅侦察无成交（扫描 {hits} 次）{extra}",
                    "kind": "scout",
                }
            )
        elif day_mode in {"idle_empty", "idle"}:
            items.append(
                {
                    "time": clock,
                    "text": "路线 B：今日无财报事件，待命",
                    "kind": "info",
                }
            )
        elif day_mode == "deploy" or str(lane_b.get("state") or "") == "DEPLOY":
            sym = lane_b.get("prefer_symbol")
            items.append(
                {
                    "time": clock,
                    "text": "路线 B：持仓中" + (f"（关注 {sym}）" if sym else ""),
                    "kind": "info",
                }
            )
        elif day_mode == "halt" or str(lane_b.get("state") or "") == "HALT":
            items.append(
                {
                    "time": clock,
                    "text": f"路线 B：已停手 — {human_halt_reason(lane_b.get('halt_reason'))}",
                    "kind": "halt",
                }
            )

    for halt in report.get("halts") or []:
        if not isinstance(halt, dict):
            continue
        items.append(
            {
                "time": clock,
                "text": (
                    f"停手 · 路线 {halt.get('lane') or '?'} — "
                    f"{human_halt_reason(halt.get('reason'))}"
                ),
                "kind": "halt",
            }
        )

    # De-dupe consecutive identical texts
    deduped: list[dict[str, Any]] = []
    for item in items:
        if deduped and deduped[-1].get("text") == item.get("text"):
            continue
        deduped.append(item)
    return deduped[:limit]


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
    if iso_or_epoch is None:
        return "—"
    if isinstance(iso_or_epoch, (int, float)):
        dt = datetime.fromtimestamp(float(iso_or_epoch), tz=timezone.utc)
    else:
        dt = _parse_iso_dt(str(iso_or_epoch))
        if dt is None:
            return str(iso_or_epoch)
    local = dt.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


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
            f"小时心跳已超过 {HOURLY_STALE_AFTER_HOURS} 小时（{heartbeat.get('generated_at')}），"
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
    # PROH-90: fills + activity
    fills: list[dict[str, Any]] = field(default_factory=list)
    fills_history: list[dict[str, Any]] = field(default_factory=list)
    activity_timeline: list[dict[str, Any]] = field(default_factory=list)
    last_updated_zh: str = "—"

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
    fills_history = build_fills_history(all_reports, rdir)
    session = str((report or {}).get("session_date") or "") or None
    fills = [
        f
        for f in fills_history
        if not session or f.get("session_date") == session or not f.get("session_date")
    ]
    if not fills and report:
        fills = build_fills(report, rdir)
    positions = enrich_positions_amounts(build_positions(report), fills)
    activity = build_activity_timeline(report, fills)
    last_updated_zh = format_last_updated_zh(
        sync.synced_at or data_as_of or hourly_as_of
    )
    a_status = lane_a_status_zh(report)
    b_status = lane_b_status_zh(report)
    a_label = a_status if report else "暂无数据"
    b_label = b_status if report else "暂无数据"

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
        fills_history=fills_history,
        activity_timeline=activity,
        last_updated_zh=last_updated_zh,
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
