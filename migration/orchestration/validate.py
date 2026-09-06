from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..common.checkpoint import StateStore
from ..common.graph import GraphError
from ..onedrive.validation import validate_user_drive
from ..teams.validation import validate_chat_migration


def _validate_onedrive(source_graph: Any, target_graph: Any, plan: dict[str, Any], state: StateStore, verify_hash: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in plan.get("workloads", {}).get("onedrive", {}).get("users", []):
        user_key = entry.get("user")
        user = next((item for item in plan.get("users", []) if item.get("key") == user_key), None)
        if not user:
            results.append({"user_key": user_key, "status": "error", "error": f"Unknown OneDrive user: {user_key}"})
            continue
        try:
            report = validate_user_drive(source_graph, target_graph, user["source_id"], user["target_id"], state, verify_hash=verify_hash)
        except GraphError as e:
            report = {"user": user["source_id"], "status": "error", "error": str(e)[:500]}
        report["user_key"] = user_key
        results.append(report)
    return results


def _validate_teams(source_graph: Any, target_graph: Any, plan: dict[str, Any], state: StateStore) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in plan.get("chats", []):
        source_chat_id = entry.get("source_chat_id")
        name = entry.get("name", source_chat_id)
        target_chat_id = state.target("teams-chat", source_chat_id) if source_chat_id else None
        if not target_chat_id:
            results.append({"name": name, "source_chat_id": source_chat_id, "status": "error", "error": "no target_chat_id recorded (chat not migrated yet)"})
            continue
        report = validate_chat_migration(source_graph, target_graph, source_chat_id, target_chat_id)
        report["name"] = name
        results.append(report)
    return results


def run_validation(
    source_graph: Any,
    target_graph: Any,
    plan: dict[str, Any],
    states: dict[str, StateStore],
    output_dir: Path,
    area: str,
    verify_hash: bool = False,
) -> dict[str, Any]:
    """Separate post-migration integrity pass for onedrive/teams - deliberately
    not run inline during the copy/import, since hashing every file during
    upload is expensive at scale (run this as its own pass instead). Reuses
    the checkpoint's recorded source_id/target_id pairs, so it only needs one
    GET per side per item instead of re-walking the whole tree.
    """
    if area == "onedrive":
        results = _validate_onedrive(source_graph, target_graph, plan, states["onedrive"], verify_hash)
    elif area == "teams":
        results = _validate_teams(source_graph, target_graph, plan, states["teams"])
    else:
        raise ValueError(f"Unsupported validation area: {area!r} (expected 'onedrive' or 'teams')")

    output_dir.mkdir(parents=True, exist_ok=True)
    overall = "verified" if results and all(r.get("status") == "verified" for r in results) else "mismatch"
    report = {
        "migration_id": plan.get("migration_id"),
        "area": area,
        "generated_at": datetime.now(UTC).isoformat(),
        "verify_hash": verify_hash,
        "overall_status": overall,
        "results": results,
    }
    (output_dir / f"{area}-validation-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
