"""Load Shadow dual-run compare artifacts when present."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_dual_run_compare(
    reports_dir: Path | str | None,
    *,
    session_date: str | None = None,
) -> dict[str, Any]:
    """Return a dual-run summary dict; ``present=False`` when no artifact."""
    empty: dict[str, Any] = {
        "present": False,
        "session_date": session_date,
        "verdict": None,
        "mode": None,
        "hard_fails": [],
        "soft_fails": [],
        "path": None,
    }
    if reports_dir is None:
        return empty
    root = Path(reports_dir)
    if not root.exists():
        return empty

    candidates: list[Path] = []
    if session_date:
        candidates.append(root / session_date / "dual_run_compare.json")
        candidates.append(root / f"dual_run_compare_{session_date}.json")
    candidates.append(root / "dual_run_compare.json")

    # Newest dated folder that contains a compare file.
    if root.is_dir():
        dated = sorted(
            (p for p in root.iterdir() if p.is_dir() and len(p.name) == 10),
            key=lambda p: p.name,
            reverse=True,
        )
        for folder in dated:
            candidates.append(folder / "dual_run_compare.json")

    path: Path | None = None
    for cand in candidates:
        if cand.is_file():
            path = cand
            break
    if path is None:
        return empty

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            **empty,
            "present": True,
            "verdict": "DEGRADED",
            "path": str(path),
            "error": "compare_file_unreadable",
        }

    if not isinstance(raw, dict):
        return {
            **empty,
            "present": True,
            "verdict": "DEGRADED",
            "path": str(path),
            "error": "compare_file_invalid",
        }

    return {
        "present": True,
        "session_date": raw.get("session_date") or session_date,
        "verdict": raw.get("verdict"),
        "mode": raw.get("mode") or "shadow",
        "hard_fails": list(raw.get("hard_fails") or []),
        "soft_fails": list(raw.get("soft_fails") or []),
        "legacy_report": raw.get("legacy_report"),
        "t3_report": raw.get("t3_report"),
        "path": str(path),
    }
