"""Staging harness: one-day dual-lane paper run + daily report schema."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from staging.harness import StagingDayResult
    from staging.report import DailyReport

__all__ = [
    "DailyReport",
    "StagingDayResult",
    "build_daily_report",
    "run_staging_day",
    "validate_daily_report",
]


def __getattr__(name: str) -> Any:
    if name == "DailyReport":
        from staging.report import DailyReport

        return DailyReport
    if name == "StagingDayResult":
        from staging.harness import StagingDayResult

        return StagingDayResult
    if name == "build_daily_report":
        from staging.report import build_daily_report

        return build_daily_report
    if name == "run_staging_day":
        from staging.harness import run_staging_day

        return run_staging_day
    if name == "validate_daily_report":
        from staging.schema import validate_daily_report

        return validate_daily_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
