"""Hourly OpenD → ops heartbeat (local snapshot; optional cloud sync).

Cloud Render cannot reach laptop OpenD. This job probes OpenD locally, writes a
small heartbeat next to staging artifacts, and optionally re-syncs daily reports
to the public mirror so hosted boards pick up fresh ``manifest.updated_at``.

Default cadence is ~60 minutes (Task Scheduler / cron). Not a full staging day.
Never enables live trading.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.portfolio import load_portfolio_config
from dashboard.ops import probe_opend_reachable
from data.futu_feed import get_qqq_snapshot

HEARTBEAT_NAME = "heartbeat.json"
DEFAULT_INTERVAL_MINUTES = 60


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def heartbeat_path(dest_dir: Path | None = None) -> Path:
    base = dest_dir if dest_dir is not None else _repo_root() / "logs" / "ops_hourly"
    return Path(base) / HEARTBEAT_NAME


def load_heartbeat(path: Path | None = None) -> dict[str, Any] | None:
    hb = path if path is not None else heartbeat_path()
    if not hb.exists():
        return None
    try:
        data = json.loads(hb.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def collect_hourly_heartbeat(
    *,
    config_path: Path | None = None,
    dest_dir: Path | None = None,
    probe_opend: bool = True,
    include_quote: bool = True,
) -> dict[str, Any]:
    """Probe OpenD (optional quote) and write heartbeat JSON."""
    root = _repo_root()
    cfg_path = Path(config_path) if config_path else root / "config" / "portfolio.virtual.yaml"
    if not cfg_path.exists():
        cfg_path = root / "config" / "portfolio.yaml"
    config = load_portfolio_config(cfg_path)

    host = str(config.futu.host)
    port = int(config.futu.port)
    reachable = False
    if probe_opend:
        reachable = probe_opend_reachable(host, port)

    quote_payload: dict[str, Any] | None = None
    if include_quote:
        snap = get_qqq_snapshot(host=host, port=port)
        quote_payload = {
            "symbol": snap.code,
            "last_price": snap.last_price,
            "source": snap.source,  # futu | mock
        }

    payload: dict[str, Any] = {
        "schema": "ops_hourly_heartbeat/v1",
        "generated_at": _utc_now_iso(),
        "opend": {
            "host": host,
            "port": port,
            "reachable": reachable,
            "env": str(config.futu.env),
        },
        "quote": quote_payload,
        "trading_enabled": bool(config.trading_enabled),
        "note": (
            "Local OpenD heartbeat for dashboard freshness. "
            "Cloud hosts never TCP-probe laptop OpenD; they only read synced files."
        ),
    }

    out = heartbeat_path(dest_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _run_cloud_sync(*, push: bool) -> int:
    script = _repo_root() / "scripts" / "sync_reports_to_cloud.py"
    if not script.exists():
        print(f"sync script missing: {script}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(script)]
    if push:
        cmd.append("--push")
    print(f"running: {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=_repo_root(), check=False)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Hourly ops heartbeat: probe OpenD SIMULATE, write heartbeat.json, "
            "optionally sync/push daily reports for the cloud board."
        )
    )
    parser.add_argument(
        "--config",
        default="",
        help="Portfolio YAML (default: config/portfolio.virtual.yaml)",
    )
    parser.add_argument(
        "--dest",
        default="",
        help="Directory for heartbeat.json (default: logs/ops_hourly)",
    )
    parser.add_argument(
        "--no-probe-opend",
        action="store_true",
        help="Skip TCP probe (offline / CI).",
    )
    parser.add_argument(
        "--no-quote",
        action="store_true",
        help="Skip QQQ snapshot (probe-only).",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Also run scripts/sync_reports_to_cloud.py (copies reports + heartbeat).",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="With --sync, push the public mirror (implies --sync).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when OpenD is unreachable. Default is soft-fail (exit 0) so "
            "Task Scheduler does not mark every off-hours tick as Failed."
        ),
    )
    args = parser.parse_args(argv)

    cfg = Path(args.config) if args.config else None
    dest = Path(args.dest) if args.dest else None
    payload = collect_hourly_heartbeat(
        config_path=cfg,
        dest_dir=dest,
        probe_opend=not args.no_probe_opend,
        include_quote=not args.no_quote,
    )
    out = heartbeat_path(dest)
    reachable = bool((payload.get("opend") or {}).get("reachable"))
    quote_src = (payload.get("quote") or {}).get("source")
    print(
        f"hourly heartbeat written={out} opend_reachable={reachable} "
        f"quote_source={quote_src} at={payload.get('generated_at')}"
    )

    if args.push or args.sync:
        code = _run_cloud_sync(push=bool(args.push))
        if code != 0:
            return code

    # Soft-fail by default: heartbeat on disk is enough for the board; reachability
    # is shown in JSON / UI. Use --strict when CI wants a hard fail.
    if args.strict and not args.no_probe_opend and not reachable:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
