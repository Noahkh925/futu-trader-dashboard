"""Dashboard-facing Lane A tech watchlist view model (PROH-109 / PROH-179).

Loads ``lane_a_tech_1.0`` for the ops board. Fail-closed: missing file / bad
schema / empty deployable universe → ``no_trade_day`` (never invent a QQQ list).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.market_session import MarketId, default_config_for, normalize_market
from core.portfolio import PortfolioConfig, load_portfolio_config
from data.lane_a_tech_watchlist import (
    LaneATechName,
    LaneATechWatchlist,
    LaneATechWatchlistError,
    load_lane_a_tech_watchlist,
)

WatchlistPanelStatus = Literal["ok", "no_trade_day", "missing", "bad_schema"]

DEFAULT_STABLE_POINTER = "logs/lane_a/tech_watchlist.json"
DEFAULT_STABLE_POINTER_BY_MARKET: dict[MarketId, str] = {
    "US": "logs/lane_a/tech_watchlist.json",
    "HK": "logs/lane_a/hk/tech_watchlist.json",
}
CLOUD_RELATIVE = Path("lane_a") / "tech_watchlist.json"
CLOUD_RELATIVE_BY_MARKET: dict[MarketId, Path] = {
    "US": Path("lane_a") / "tech_watchlist.json",
    "HK": Path("lane_a") / "hk" / "tech_watchlist.json",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LaneAWatchlistPanel:
    """Structured payload for UI / ops_export (JSON-serializable via ``to_dict``)."""

    status: WatchlistPanelStatus
    no_trade_day: bool
    reason_zh: str
    path: str | None = None
    schema_version: str | None = None
    as_of: str | None = None
    timezone: str | None = None
    generated_by: str | None = None
    universe_note: str | None = None
    # Autopilot product field (PROH-187): human reason when no deployable names.
    empty_reason: str | None = None
    deployable: list[dict[str, Any]] = field(default_factory=list)
    vetoed: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def name_to_ui_dict(name: LaneATechName) -> dict[str, Any]:
    """Flatten one watchlist row for Streamlit / JSON export."""
    return {
        "symbol": name.symbol,
        "score": name.score,
        "veto": name.veto,
        "reasons": list(name.reasons),
        "sources": list(name.sources),
        "last_price": name.last_price,
        "min_price_ok": name.min_price_ok,
        "adv_ok": name.adv_ok,
        "avg_dollar_volume": name.avg_dollar_volume,
        "avg_volume": name.avg_volume,
        "gap_pct": name.gap_pct,
        "atr_pct": name.atr_pct,
        "relative_volume": name.relative_volume,
        "regime_tag": name.regime_tag,
        "market": name.market,
        "reason": name.reason_text,
    }


def _from_watchlist(wl: LaneATechWatchlist, *, path: Path) -> LaneAWatchlistPanel:
    deployable = [name_to_ui_dict(n) for n in wl.deployable()]
    vetoed = [name_to_ui_dict(n) for n in wl.vetoed()]
    # Sort deployable by score desc for UI (matches executor selection order).
    deployable.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    if not deployable:
        reason = "今日 Lane A 不交易（no_trade_day）：可交易名单为空"
        if vetoed:
            reason = (
                f"今日 Lane A 不交易（no_trade_day）："
                f"全部 {len(vetoed)} 只被否决，无可交易标的"
            )
        elif not wl.names:
            reason = "今日 Lane A 不交易（no_trade_day）：盘前筛选结果为空名单"
        # Prefer analyst universe_note as Autopilot empty_reason when present.
        empty = (wl.universe_note or "").strip() or reason
        return LaneAWatchlistPanel(
            status="no_trade_day",
            no_trade_day=True,
            reason_zh=reason,
            path=str(path),
            schema_version=wl.schema_version,
            as_of=wl.as_of.isoformat(),
            timezone=wl.timezone,
            generated_by=wl.generated_by,
            universe_note=wl.universe_note,
            empty_reason=empty,
            deployable=[],
            vetoed=vetoed,
        )
    return LaneAWatchlistPanel(
        status="ok",
        no_trade_day=False,
        reason_zh=f"可交易 {len(deployable)} 只 · 否决 {len(vetoed)} 只",
        path=str(path),
        schema_version=wl.schema_version,
        as_of=wl.as_of.isoformat(),
        timezone=wl.timezone,
        generated_by=wl.generated_by,
        universe_note=wl.universe_note,
        empty_reason=None,
        deployable=deployable,
        vetoed=vetoed,
    )


def resolve_lane_a_watchlist_path(
    *,
    config: PortfolioConfig | None = None,
    reports_dir: Path | None = None,
    explicit: str | Path | None = None,
    repo_root: Path | None = None,
    market: MarketId | str | None = None,
) -> Path | None:
    """Pick the first existing candidate path, or the preferred missing path.

    Resolution order (first *existing* wins; if none exist, return preferred):
    1. ``explicit`` / ``LANE_A_TECH_WATCHLIST_PATH`` (strict — no fallback)
    2. ``{reports_dir}/lane_a[/hk]/tech_watchlist.json`` (cloud sync landing)
    3. repo ``logs/lane_a[/hk]/tech_watchlist.json`` (Autopilot stable pointer)
    4. ``config.lane_a.tech_watchlist_path`` (CI fixtures last — never mask live sync)
    """
    root = repo_root or _repo_root()
    market_id: MarketId | None = None
    if market is not None:
        market_id = normalize_market(str(market))
    elif config is not None and getattr(config, "market", None):
        try:
            market_id = normalize_market(str(config.market))
        except ValueError:
            market_id = None

    env_path = os.environ.get("LANE_A_TECH_WATCHLIST_PATH", "").strip()
    if explicit is not None:
        p = Path(explicit)
        return p if p.is_absolute() else root / p
    if env_path:
        p = Path(env_path)
        return p if p.is_absolute() else root / p

    preferred: list[Path] = []
    fallback: list[Path] = []

    cloud_rel = (
        CLOUD_RELATIVE_BY_MARKET.get(market_id, CLOUD_RELATIVE)
        if market_id
        else CLOUD_RELATIVE
    )
    stable = (
        DEFAULT_STABLE_POINTER_BY_MARKET.get(market_id, DEFAULT_STABLE_POINTER)
        if market_id
        else DEFAULT_STABLE_POINTER
    )

    if reports_dir is not None:
        preferred.append(Path(reports_dir) / cloud_rel)
        if market_id == "HK":
            # Also accept nested under reports_dir/HK if sync mirrors staging.
            preferred.append(Path(reports_dir) / "HK" / cloud_rel)

    preferred.append(root / stable)
    if market_id == "US":
        # Keep legacy US pointer first; HK must not fall through to US list.
        pass
    elif market_id is None:
        preferred.append(root / DEFAULT_STABLE_POINTER_BY_MARKET["HK"])

    if config is not None and config.lane_a.tech_watchlist_path:
        p = Path(config.lane_a.tech_watchlist_path)
        fallback.append(p if p.is_absolute() else root / p)

    seen: set[str] = set()
    unique: list[Path] = []
    for c in preferred + fallback:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    for c in unique:
        if c.is_file():
            return c
    # Prefer a live path in the missing-file message (not the CI fixture).
    return preferred[0] if preferred else (unique[0] if unique else None)


def build_lane_a_watchlist_panel(
    *,
    config_path: str | Path | None = None,
    config: PortfolioConfig | None = None,
    reports_dir: Path | None = None,
    path: str | Path | None = None,
    repo_root: Path | None = None,
    market: MarketId | str | None = None,
) -> LaneAWatchlistPanel:
    """Load + structure watchlist for the dashboard. Never fabricates symbols."""
    root = repo_root or _repo_root()
    market_id: MarketId | None = (
        normalize_market(str(market)) if market is not None else None
    )
    cfg = config
    if cfg is None:
        if config_path is not None:
            cfg_path = Path(config_path)
        elif market_id is not None:
            cfg_path = root / default_config_for(market_id)
        else:
            cfg_path = root / "config" / "portfolio.yaml"
        try:
            cfg = load_portfolio_config(cfg_path)
        except Exception:
            cfg = None

    if market_id is None and cfg is not None and getattr(cfg, "market", None):
        try:
            market_id = normalize_market(str(cfg.market))
        except ValueError:
            market_id = None

    resolved = resolve_lane_a_watchlist_path(
        config=cfg,
        reports_dir=reports_dir,
        explicit=path,
        repo_root=root,
        market=market_id,
    )
    if resolved is None:
        reason = "今日 Lane A 不交易（no_trade_day）：未配置 tech watchlist 路径"
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh=reason,
            empty_reason=reason,
            error="path_unresolved",
        )

    if not resolved.is_file():
        reason = (
            "今日 Lane A 不交易（no_trade_day）：找不到盘前名单文件"
            f"（期望路径：{resolved}）"
        )
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh=reason,
            empty_reason=reason,
            path=str(resolved),
            error="file_missing",
        )

    try:
        wl = load_lane_a_tech_watchlist(resolved)
    except LaneATechWatchlistError as exc:
        reason = "今日 Lane A 不交易（no_trade_day）：名单 JSON 无效或 schema 不符"
        return LaneAWatchlistPanel(
            status="bad_schema",
            no_trade_day=True,
            reason_zh=reason,
            empty_reason=reason,
            path=str(resolved),
            error=str(exc),
        )
    except OSError as exc:
        reason = "今日 Lane A 不交易（no_trade_day）：无法读取名单文件"
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh=reason,
            empty_reason=reason,
            path=str(resolved),
            error=str(exc),
        )

    return _from_watchlist(wl, path=resolved)
