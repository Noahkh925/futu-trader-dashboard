"""Dashboard-facing Lane A tech watchlist view model (PROH-109).

Loads ``lane_a_tech_1.0`` for the ops board. Fail-closed: missing file / bad
schema / empty deployable universe → ``no_trade_day`` (never invent a QQQ list).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.portfolio import PortfolioConfig, load_portfolio_config
from data.lane_a_tech_watchlist import (
    LaneATechName,
    LaneATechWatchlist,
    LaneATechWatchlistError,
    load_lane_a_tech_watchlist,
)

WatchlistPanelStatus = Literal["ok", "no_trade_day", "missing", "bad_schema"]

DEFAULT_STABLE_POINTER = "logs/lane_a/tech_watchlist.json"
CLOUD_RELATIVE = Path("lane_a") / "tech_watchlist.json"


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
        deployable=deployable,
        vetoed=vetoed,
    )


def resolve_lane_a_watchlist_path(
    *,
    config: PortfolioConfig | None = None,
    reports_dir: Path | None = None,
    explicit: str | Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Pick the first existing candidate path, or the preferred missing path.

    Resolution order (first *existing* wins; if none exist, return preferred):
    1. ``explicit`` / ``LANE_A_TECH_WATCHLIST_PATH`` (strict — no fallback)
    2. ``{reports_dir}/lane_a/tech_watchlist.json`` (cloud sync landing)
    3. repo ``logs/lane_a/tech_watchlist.json`` (Autopilot stable pointer)
    4. ``config.lane_a.tech_watchlist_path`` (CI fixtures last — never mask live sync)
    """
    root = repo_root or _repo_root()

    env_path = os.environ.get("LANE_A_TECH_WATCHLIST_PATH", "").strip()
    if explicit is not None:
        p = Path(explicit)
        return p if p.is_absolute() else root / p
    if env_path:
        p = Path(env_path)
        return p if p.is_absolute() else root / p

    preferred: list[Path] = []
    fallback: list[Path] = []

    if reports_dir is not None:
        preferred.append(Path(reports_dir) / CLOUD_RELATIVE)

    preferred.append(root / DEFAULT_STABLE_POINTER)

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
) -> LaneAWatchlistPanel:
    """Load + structure watchlist for the dashboard. Never fabricates symbols."""
    root = repo_root or _repo_root()
    cfg = config
    if cfg is None:
        cfg_path = Path(config_path) if config_path else root / "config" / "portfolio.yaml"
        try:
            cfg = load_portfolio_config(cfg_path)
        except Exception:
            cfg = None

    resolved = resolve_lane_a_watchlist_path(
        config=cfg,
        reports_dir=reports_dir,
        explicit=path,
        repo_root=root,
    )
    if resolved is None:
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh="今日 Lane A 不交易（no_trade_day）：未配置 tech watchlist 路径",
            error="path_unresolved",
        )

    if not resolved.is_file():
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh=(
                "今日 Lane A 不交易（no_trade_day）：找不到盘前名单文件"
                f"（期望路径：{resolved}）"
            ),
            path=str(resolved),
            error="file_missing",
        )

    try:
        wl = load_lane_a_tech_watchlist(resolved)
    except LaneATechWatchlistError as exc:
        return LaneAWatchlistPanel(
            status="bad_schema",
            no_trade_day=True,
            reason_zh=(
                "今日 Lane A 不交易（no_trade_day）：名单 JSON 无效或 schema 不符"
            ),
            path=str(resolved),
            error=str(exc),
        )
    except OSError as exc:
        return LaneAWatchlistPanel(
            status="missing",
            no_trade_day=True,
            reason_zh="今日 Lane A 不交易（no_trade_day）：无法读取名单文件",
            path=str(resolved),
            error=str(exc),
        )

    return _from_watchlist(wl, path=resolved)
