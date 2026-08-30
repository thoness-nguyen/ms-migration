from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def new_report(migration_id: str | None, dry_run: bool) -> dict[str, Any]:
    """Start a migration report with the ADR's required observability fields."""
    return {
        "migration_id": migration_id,
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "dry_run": dry_run,
        "results": [],
    }


def domain_result(area: str, name: str, status: str = "failed", **fields: Any) -> dict[str, Any]:
    """Build one per-item result entry: area, source/target object, counts, retries, errors, validation."""
    return {
        "workload": area,
        "name": name,
        "status": status,
        "items_discovered": fields.pop("items_discovered", 0),
        "items_migrated": fields.pop("items_migrated", 0),
        "items_skipped": fields.pop("items_skipped", 0),
        "items_failed": fields.pop("items_failed", 0),
        "retry_count": fields.pop("retry_count", 0),
        "validation": fields.pop("validation", None),
        **fields,
    }


def finalize_report(report: dict[str, Any], output: Path, planned_workloads: dict[str, Any] | None = None) -> dict[str, Any]:
    report["completed_at"] = datetime.now(UTC).isoformat()
    if planned_workloads is not None:
        report["planned_workloads"] = planned_workloads
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
