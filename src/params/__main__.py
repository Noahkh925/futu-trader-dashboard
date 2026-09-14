"""CLI: create / list / activate / rollback param versions (paper|staging only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from params.service import ParamVersionService
from params.store import default_store_root


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_payload(path: str | None, inline_json: str | None) -> dict[str, Any]:
    if inline_json:
        data = json.loads(inline_json)
    elif path:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        raise SystemExit("provide --payload-file or --payload-json")
    if not isinstance(data, dict):
        raise SystemExit("payload must be a JSON object")
    return data


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="futu-params",
        description=(
            "Parameter co-pilot versions: save=new version → paper/staging only. "
            "Activate binds ACTIVE_STAGING; never enables live."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Store root (default: <repo>/logs/param_versions)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Save a new version (draft unless --activate)")
    create.add_argument("--payload-file", default=None)
    create.add_argument("--payload-json", default=None)
    create.add_argument("--target-env", required=True, choices=["paper", "staging"])
    create.add_argument("--created-by", default="noah")
    create.add_argument("--note", default="")
    create.add_argument("--causal-summary", default="")
    create.add_argument("--parent-id", default=None)
    create.add_argument(
        "--activate",
        action="store_true",
        help="Also bind ACTIVE_STAGING (still not live)",
    )

    sub.add_parser("list", help="List version index rows")

    get = sub.add_parser("get", help="Get one version document")
    get.add_argument("version_id")

    activate = sub.add_parser("activate", help="Bind ACTIVE_STAGING to version id")
    activate.add_argument("version_id")

    rollback = sub.add_parser("rollback", help="Re-activate prior version (never deletes)")
    rollback.add_argument(
        "version_id",
        nargs="?",
        default=None,
        help="Target id (default: active.parent_id)",
    )
    rollback.add_argument("--created-by", default="noah")
    rollback.add_argument("--note", default="")

    active = sub.add_parser("active", help="Show ACTIVE_STAGING document or null")
    _ = active

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else default_store_root(_repo_root())
    svc = ParamVersionService(root=root)

    if args.command == "create":
        payload = _load_payload(args.payload_file, args.payload_json)
        doc = svc.create_version(
            payload=payload,
            target_env=args.target_env,
            created_by=args.created_by,
            note=args.note,
            causal_summary=args.causal_summary,
            parent_id=args.parent_id,
            activate=args.activate,
        )
        _print(doc)
        return 0

    if args.command == "list":
        _print(svc.list_versions())
        return 0

    if args.command == "get":
        _print(svc.get_version(args.version_id))
        return 0

    if args.command == "activate":
        _print(svc.activate_version(args.version_id))
        return 0

    if args.command == "rollback":
        _print(
            svc.rollback_version(
                args.version_id,
                created_by=args.created_by,
                note=args.note,
            )
        )
        return 0

    if args.command == "active":
        _print(svc.active_staging())
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
