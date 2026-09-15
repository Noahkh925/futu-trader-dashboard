"""T3 trading kernel skeleton (ADR-0001).

Stage 2 thin slice: ports + paper/virtual runner + Shadow dual-run compare.
Legacy ``paper`` / ``staging`` / dashboard paths remain the default traffic for
Noah until cutover (``runtime.active_kernel=legacy``).

Import concrete symbols from submodules (``kernel.runner``, ``kernel.dual_run``,
``kernel.runtime``) to avoid circular imports with ``core.portfolio``.
"""

__all__ = [
    "DualRunCompare",
    "RuntimeConfig",
    "T3DayResult",
    "compare_reports",
    "load_runtime_config",
    "run_shadow_dual_run",
    "run_t3_day",
]


def __getattr__(name: str):
    if name in {"RuntimeConfig", "load_runtime_config"}:
        from kernel.runtime import RuntimeConfig, load_runtime_config

        return RuntimeConfig if name == "RuntimeConfig" else load_runtime_config
    if name in {"T3DayResult", "run_t3_day"}:
        from kernel.runner import T3DayResult, run_t3_day

        return T3DayResult if name == "T3DayResult" else run_t3_day
    if name in {"DualRunCompare", "compare_reports", "run_shadow_dual_run"}:
        from kernel.dual_run import DualRunCompare, compare_reports, run_shadow_dual_run

        mapping = {
            "DualRunCompare": DualRunCompare,
            "compare_reports": compare_reports,
            "run_shadow_dual_run": run_shadow_dual_run,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
