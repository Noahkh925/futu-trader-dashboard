"""HTTP-shaped param version API surface (callable without FastAPI yet).

Maps 1:1 to ADR-0001 §5.2 routes under ``/api/v1/params/versions``.
Live enable endpoints are intentionally absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from params.model import (
    ParamPayload,
    ParamVersion,
    ParamVersionError,
    overlay_payload_onto_config,
)
from params.store import FileParamVersionStore, create_from_payload, default_store_root


class ParamVersionService:
    """Create / list / get / activate / rollback — paper|staging only."""

    def __init__(
        self,
        store: FileParamVersionStore | None = None,
        *,
        root: Path | str | None = None,
    ) -> None:
        if store is not None:
            self.store = store
        else:
            self.store = FileParamVersionStore(root or default_store_root())

    # GET /params/versions
    def list_versions(self) -> list[dict[str, Any]]:
        return self.store.list()

    # GET /params/versions/{id}
    def get_version(self, version_id: str) -> dict[str, Any]:
        return self.store.get(version_id).to_dict()

    # POST /params/versions
    def create_version(
        self,
        *,
        payload: dict[str, Any] | ParamPayload,
        target_env: str,
        created_by: str,
        note: str = "",
        causal_summary: str = "",
        parent_id: str | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Save = new version. Rejects production/live target_env."""
        version = create_from_payload(
            self.store,
            payload=payload,
            target_env=target_env,
            created_by=created_by,
            note=note,
            causal_summary=causal_summary,
            parent_id=parent_id,
            activate=activate,
        )
        return version.to_dict()

    # POST /params/versions/{id}/activate
    def activate_version(self, version_id: str) -> dict[str, Any]:
        """Bind ACTIVE_STAGING. Does not enable live trading."""
        return self.store.activate(version_id).to_dict()

    # POST /params/versions/{id}/rollback
    def rollback_version(
        self,
        version_id: str | None = None,
        *,
        created_by: str = "system",
        note: str = "",
    ) -> dict[str, Any]:
        """Re-activate a historical id (or active.parent_id). Never deletes."""
        return self.store.rollback(version_id, created_by=created_by, note=note).to_dict()

    def active_staging(self) -> dict[str, Any] | None:
        active_id = self.store.active_staging_id()
        if not active_id:
            return None
        return self.get_version(active_id)

    def apply_active_to_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Overlay ACTIVE_STAGING onto portfolio YAML dict for paper/staging runners.

        Forces ``trading_enabled=false`` and ``allow_production_live=false``.
        """
        active = self.active_staging()
        if active is None:
            out = dict(config)
            out["trading_enabled"] = False
            out["allow_production_live"] = False
            return out
        version = ParamVersion.from_dict(active)
        if version.target_env not in {"paper", "staging"}:
            raise ParamVersionError(
                f"refusing to apply target_env={version.target_env!r}; only paper|staging"
            )
        return overlay_payload_onto_config(config, version.payload)
