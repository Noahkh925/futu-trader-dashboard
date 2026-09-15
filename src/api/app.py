"""FastAPI console API + static 作战台 shell.

Hard constraints (PROH-126 / PROH-128 / PROH-129 / ADR-0001):
- No endpoints that enable live / trading_enabled
- Overview kill switch is display-only
- Param writes only bind paper|staging (apply ≠ live enable)
- Market + review are read-only ops projections
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.dual_run import load_dual_run_compare
from api.market import build_market_view
from api.overview import build_overview_view
from api.review import build_review_view
from api.strategy import (
    build_strategy_view,
    param_service,
    param_store_root,
    payload_from_form,
)
from dashboard.ops import build_ops_snapshot, expected_dashboard_password
from params.model import ParamVersionError


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _console_static_dir() -> Path:
    env = os.environ.get("CONSOLE_STATIC_DIR", "").strip()
    if env:
        return Path(env)
    return _repo_root() / "web" / "console"


def _default_reports_dir() -> Path | None:
    raw = os.environ.get("CONSOLE_REPORTS_DIR", "").strip()
    if raw:
        return Path(raw)
    # Local demo default: promotion_ok fixtures so the shell is clickable offline.
    demo = os.environ.get("CONSOLE_USE_FIXTURES", "").strip().lower()
    if demo in {"1", "true", "yes", "on"}:
        return _repo_root() / "fixtures" / "staging" / "promotion_ok"
    return None


def _password_ok(provided: str | None) -> bool:
    expected = expected_dashboard_password()
    if expected is None:
        return True
    if provided is None:
        return False
    return secrets.compare_digest(provided.strip(), expected)


def require_console_auth(
    x_console_password: str | None = Header(default=None, alias="X-Console-Password"),
    authorization: str | None = Header(default=None),
) -> None:
    expected = expected_dashboard_password()
    if expected is None:
        return
    token = x_console_password
    if token is None and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not _password_ok(token):
        raise HTTPException(
            status_code=401,
            detail="需要密码。输入密码后查看；此页不会改真下单开关。",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ParamCreateBody(BaseModel):
    """Save = new version → paper|staging only. Never enables live."""

    target_env: str = Field(default="staging", description="paper|staging only")
    created_by: str = Field(default="noah")
    note: str = ""
    causal_summary: str = ""
    activate: bool = True
    # Either full payload or co-pilot form fields.
    payload: dict[str, Any] | None = None
    form: dict[str, Any] | None = None


class ParamRollbackBody(BaseModel):
    created_by: str = "noah"
    note: str = ""
    version_id: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(
        title="futu-trader console API",
        version="0.3.0",
        description=(
            "Ops/overview + strategy + market + review API for the T3 作战台 shell. "
            "No live-enable or trading_enabled write endpoints. "
            "Param saves bind paper|staging only."
        ),
    )

    @app.get("/health")
    @app.get("/_stcore/health")  # Render UI may still probe Streamlit path post-cutover
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "futu-trader-console",
            "live_enable_endpoints": False,
            "param_target_envs": ["paper", "staging"],
        }

    @app.get("/api/v1/auth/status")
    def auth_status() -> dict[str, Any]:
        return {
            "password_required": expected_dashboard_password() is not None,
            "hint_zh": "输入密码后查看；此页不会改真下单开关。",
        }

    @app.get("/api/v1/ops/snapshot")
    def ops_snapshot(
        _: None = Depends(require_console_auth),
        reports_dir: str | None = Query(default=None),
        probe_opend: bool = Query(default=False),
        market: str | None = Query(
            default=None, description="US or HK — scopes report/watchlist panels"
        ),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        snap = build_ops_snapshot(
            reports_dir=rdir,
            probe_opend=probe_opend,
            market=market,
        )
        return snap.to_dict()

    @app.get("/api/v1/overview")
    def overview(
        _: None = Depends(require_console_auth),
        reports_dir: str | None = Query(default=None),
        probe_opend: bool = Query(default=False),
        market: str | None = Query(
            default=None, description="US or HK — scopes detail; board always dual"
        ),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        snap = build_ops_snapshot(
            reports_dir=rdir,
            probe_opend=probe_opend,
            market=market,
        )
        compare = load_dual_run_compare(
            Path(snap.reports_dir) if snap.reports_dir else rdir,
            session_date=snap.last_session_date,
        )
        return build_overview_view(snap, dual_run=compare)

    @app.get("/api/v1/dual-run/compare")
    def dual_run_compare(
        _: None = Depends(require_console_auth),
        date: str | None = Query(default=None),
        reports_dir: str | None = Query(default=None),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        if rdir is None:
            # Resolve via snapshot so cloud/live_reports path still works.
            snap = build_ops_snapshot(probe_opend=False)
            rdir = Path(snap.reports_dir)
            session = date or snap.last_session_date
        else:
            session = date
        return load_dual_run_compare(rdir, session_date=session)

    @app.get("/api/v1/strategy")
    def strategy(
        _: None = Depends(require_console_auth),
        reports_dir: str | None = Query(default=None),
        market: str | None = Query(
            default=None, description="US or HK — scopes latest report"
        ),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        if rdir is None:
            snap = build_ops_snapshot(probe_opend=False, market=market)
            rdir = Path(snap.reports_dir) if snap.reports_dir else None
        return build_strategy_view(
            reports_dir=rdir,
            svc=param_service(root=param_store_root()),
            market=market,
        )

    @app.get("/api/v1/market")
    def market_page(
        _: None = Depends(require_console_auth),
        reports_dir: str | None = Query(default=None),
        probe_opend: bool = Query(default=False),
        market: str | None = Query(
            default=None, description="US or HK — scopes holdings/watchlist"
        ),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        snap = build_ops_snapshot(
            reports_dir=rdir,
            probe_opend=probe_opend,
            market=market,
        )
        return build_market_view(snap)

    @app.get("/api/v1/review")
    def review(
        _: None = Depends(require_console_auth),
        reports_dir: str | None = Query(default=None),
        date: str | None = Query(default=None, description="session_date YYYY-MM-DD"),
        market: str | None = Query(
            default=None, description="US or HK — scopes report calendar"
        ),
    ) -> dict[str, Any]:
        rdir = Path(reports_dir) if reports_dir else _default_reports_dir()
        if rdir is None:
            snap = build_ops_snapshot(probe_opend=False, market=market)
            rdir = Path(snap.reports_dir) if snap.reports_dir else None
        return build_review_view(rdir, session_date=date, market=market)

    @app.get("/api/v1/params/versions")
    def list_param_versions(_: None = Depends(require_console_auth)) -> dict[str, Any]:
        svc = param_service(root=param_store_root())
        return {
            "versions": svc.list_versions(),
            "active": svc.active_staging(),
            "allowed_target_envs": ["paper", "staging"],
        }

    @app.get("/api/v1/params/versions/{version_id}")
    def get_param_version(
        version_id: str,
        _: None = Depends(require_console_auth),
    ) -> dict[str, Any]:
        svc = param_service(root=param_store_root())
        try:
            return svc.get_version(version_id)
        except ParamVersionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/params/versions")
    def create_param_version(
        body: ParamCreateBody,
        _: None = Depends(require_console_auth),
    ) -> dict[str, Any]:
        """Save = new version. Rejects production/live. Activate binds ACTIVE_STAGING only."""
        svc = param_service(root=param_store_root())
        if body.payload is not None:
            payload = body.payload
        elif body.form is not None:
            payload = payload_from_form(body.form)
        else:
            raise HTTPException(status_code=400, detail="payload 或 form 必填其一")
        try:
            created = svc.create_version(
                payload=payload,
                target_env=body.target_env,
                created_by=body.created_by,
                note=body.note,
                causal_summary=body.causal_summary,
                activate=body.activate,
            )
        except ParamVersionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "version": created,
            "message_zh": "已进 staging 队列，未开真钱。",
            "live_enabled": False,
            "trading_enabled": False,
        }

    @app.post("/api/v1/params/versions/{version_id}/activate")
    def activate_param_version(
        version_id: str,
        _: None = Depends(require_console_auth),
    ) -> dict[str, Any]:
        svc = param_service(root=param_store_root())
        try:
            activated = svc.activate_version(version_id)
        except ParamVersionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "version": activated,
            "message_zh": "已绑定练兵环境（ACTIVE_STAGING），未开真钱。",
            "live_enabled": False,
        }

    @app.post("/api/v1/params/versions/{version_id}/rollback")
    def rollback_param_version(
        version_id: str,
        body: ParamRollbackBody | None = None,
        _: None = Depends(require_console_auth),
    ) -> dict[str, Any]:
        svc = param_service(root=param_store_root())
        meta = body or ParamRollbackBody()
        try:
            rolled = svc.rollback_version(
                version_id,
                created_by=meta.created_by,
                note=meta.note or f"rollback to {version_id}",
            )
        except ParamVersionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "version": rolled,
            "message_zh": "已回滚到选定版本（仍仅练兵，未开真钱）。",
            "live_enabled": False,
        }

    @app.post("/api/v1/params/rollback")
    def rollback_active_parent(
        body: ParamRollbackBody | None = None,
        _: None = Depends(require_console_auth),
    ) -> dict[str, Any]:
        """Rollback to explicit id or active.parent_id."""
        svc = param_service(root=param_store_root())
        meta = body or ParamRollbackBody()
        try:
            rolled = svc.rollback_version(
                meta.version_id,
                created_by=meta.created_by,
                note=meta.note or "rollback to previous",
            )
        except ParamVersionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "version": rolled,
            "message_zh": "已回滚到上一参数版本（仍仅练兵，未开真钱）。",
            "live_enabled": False,
        }

    static_dir = _console_static_dir()
    if static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    return app


def main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(
        description=(
            "Serve the 作战台 console "
            "(overview + strategy + market + review, PROH-126/128/129)."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="Serve overview from fixtures/staging/promotion_ok (demo banner on).",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=None,
        help="Override reports directory (sets CONSOLE_REPORTS_DIR).",
    )
    parser.add_argument(
        "--param-root",
        type=Path,
        default=None,
        help="Override param version store root (sets CONSOLE_PARAM_ROOT).",
    )
    args = parser.parse_args(argv)

    if args.fixtures:
        os.environ["CONSOLE_USE_FIXTURES"] = "1"
    if args.reports_dir is not None:
        os.environ["CONSOLE_REPORTS_DIR"] = str(args.reports_dir)
    if args.param_root is not None:
        os.environ["CONSOLE_PARAM_ROOT"] = str(args.param_root)

    uvicorn.run(
        "api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
