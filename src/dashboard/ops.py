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
DEFAULT_CALENDAR_DAYS = 20
DEFAULT_PNL_DAYS = 20
STALE_AFTER_DAYS = 3


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
) -> str:
    if sync_status == "failed":
        return "日报同步失败，看板数据不可信，请检查 REPORTS_REMOTE_BASE / 网络。"
    if sync_status == "stale":
        return "日报可能过期（同步异常或用了缓存），请勿当成最新实况。"
    if sync_status == "empty":
        return "还没有日报。跑完模拟交易日后再看这里。"
    if opend_mode == "opend_sim_fallback_mock":
        return "最近一日连虚拟盘失败，已回退本地假成交——请检查 OpenD。"
    if opend_reachable:
        if opend_mode == "opend":
            return "OpenD 可达，模式正常。"
        if opend_mode == "mock":
            return "OpenD 端口可达，但最近日报标记为 mock（可能是强制 mock）。"
        return "OpenD 端口可达。"
    if opend_mode == "mock":
        return "OpenD 当前不可达；日报为 mock，属预期演示/离线模式。"
    return "OpenD 不可达。若本机应在跑虚拟盘，请确认 OpenD 已启动。"


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

    lane_a = (report or {}).get("lane_a") or {}
    lane_b = (report or {}).get("lane_b") or {}
    capital = (report or {}).get("capital") or {}
    lane_a_state = str(lane_a.get("state") or "HUNT")
    lane_b_state = str(lane_b.get("state") or "IDLE")
    halts = len((report or {}).get("halts") or [])
    opend_mode = str((report or {}).get("opend_mode") or "") or None

    generated_at = (report or {}).get("generated_at")
    session_date = (report or {}).get("session_date")
    data_as_of = str(generated_at or session_date) if (generated_at or session_date) else None

    opend_reachable = False
    if probe_opend:
        opend_reachable = probe_opend_reachable(config.futu.host, int(config.futu.port))

    hint = health_hint_zh(
        opend_reachable=opend_reachable,
        opend_mode=opend_mode,
        sync_status=sync_status,
    )

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
    elif opend_mode == "opend_sim_fallback_mock":
        alert = "最近一日连虚拟盘失败，回退成了本地假成交——请检查 OpenD 是否在线。"
    elif promo.verdict != "PASS":
        alert = (
            f"虚拟盘练兵进度 {promo.counting_streak}/{promo.required_n}，"
            "还没达到可讨论真钱交易的门槛。"
        )
    elif halts:
        alert = f"最近一天有 {halts} 次停手记录，请留意。"
    else:
        alert = "虚拟盘实况正常。真下单仍关闭。"

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
        lane_a_label=human_lane_a(lane_a_state),
        lane_b_label=human_lane_b(lane_b_state),
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
    return raw
