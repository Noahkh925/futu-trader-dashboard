"""Dashboard panels for Autopilot day watchlists (Lane A/B × market).

Reads ``day_watchlist_1.0`` product files when present. Falls back to projecting
Lane A tech watchlist / Lane B news-event fixtures so empty days still surface
``empty_reason`` on the console (PROH-187).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.market_session import MarketId, normalize_market
from dashboard.lane_a_watchlist import build_lane_a_watchlist_panel
from data.day_watchlist import (
    DayWatchlist,
    DayWatchlistError,
    load_day_watchlist,
)

LaneId = Literal["A", "B"]
PanelStatus = Literal["ok", "empty", "missing", "bad_schema"]

# Stable Autopilot landing paths (mirrors Lane A tech pointers).
STABLE_BY_LANE_MARKET: dict[tuple[LaneId, MarketId], str] = {
    ("A", "US"): "logs/lane_a/day_watchlist.json",
    ("A", "HK"): "logs/lane_a/hk/day_watchlist.json",
    ("B", "US"): "logs/lane_b/day_watchlist.json",
    ("B", "HK"): "logs/lane_b/hk/day_watchlist.json",
}
CLOUD_BY_LANE_MARKET: dict[tuple[LaneId, MarketId], Path] = {
    ("A", "US"): Path("lane_a") / "day_watchlist.json",
    ("A", "HK"): Path("lane_a") / "hk" / "day_watchlist.json",
    ("B", "US"): Path("lane_b") / "day_watchlist.json",
    ("B", "HK"): Path("lane_b") / "hk" / "day_watchlist.json",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DayWatchlistPanel:
    """UI payload: always exposes empty_reason when symbols are empty."""

    market: str
    lane: str
    status: PanelStatus
    session_date: str | None = None
    symbols: list[str] = field(default_factory=list)
    empty_reason: str | None = None
    rationale: str | None = None
    path: str | None = None
    source: str | None = None  # day_watchlist | lane_a_tech | lane_b_news | missing
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_day_watchlist_path(
    *,
    market: MarketId | str,
    lane: LaneId | str,
    reports_dir: Path | None = None,
    explicit: str | Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """First existing candidate, else preferred missing path."""
    root = repo_root or _repo_root()
    market_id = normalize_market(str(market))
    lane_id: LaneId = "A" if str(lane).upper() in {"A", "LANE_A"} else "B"

    env_key = f"LANE_{lane_id}_DAY_WATCHLIST_PATH"
    env_path = os.environ.get(env_key, "").strip()
    if explicit is not None:
        p = Path(explicit)
        return p if p.is_absolute() else root / p
    if env_path:
        p = Path(env_path)
        return p if p.is_absolute() else root / p

    preferred: list[Path] = []
    cloud = CLOUD_BY_LANE_MARKET[(lane_id, market_id)]
    stable = STABLE_BY_LANE_MARKET[(lane_id, market_id)]
    if reports_dir is not None:
        preferred.append(Path(reports_dir) / cloud)
        if market_id == "HK":
            preferred.append(Path(reports_dir) / "HK" / cloud)
    preferred.append(root / stable)

    for c in preferred:
        if c.is_file():
            return c
    return preferred[0] if preferred else None


def _from_product(wl: DayWatchlist, *, path: Path) -> DayWatchlistPanel:
    if wl.is_empty:
        return DayWatchlistPanel(
            market=wl.market,
            lane=wl.lane,
            status="empty",
            session_date=wl.session_date.isoformat(),
            symbols=[],
            empty_reason=wl.empty_reason,
            rationale=wl.rationale,
            path=str(path),
            source="day_watchlist",
        )
    return DayWatchlistPanel(
        market=wl.market,
        lane=wl.lane,
        status="ok",
        session_date=wl.session_date.isoformat(),
        symbols=list(wl.symbols),
        empty_reason=None,
        rationale=wl.rationale,
        path=str(path),
        source="day_watchlist",
    )


def _from_lane_a_tech(
    *,
    market: MarketId,
    reports_dir: Path | None,
    repo_root: Path,
) -> DayWatchlistPanel:
    panel = build_lane_a_watchlist_panel(
        market=market,
        reports_dir=reports_dir,
        repo_root=repo_root,
    )
    symbols = [str(r.get("symbol")) for r in (panel.deployable or []) if r.get("symbol")]
    if not symbols:
        empty_reason = (
            panel.empty_reason
            or panel.universe_note
            or panel.reason_zh
            or "今日 Lane A 无交易名单（no_trade_day）"
        )
        if panel.status == "missing":
            status: PanelStatus = "missing"
        elif panel.status == "bad_schema":
            status = "bad_schema"
        else:
            status = "empty"
        return DayWatchlistPanel(
            market=market,
            lane="A",
            status=status,
            session_date=panel.as_of,
            symbols=[],
            empty_reason=str(empty_reason),
            rationale=panel.universe_note,
            path=panel.path,
            source="lane_a_tech",
            error=panel.error,
        )
    return DayWatchlistPanel(
        market=market,
        lane="A",
        status="ok",
        session_date=panel.as_of,
        symbols=symbols,
        empty_reason=None,
        rationale=panel.reason_zh,
        path=panel.path,
        source="lane_a_tech",
    )


def _from_lane_b_news(
    *,
    market: MarketId,
    reports_dir: Path | None,
    repo_root: Path,
) -> DayWatchlistPanel:
    """Best-effort: project executable news-event symbols when no day product."""
    try:
        from data.lane_b_news_event import (
            LaneBNewsEventError,
            discover_news_event_paths,
            load_lane_b_news_events,
        )
    except ModuleNotFoundError:
        # Render console image is a subset — news-event parser is optional.
        return DayWatchlistPanel(
            market=market,
            lane="B",
            status="missing",
            symbols=[],
            empty_reason="今日 Lane B 尚无盘前名单（云端精简镜像未带事件解析器）",
            source="missing",
            error="lane_b_news_event_not_shipped",
        )

    candidates: list[Path] = []
    if reports_dir is not None:
        candidates.append(Path(reports_dir) / "lane_b" / "news_events")
        if market == "HK":
            candidates.append(Path(reports_dir) / "lane_b" / "hk" / "news_events")
    candidates.append(repo_root / "logs" / "lane_b" / "news_events")
    if market == "HK":
        candidates.append(repo_root / "logs" / "lane_b" / "hk" / "news_events")
        candidates.append(
            repo_root / "fixtures" / "lane_b" / "sample_news_event_hk.json"
        )
    else:
        candidates.append(repo_root / "fixtures" / "lane_b" / "sample_news_event.json")

    existing = [c for c in candidates if c.exists()]
    if not existing:
        return DayWatchlistPanel(
            market=market,
            lane="B",
            status="missing",
            symbols=[],
            empty_reason="今日 Lane B 尚无盘前名单/事件产物",
            source="missing",
            error="file_missing",
        )

    symbols: list[str] = []
    path_used: Path | None = None
    try:
        for base in existing:
            for p in discover_news_event_paths(base, fixture_base=repo_root):
                events = load_lane_b_news_events(p)
                path_used = p
                for ev in events:
                    prefix = str(ev.symbol).split(".", 1)[0].upper()
                    if prefix != market:
                        continue
                    if ev.executable() and ev.symbol not in symbols:
                        symbols.append(ev.symbol)
    except LaneBNewsEventError as exc:
        return DayWatchlistPanel(
            market=market,
            lane="B",
            status="bad_schema",
            symbols=[],
            empty_reason="今日 Lane B 事件 JSON 无效",
            path=str(path_used) if path_used else None,
            source="lane_b_news",
            error=str(exc),
        )

    if not symbols:
        return DayWatchlistPanel(
            market=market,
            lane="B",
            status="empty",
            symbols=[],
            empty_reason="今日 Lane B 无可执行事件候选（空名单）",
            path=str(path_used) if path_used else str(existing[0]),
            source="lane_b_news",
        )
    return DayWatchlistPanel(
        market=market,
        lane="B",
        status="ok",
        symbols=symbols,
        empty_reason=None,
        path=str(path_used) if path_used else str(existing[0]),
        source="lane_b_news",
    )


def build_day_watchlist_panel(
    *,
    market: MarketId | str,
    lane: LaneId | str,
    reports_dir: Path | None = None,
    path: str | Path | None = None,
    repo_root: Path | None = None,
) -> DayWatchlistPanel:
    """Load Autopilot product or project from lane-specific sources."""
    root = repo_root or _repo_root()
    market_id = normalize_market(str(market))
    lane_id: LaneId = "A" if str(lane).upper() in {"A", "LANE_A"} else "B"

    resolved = resolve_day_watchlist_path(
        market=market_id,
        lane=lane_id,
        reports_dir=reports_dir,
        explicit=path,
        repo_root=root,
    )

    if resolved is not None and resolved.is_file():
        try:
            wl = load_day_watchlist(resolved)
            if wl.market != market_id or wl.lane != lane_id:
                raise DayWatchlistError(
                    f"document market/lane={wl.market}/{wl.lane} "
                    f"!= requested {market_id}/{lane_id}"
                )
            return _from_product(wl, path=resolved)
        except DayWatchlistError as exc:
            return DayWatchlistPanel(
                market=market_id,
                lane=lane_id,
                status="bad_schema",
                symbols=[],
                empty_reason="盘前名单 JSON 无效或字段不全",
                path=str(resolved),
                source="day_watchlist",
                error=str(exc),
            )

    # Fallbacks keep the console honest when Autopilot has not written the
    # product envelope yet (still show empty_reason, never invent symbols).
    if lane_id == "A":
        return _from_lane_a_tech(
            market=market_id, reports_dir=reports_dir, repo_root=root
        )
    return _from_lane_b_news(
        market=market_id, reports_dir=reports_dir, repo_root=root
    )


def build_lane_watchlists_for_market(
    *,
    market: MarketId | str,
    reports_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Both lanes for the selected market (console overview / market page)."""
    market_id = normalize_market(str(market))
    lane_a = build_day_watchlist_panel(
        market=market_id, lane="A", reports_dir=reports_dir, repo_root=repo_root
    )
    lane_b = build_day_watchlist_panel(
        market=market_id, lane="B", reports_dir=reports_dir, repo_root=repo_root
    )
    return {
        "market": market_id,
        "A": lane_a.to_dict(),
        "B": lane_b.to_dict(),
    }
