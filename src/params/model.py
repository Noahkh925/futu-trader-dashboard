"""ParamVersion schema: watchlist / position / risk overlays, staging-bound."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

TargetEnv = Literal["paper", "staging"]
VersionStatus = Literal["draft", "active_staging", "superseded", "rejected"]

ALLOWED_TARGET_ENVS = frozenset({"paper", "staging"})
FORBIDDEN_TARGET_ENVS = frozenset({"production", "live", "real", "prod"})

SCHEMA_VERSION = "param_version_1.0"


class ParamVersionError(ValueError):
    """Invalid param version create/activate/rollback request."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def content_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_target_env(value: str) -> TargetEnv:
    env = str(value or "").strip().lower()
    if env in FORBIDDEN_TARGET_ENVS:
        raise ParamVersionError(
            f"target_env={value!r} is forbidden; save/activate only binds paper|staging "
            "(apply ≠ live enable)."
        )
    if env not in ALLOWED_TARGET_ENVS:
        raise ParamVersionError(
            f"target_env must be one of {sorted(ALLOWED_TARGET_ENVS)}, got {value!r}"
        )
    return env  # type: ignore[return-value]


@dataclass
class ParamPayload:
    """Subset of portfolio knobs that co-pilot may version.

    Shapes mirror ``config/portfolio.yaml`` lane_a / lane_b / risk fields.
    Unknown nested keys are kept for forward compatibility; runners overlay
    known paths only.
    """

    experiment_id: str = "baseline"
    lane_a: dict[str, Any] = field(default_factory=dict)
    lane_b: dict[str, Any] = field(default_factory=dict)
    # Optional inline watchlist snapshot or path reference.
    watchlist: dict[str, Any] | None = None
    # Free-form risk overlays (e.g. lane_a.risk / lane_b notional caps).
    risk: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "experiment_id": self.experiment_id,
            "lane_a": dict(self.lane_a),
            "lane_b": dict(self.lane_b),
            "risk": dict(self.risk),
        }
        if self.watchlist is not None:
            out["watchlist"] = dict(self.watchlist)
        return out

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> ParamPayload:
        raw = dict(data or {})
        return cls(
            experiment_id=str(raw.get("experiment_id") or "baseline"),
            lane_a=dict(raw.get("lane_a") or {}),
            lane_b=dict(raw.get("lane_b") or {}),
            watchlist=dict(raw["watchlist"]) if isinstance(raw.get("watchlist"), dict) else None,
            risk=dict(raw.get("risk") or {}),
        )


@dataclass
class ParamVersion:
    id: str
    created_at: str
    created_by: str
    note: str
    target_env: TargetEnv
    status: VersionStatus
    payload: ParamPayload
    content_hash: str
    parent_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    # Human-readable causal summary for co-pilot UI (what changed / why).
    causal_summary: str = ""
    # Audit: set when this record was created via rollback of another id.
    rolled_back_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "note": self.note,
            "causal_summary": self.causal_summary,
            "target_env": self.target_env,
            "status": self.status,
            "payload": self.payload.to_dict(),
            "content_hash": self.content_hash,
            "rolled_back_from": self.rolled_back_from,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParamVersion:
        if not isinstance(data, dict):
            raise ParamVersionError("version document must be an object")
        raw_payload = data.get("payload")
        payload = ParamPayload.from_mapping(raw_payload if isinstance(raw_payload, dict) else {})
        target = normalize_target_env(str(data.get("target_env") or "staging"))
        status = str(data.get("status") or "draft")
        if status not in {"draft", "active_staging", "superseded", "rejected"}:
            raise ParamVersionError(f"unknown status {status!r}")
        rolled_from = data.get("rolled_back_from")
        return cls(
            id=str(data["id"]),
            parent_id=str(data["parent_id"]) if data.get("parent_id") else None,
            created_at=str(data.get("created_at") or _utc_now_iso()),
            created_by=str(data.get("created_by") or "unknown"),
            note=str(data.get("note") or ""),
            causal_summary=str(data.get("causal_summary") or ""),
            target_env=target,
            status=status,  # type: ignore[arg-type]
            payload=payload,
            content_hash=str(data.get("content_hash") or content_hash(payload.to_dict())),
            schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
            rolled_back_from=str(rolled_from) if rolled_from else None,
        )

    def index_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "status": self.status,
            "target_env": self.target_env,
            "content_hash": self.content_hash,
            "note": self.note,
            "parent_id": self.parent_id,
            "causal_summary": self.causal_summary,
        }


def new_version_id() -> str:
    return str(uuid4())


def build_version(
    *,
    payload: ParamPayload | dict[str, Any],
    target_env: str,
    created_by: str,
    note: str = "",
    causal_summary: str = "",
    parent_id: str | None = None,
    status: VersionStatus = "draft",
    version_id: str | None = None,
    rolled_back_from: str | None = None,
) -> ParamVersion:
    env = normalize_target_env(target_env)
    body = payload if isinstance(payload, ParamPayload) else ParamPayload.from_mapping(payload)
    digest = content_hash(body.to_dict())
    return ParamVersion(
        id=version_id or new_version_id(),
        parent_id=parent_id,
        created_at=_utc_now_iso(),
        created_by=created_by,
        note=note,
        causal_summary=causal_summary,
        target_env=env,
        status=status,
        payload=body,
        content_hash=digest,
        rolled_back_from=rolled_back_from,
    )


def overlay_payload_onto_config(
    config: dict[str, Any],
    payload: ParamPayload | dict[str, Any],
) -> dict[str, Any]:
    """Deep-merge version payload into a portfolio YAML mapping (non-destructive copy).

    Always forces ``trading_enabled=false`` and ``allow_production_live=false``.
    Apply ≠ live enable — RiskOps dual-sign is a separate path.
    """
    import copy

    out = copy.deepcopy(config)
    body = payload if isinstance(payload, ParamPayload) else ParamPayload.from_mapping(payload)
    if body.experiment_id:
        out["experiment_id"] = body.experiment_id
    if body.lane_a:
        out["lane_a"] = _deep_merge(dict(out.get("lane_a") or {}), body.lane_a)
    if body.lane_b:
        out["lane_b"] = _deep_merge(dict(out.get("lane_b") or {}), body.lane_b)
    if body.risk:
        # risk may nest under lane_a.risk or as a flat lane_a.risk overlay
        if "lane_a" in body.risk and isinstance(body.risk["lane_a"], dict):
            la = dict(out.get("lane_a") or {})
            la["risk"] = _deep_merge(dict(la.get("risk") or {}), dict(body.risk["lane_a"]))
            out["lane_a"] = la
        else:
            la = dict(out.get("lane_a") or {})
            la["risk"] = _deep_merge(dict(la.get("risk") or {}), body.risk)
            out["lane_a"] = la
    out["trading_enabled"] = False
    out["allow_production_live"] = False
    return out


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
