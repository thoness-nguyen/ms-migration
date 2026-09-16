from __future__ import annotations

from typing import Any

from ..common.checkpoint import StateStore
from ..common.graph import GraphClient, GraphError

_HASH_FIELDS = ("quickXorHash", "sha1Hash", "sha256Hash")


def _file_hash(item: dict[str, Any]) -> str | None:
    hashes = (item.get("file") or {}).get("hashes") or {}
    for field in _HASH_FIELDS:
        if hashes.get(field):
            return f"{field}:{hashes[field]}"
    return None


def validate_user_drive(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    verify_hash: bool = False,
) -> dict[str, Any]:
    """Post-migration integrity check for one user's OneDrive.

    For every file checkpoint already marked "completed", compares source vs
    target size (and, if `verify_hash`, the content hash) using the
    source_id/target_id already recorded in `state` - no need to re-walk the
    whole tree. This is meant to run as a separate pass after the copy, not
    inline during upload (hashing everything during upload is expensive at
    scale; see ARCHITECHTURE_UPGRADE.md discussion of post-migration validation).
    """
    prefix = f"{source_user}:"
    completed_ids = [source_id for source_id in state.list_ids("onedrive", "completed") if source_id.startswith(prefix)]
    select = "id,name,size,folder" + (",file" if verify_hash else "")
    report: dict[str, Any] = {"user": source_user, "checked": 0, "verified": 0, "mismatched": [], "missing_target": [], "errors": []}

    for source_id in completed_ids:
        item_id = source_id[len(prefix):]
        target_id = state.target("onedrive", source_id)
        if not target_id:
            report["missing_target"].append({"item": item_id})
            continue
        try:
            source_item = source_graph.request("GET", f"/users/{source_user}/drive/items/{item_id}?$select={select}")
        except GraphError as e:
            report["errors"].append({"item": item_id, "error": f"source fetch failed: {str(e)[:300]}"})
            continue
        if "folder" in source_item:
            continue  # folders have no content to compare
        try:
            target_item = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_id}?$select={select}")
        except GraphError as e:
            report["errors"].append({"item": source_item.get("name", item_id), "error": f"target fetch failed: {str(e)[:300]}"})
            continue

        report["checked"] += 1
        name = source_item.get("name", item_id)
        size_match = source_item.get("size") == target_item.get("size")
        hash_match = True
        source_hash = target_hash = None
        if verify_hash:
            source_hash, target_hash = _file_hash(source_item), _file_hash(target_item)
            hash_match = bool(source_hash) and source_hash == target_hash

        if size_match and hash_match:
            report["verified"] += 1
        else:
            report["mismatched"].append({
                "item": name,
                "source_size": source_item.get("size"),
                "target_size": target_item.get("size"),
                **({"source_hash": source_hash, "target_hash": target_hash} if verify_hash else {}),
            })

    report["status"] = "verified" if not report["mismatched"] and not report["missing_target"] and not report["errors"] else "mismatch"
    return report
