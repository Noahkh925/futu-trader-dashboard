"""Runtime traffic switches (ADR-0001 §4). Default: legacy serves Noah."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ALLOWED_KERNELS = frozenset({"legacy", "t3"})
ALLOWED_PUBLISH = frozenset({"legacy", "t3"})
ALLOWED_API_PREF = frozenset({"legacy", "t3", "dual"})


@dataclass(frozen=True)
class RuntimeConfig:
    """Rollbackable switches — flip without deleting legacy paths."""

    active_kernel: str = "legacy"
    dual_run: bool = True
    report_publish_source: str = "legacy"
    api_read_preference: str = "legacy"

    def __post_init__(self) -> None:
        if self.active_kernel not in ALLOWED_KERNELS:
            raise ValueError(
                f"runtime.active_kernel must be one of {sorted(ALLOWED_KERNELS)}, "
                f"got {self.active_kernel!r}"
            )
        if self.report_publish_source not in ALLOWED_PUBLISH:
            raise ValueError(
                f"runtime.report_publish_source must be one of {sorted(ALLOWED_PUBLISH)}, "
                f"got {self.report_publish_source!r}"
            )
        if self.api_read_preference not in ALLOWED_API_PREF:
            raise ValueError(
                f"runtime.api_read_preference must be one of {sorted(ALLOWED_API_PREF)}, "
                f"got {self.api_read_preference!r}"
            )


def load_runtime_config(raw: Mapping[str, Any] | None) -> RuntimeConfig:
    """Parse optional ``runtime:`` YAML block; missing → safe legacy defaults."""
    data = dict(raw or {})
    return RuntimeConfig(
        active_kernel=str(data.get("active_kernel", "legacy")),
        dual_run=bool(data.get("dual_run", True)),
        report_publish_source=str(data.get("report_publish_source", "legacy")),
        api_read_preference=str(data.get("api_read_preference", "legacy")),
    )
