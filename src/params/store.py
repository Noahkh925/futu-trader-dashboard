"""Filesystem ParamVersion store: logs/param_versions/ + ACTIVE_STAGING."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from params.model import (
    ParamPayload,
    ParamVersion,
    ParamVersionError,
    VersionStatus,
    build_version,
    normalize_target_env,
)

ACTIVE_STAGING_NAME = "ACTIVE_STAGING"
INDEX_NAME = "index.json"
AUDIT_NAME = "audit.jsonl"


class ParamVersionStore(Protocol):
    def create(self, version: ParamVersion) -> ParamVersion: ...

    def get(self, version_id: str) -> ParamVersion: ...

    def list(self) -> list[dict[str, Any]]: ...

    def activate(self, version_id: str) -> ParamVersion: ...

    def rollback(
        self, version_id: str | None = None, *, created_by: str = "system"
    ) -> ParamVersion: ...

    def active_staging_id(self) -> str | None: ...


def default_store_root(repo_root: Path | None = None) -> Path:
    base = repo_root or Path.cwd()
    return base / "logs" / "param_versions"


class FileParamVersionStore:
    """Append-only JSON documents under ``logs/param_versions/``.

    Layout (ADR-0001 §6.2)::

        logs/param_versions/
          index.json
          {id}.json
          ACTIVE_STAGING          # plain text: current id
          audit.jsonl             # activate / rollback events
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _index_path(self) -> Path:
        return self.root / INDEX_NAME

    def _version_path(self, version_id: str) -> Path:
        return self.root / f"{version_id}.json"

    def _active_path(self) -> Path:
        return self.root / ACTIVE_STAGING_NAME

    def _audit_path(self) -> Path:
        return self.root / AUDIT_NAME

    def _ensure_index(self) -> None:
        path = self._index_path()
        if not path.is_file():
            self._atomic_write_text(path, json.dumps({"versions": []}, indent=2) + "\n")

    def _read_index(self) -> dict[str, Any]:
        self._ensure_index()
        data = json.loads(self._index_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ParamVersionError("index.json must be an object")
        data.setdefault("versions", [])
        return data

    def _write_index(self, data: dict[str, Any]) -> None:
        self._atomic_write_text(
            self._index_path(),
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )

    def _atomic_write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def _append_audit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._audit_path().open("a", encoding="utf-8") as handle:
            handle.write(line)

    def create(self, version: ParamVersion) -> ParamVersion:
        normalize_target_env(version.target_env)
        path = self._version_path(version.id)
        if path.exists():
            raise ParamVersionError(f"version already exists: {version.id}")
        self._atomic_write_text(
            path,
            json.dumps(version.to_dict(), indent=2, ensure_ascii=False) + "\n",
        )
        index = self._read_index()
        rows = list(index.get("versions") or [])
        rows.append(version.index_row())
        # Newest first for list UX.
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        index["versions"] = rows
        self._write_index(index)
        self._append_audit(
            {
                "event": "create",
                "id": version.id,
                "target_env": version.target_env,
                "status": version.status,
                "created_by": version.created_by,
            }
        )
        return version

    def get(self, version_id: str) -> ParamVersion:
        path = self._version_path(version_id)
        if not path.is_file():
            raise ParamVersionError(f"version not found: {version_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ParamVersion.from_dict(data)

    def list(self) -> list[dict[str, Any]]:
        index = self._read_index()
        return list(index.get("versions") or [])

    def active_staging_id(self) -> str | None:
        path = self._active_path()
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def _set_active_staging(self, version_id: str) -> None:
        self._atomic_write_text(self._active_path(), version_id + "\n")

    def _persist_version(self, version: ParamVersion) -> None:
        self._atomic_write_text(
            self._version_path(version.id),
            json.dumps(version.to_dict(), indent=2, ensure_ascii=False) + "\n",
        )
        index = self._read_index()
        rows = []
        found = False
        for row in index.get("versions") or []:
            if row.get("id") == version.id:
                rows.append(version.index_row())
                found = True
            else:
                rows.append(row)
        if not found:
            rows.append(version.index_row())
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        index["versions"] = rows
        self._write_index(index)

    def _supersede_current_active(self, except_id: str) -> None:
        current = self.active_staging_id()
        if not current or current == except_id:
            return
        try:
            previous = self.get(current)
        except ParamVersionError:
            return
        if previous.status == "active_staging":
            previous.status = "superseded"
            self._persist_version(previous)

    def activate(self, version_id: str) -> ParamVersion:
        version = self.get(version_id)
        normalize_target_env(version.target_env)
        self._supersede_current_active(except_id=version_id)
        version.status = "active_staging"
        self._persist_version(version)
        self._set_active_staging(version_id)
        self._append_audit(
            {
                "event": "activate",
                "id": version_id,
                "target_env": version.target_env,
                "note": "binds ACTIVE_STAGING only; does not enable live",
            }
        )
        return version

    def rollback(
        self,
        version_id: str | None = None,
        *,
        created_by: str = "system",
        note: str = "",
    ) -> ParamVersion:
        """Activate a prior version without deleting history.

        If ``version_id`` is omitted, uses the active version's ``parent_id``.
        Writes an audit row; never removes old ``{id}.json`` files.
        """
        target_id = version_id
        current_id = self.active_staging_id()
        if target_id is None:
            if not current_id:
                raise ParamVersionError("no ACTIVE_STAGING to roll back from")
            current = self.get(current_id)
            if not current.parent_id:
                raise ParamVersionError(
                    f"active version {current_id} has no parent_id; pass an explicit version id"
                )
            target_id = current.parent_id

        # Ensure target exists before flipping ACTIVE_STAGING.
        self.get(target_id)
        # Rollback re-activates the historical document (status flip), keeping payload.
        activated = self.activate(target_id)
        self._append_audit(
            {
                "event": "rollback",
                "from_id": current_id,
                "to_id": target_id,
                "created_by": created_by,
                "note": note or f"rollback to {target_id}",
                "parent_of_from": (self.get(current_id).parent_id if current_id else None),
                "target_env": activated.target_env,
            }
        )
        # Stamp rolled_back_from on the activated record for UI causality.
        activated.rolled_back_from = current_id
        if note and not activated.note:
            activated.note = note
        self._persist_version(activated)
        return activated


def create_from_payload(
    store: FileParamVersionStore,
    *,
    payload: ParamPayload | dict[str, Any],
    target_env: str,
    created_by: str,
    note: str = "",
    causal_summary: str = "",
    parent_id: str | None = None,
    activate: bool = False,
    status: VersionStatus = "draft",
) -> ParamVersion:
    """Convenience: build + persist; optionally activate for staging/paper."""
    if parent_id is None:
        parent_id = store.active_staging_id()
    version = build_version(
        payload=payload,
        target_env=target_env,
        created_by=created_by,
        note=note,
        causal_summary=causal_summary,
        parent_id=parent_id,
        status=status,
    )
    stored = store.create(version)
    if activate:
        return store.activate(stored.id)
    return stored
