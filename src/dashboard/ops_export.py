"""Stable read-only JSON export of the ops snapshot.

Usage:
  python -m dashboard.ops_export
  python -m dashboard.ops_export --reports-dir fixtures/staging/promotion_ok
  python -m dashboard.ops_export --no-opend-probe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dashboard.ops import build_ops_snapshot, expected_dashboard_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export futu-trader ops snapshot as JSON (read-only; no trading toggles)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="portfolio.yaml path (default: repo config/portfolio.yaml)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help="Override reports directory (skips REPORTS_REMOTE_BASE / live_reports resolution)",
    )
    parser.add_argument(
        "--no-opend-probe",
        action="store_true",
        help="Skip TCP probe of OpenD (useful in CI / offline)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent (default 2; 0 for compact)",
    )
    args = parser.parse_args(argv)

    # Password gate is for the Streamlit UI only; CLI export is local/ops tooling.
    _ = expected_dashboard_password

    snap = build_ops_snapshot(
        args.config,
        reports_dir=args.reports_dir,
        probe_opend=not args.no_opend_probe,
    )
    indent = None if args.indent <= 0 else args.indent
    json.dump(snap.to_dict(), sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
