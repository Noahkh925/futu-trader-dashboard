"""Promotion gate over staging daily reports."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from staging.schema import SchemaError, validate_daily_report

DEFAULT_N = 20
MAX_HALTS_PER_DAY = 2


@dataclass
class DayVerdict:
    date: str
    counts: bool
    opend_mode: str
    halts: int
    reason: str = ""


@dataclass
class PromotionResult:
    required_n: int
    counting_streak: int
    verdict: str  # PASS | FAIL
    days: list[DayVerdict] = field(default_factory=list)
    excluded_fallback_days: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_n": self.required_n,
            "counting_streak": self.counting_streak,
            "verdict": self.verdict,
            "excluded_fallback_days": list(self.excluded_fallback_days),
            "days": [asdict(d) for d in self.days],
            "reasons": list(self.reasons),
        }


def _load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_daily_report(data)


def evaluate_day(report: dict[str, Any]) -> DayVerdict:
    session_date = str(report["session_date"])
    mode = str(report["opend_mode"])
    halts = len(report.get("halts") or [])
    if mode == "opend_sim_fallback_mock":
        return DayVerdict(
            date=session_date,
            counts=False,
            opend_mode=mode,
            halts=halts,
            reason="fallback_mock_excluded",
        )
    if mode not in {"mock", "opend"}:
        return DayVerdict(
            date=session_date,
            counts=False,
            opend_mode=mode,
            halts=halts,
            reason="unknown_opend_mode",
        )
    if str(report.get("futu_env") or "") not in {"staging", "simulate"}:
        return DayVerdict(
            date=session_date,
            counts=False,
            opend_mode=mode,
            halts=halts,
            reason="futu_env_not_staging",
        )
    if halts > MAX_HALTS_PER_DAY:
        return DayVerdict(
            date=session_date,
            counts=False,
            opend_mode=mode,
            halts=halts,
            reason="halt_cap_exceeded",
        )
    return DayVerdict(date=session_date, counts=True, opend_mode=mode, halts=halts)


def discover_reports(reports_dir: Path) -> list[Path]:
    """Find daily_report.json under dir or dir/YYYY-MM-DD/."""
    root = Path(reports_dir)
    if not root.exists():
        return []
    direct = sorted(root.glob("**/daily_report.json"))
    if direct:
        return direct
    # flat json files
    return sorted(root.glob("*.json"))


def check_promotion(
    reports_dir: Path,
    *,
    n: int = DEFAULT_N,
) -> PromotionResult:
    paths = discover_reports(Path(reports_dir))
    days: list[DayVerdict] = []
    excluded: list[str] = []
    for path in paths:
        try:
            report = _load_report(path)
        except (OSError, json.JSONDecodeError, SchemaError, KeyError) as exc:
            days.append(
                DayVerdict(
                    date=path.parent.name or path.stem,
                    counts=False,
                    opend_mode="?",
                    halts=0,
                    reason=f"load_error:{exc}",
                )
            )
            continue
        verdict = evaluate_day(report)
        days.append(verdict)
        if verdict.reason == "fallback_mock_excluded":
            excluded.append(verdict.date)

    # consecutive counting streak from the end of the sorted-by-date list
    days_sorted = sorted(days, key=lambda d: d.date)
    streak = 0
    for d in reversed(days_sorted):
        if d.counts:
            streak += 1
        else:
            break

    reasons: list[str] = []
    verdict = "PASS" if streak >= n else "FAIL"
    if streak < n:
        reasons.append(f"counting streak {streak} < required {n}")
    if not paths:
        reasons.append("no reports found")
        verdict = "FAIL"

    return PromotionResult(
        required_n=n,
        counting_streak=streak,
        verdict=verdict,
        days=days_sorted,
        excluded_fallback_days=excluded,
        reasons=reasons or (["ok"] if verdict == "PASS" else ["fail"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="futu-trader promotion gate")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory of daily_report.json files or YYYY-MM-DD folders",
    )
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--json", action="store_true", help="Print JSON verdict")
    args = parser.parse_args(argv)
    result = check_promotion(args.reports_dir, n=args.n)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"verdict={result.verdict} streak={result.counting_streak}/{result.required_n}")
        for r in result.reasons:
            print(f"- {r}")
    return 0 if result.verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
