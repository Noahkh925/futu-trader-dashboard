"""Daily staging report schema (versioned) and validators.

Field table (human-readable) lives in ``docs/staging_daily_report_schema.md``.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

REQUIRED_TOP_LEVEL = (
    "schema_version",
    "session_date",
    "generated_at",
    "opend_mode",
    "futu_env",
    "capital",
    "lane_a",
    "lane_b",
    "halts",
    "disconnects",
    "errors",
    "fills_summary",
    "artifact_paths",
)

REQUIRED_CAPITAL = (
    "total",
    "lane_a_start",
    "lane_b_start",
    "lane_a_end",
    "lane_b_end",
    "lane_a_pnl",
    "lane_b_pnl",
)

REQUIRED_LANE_A = (
    "state",
    "halt_reason",
    "bars_seen",
    "fills_count",
    "signals_count",
    "realized_pnl",
)

REQUIRED_LANE_B = (
    "state",
    "halt_reason",
    "day_mode",
    "fills_count",
    "scout_hits",
    "realized_pnl",
)

ALLOWED_OPEND_MODES = frozenset({"mock", "opend", "opend_sim_fallback_mock"})
ALLOWED_LANE_B_DAY_MODES = frozenset(
    {"scout_only", "deploy", "cooldown", "idle_empty", "halt", "event_cycle"}
)


class SchemaError(ValueError):
    """Daily report failed schema validation."""


def _require_keys(obj: dict[str, Any], keys: tuple[str, ...], path: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise SchemaError(f"{path}: missing keys {missing}")


def _require_dict(obj: Any, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise SchemaError(f"{path}: expected object, got {type(obj).__name__}")
    return obj


def _require_list(obj: Any, path: str) -> list[Any]:
    if not isinstance(obj, list):
        raise SchemaError(f"{path}: expected array, got {type(obj).__name__}")
    return obj


def validate_daily_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate a daily report dict against schema v1.0. Returns the same dict."""
    data = _require_dict(report, "$")
    _require_keys(data, REQUIRED_TOP_LEVEL, "$")

    if data["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(
            f"schema_version: expected {SCHEMA_VERSION!r}, got {data['schema_version']!r}"
        )

    session_date = data["session_date"]
    if not isinstance(session_date, str) or len(session_date) < 10:
        raise SchemaError(f"session_date: expected YYYY-MM-DD string, got {session_date!r}")

    if data["opend_mode"] not in ALLOWED_OPEND_MODES:
        raise SchemaError(
            f"opend_mode: expected one of {sorted(ALLOWED_OPEND_MODES)}, got {data['opend_mode']!r}"
        )

    if not isinstance(data["futu_env"], str) or not data["futu_env"]:
        raise SchemaError("futu_env: expected non-empty string")

    capital = _require_dict(data["capital"], "capital")
    _require_keys(capital, REQUIRED_CAPITAL, "capital")
    for key in REQUIRED_CAPITAL:
        if not isinstance(capital[key], (int, float)):
            raise SchemaError(f"capital.{key}: expected number")

    lane_a = _require_dict(data["lane_a"], "lane_a")
    _require_keys(lane_a, REQUIRED_LANE_A, "lane_a")
    if not isinstance(lane_a["state"], str):
        raise SchemaError("lane_a.state: expected string")
    if lane_a["halt_reason"] is not None and not isinstance(lane_a["halt_reason"], str):
        raise SchemaError("lane_a.halt_reason: expected string or null")
    for key in ("bars_seen", "fills_count", "signals_count"):
        if not isinstance(lane_a[key], int) or lane_a[key] < 0:
            raise SchemaError(f"lane_a.{key}: expected non-negative int")
    if not isinstance(lane_a["realized_pnl"], (int, float)):
        raise SchemaError("lane_a.realized_pnl: expected number")

    lane_b = _require_dict(data["lane_b"], "lane_b")
    _require_keys(lane_b, REQUIRED_LANE_B, "lane_b")
    if not isinstance(lane_b["state"], str):
        raise SchemaError("lane_b.state: expected string")
    if lane_b["halt_reason"] is not None and not isinstance(lane_b["halt_reason"], str):
        raise SchemaError("lane_b.halt_reason: expected string or null")
    if lane_b["day_mode"] not in ALLOWED_LANE_B_DAY_MODES:
        raise SchemaError(
            f"lane_b.day_mode: expected one of {sorted(ALLOWED_LANE_B_DAY_MODES)}, "
            f"got {lane_b['day_mode']!r}"
        )
    for key in ("fills_count", "scout_hits"):
        if not isinstance(lane_b[key], int) or lane_b[key] < 0:
            raise SchemaError(f"lane_b.{key}: expected non-negative int")
    if not isinstance(lane_b["realized_pnl"], (int, float)):
        raise SchemaError("lane_b.realized_pnl: expected number")

    _require_list(data["halts"], "halts")
    _require_list(data["disconnects"], "disconnects")
    _require_list(data["errors"], "errors")
    fills = _require_dict(data["fills_summary"], "fills_summary")
    for key in ("lane_a", "lane_b", "total"):
        if key not in fills or not isinstance(fills[key], int) or fills[key] < 0:
            raise SchemaError(f"fills_summary.{key}: expected non-negative int")

    paths = _require_dict(data["artifact_paths"], "artifact_paths")
    for key in ("daily_report", "lane_a_log", "lane_b_log"):
        if key not in paths or not isinstance(paths[key], str):
            raise SchemaError(f"artifact_paths.{key}: expected string")

    # Optional PROH-90 fills[] — cloud boards sync report-only; keep loose.
    if "fills" in data and data["fills"] is not None:
        fills_list = _require_list(data["fills"], "fills")
        for i, row in enumerate(fills_list):
            if not isinstance(row, dict):
                raise SchemaError(f"fills[{i}]: expected object")

    return data
