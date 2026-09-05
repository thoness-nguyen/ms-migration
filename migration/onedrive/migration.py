from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError
from ..sharepoint.services import encode_graph_path_segment

CHUNK_SIZE = 10 * 320 * 1024


@dataclass
class _FileWork:
    item: dict[str, Any]
    source_id: str
    target_parent: str


def _transfer_one_file(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    stats: dict[str, Any],
    stats_lock: threading.Lock,
    work: _FileWork,
) -> None:
    """Download one file from source and upload it to target, recording the
    outcome in `state`/`stats`. Shared by the full-tree copy (copy_drive) and
    the targeted retry (retry_failed_onedrive_files)."""
    item, source_id, target_parent = work.item, work.source_id, work.target_parent
    try:
        # skip file if an item with the same name already exists in the target folder
        try:
            existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}")
            state.mark("onedrive", source_id, "completed", existing.get("id") if existing else None)
            with stats_lock:
                stats["skipped"] += 1
            return
        except GraphError:
            pass
        content = source_graph.raw_request("GET", f"{source_graph.base_url}/users/{source_user}/drive/items/{item['id']}/content", timeout=120)
        content.raise_for_status()
        data = content.content
        if len(data) <= 4 * 1024 * 1024:
            target = target_graph.request("PUT", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}:/content", data=data)
        else:
            session = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}:/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": item["name"]}})
            upload_url = session["uploadUrl"]
            target = None
            for start in range(0, len(data), CHUNK_SIZE):
                chunk = data[start:start + CHUNK_SIZE]
                response = target_graph.raw_request("PUT", upload_url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, data=chunk, timeout=120)
                response.raise_for_status()
                target = response.json() if response.content else target
        state.mark("onedrive", source_id, "completed", target.get("id") if target else None)
        with stats_lock:
            stats["files_copied"] += 1
    except (GraphError, OSError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError) as e:
        state.mark("onedrive", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:300]}")
        with stats_lock:
            stats["failed"] += 1
            stats["errors"].append({"item": item.get("name", "unknown"), "error": f"{type(e).__name__}: {str(e)[:200]}"})


def retry_failed_onedrive_files(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    content_concurrency: int = 4,
) -> dict[str, Any]:
    """Re-attempt only the items already recorded as "failed" for this
    drive, instead of re-walking the whole tree - same rationale as
    sharepoint/migration.py's retry_failed_sharepoint_files()."""
    stats: dict[str, Any] = {"files_copied": 0, "skipped": 0, "failed": 0, "still_missing_parent": 0, "errors": []}
    stats_lock = threading.Lock()

    prefix = f"{source_user}:"
    failed_ids = [source_id for source_id in state.list_ids("onedrive", "failed") if source_id.startswith(prefix)]
    if not failed_ids:
        return stats

    root_id = source_graph.request("GET", f"/users/{source_user}/drive/root?$select=id")["id"]

    file_work: list[_FileWork] = []
    for source_id in failed_ids:
        item_id = source_id[len(prefix):]
        try:
            item = source_graph.request("GET", f"/users/{source_user}/drive/items/{item_id}?$select=id,name,size,parentReference,folder")
        except GraphError as e:
            state.mark("onedrive", source_id, "failed", detail=f"re-fetch failed: {type(e).__name__}: {str(e)[:280]}")
            stats["failed"] += 1
            stats["errors"].append({"item": item_id, "error": str(e)[:200]})
            continue

        if "folder" in item:
            stats["errors"].append({"item": item.get("name", item_id), "type": "folder", "error": "folder retry needs a full discovery pass"})
            continue

        parent_id = (item.get("parentReference") or {}).get("id")
        target_parent = "root" if parent_id == root_id else state.target("onedrive", f"{source_user}:{parent_id}")
        if not target_parent:
            state.mark("onedrive", source_id, "failed", detail="parent folder not migrated yet - run a full discovery pass")
            stats["still_missing_parent"] += 1
            continue

        file_work.append(_FileWork(item, source_id, target_parent))

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)
    run_workers(
        file_work,
        lambda work: _transfer_one_file(source_graph, target_graph, source_user, target_user, state, stats, stats_lock, work),
        max_workers=content_concurrency,
        gate=content_gate,
    )
    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats


def copy_drive(source_graph: GraphClient, target_graph: GraphClient, source_user: str, target_user: str, state: StateStore, dry_run: bool = False, content_concurrency: int = 4) -> dict[str, Any]:
    """Copy a user's OneDrive. Sequential discovery + folder pre-creation
    (parent must exist before child), then concurrent bounded file-content
    workers - same pipeline as sharepoint/migration.py's copy_library()."""
    stats: dict[str, Any] = {"files_copied": 0, "skipped": 0, "failed": 0, "errors": []}
    stats_lock = threading.Lock()
    file_work: list[_FileWork] = []

    def discover_and_prepare_folders(source_parent: str, target_parent: str) -> None:
        try:
            for item in source_graph.pages(f"/users/{source_user}/drive/items/{source_parent}/children" if source_parent != "root" else f"/users/{source_user}/drive/root/children"):
                item_id = item["id"]
                source_id = f"{source_user}:{item_id}"
                if state.status("onedrive", source_id) == "completed":
                    if "folder" in item and not dry_run:
                        # still recurse to pick up new files added since last run
                        stored = state.target("onedrive", source_id)
                        if stored:
                            discover_and_prepare_folders(item_id, stored)
                    with stats_lock:
                        stats["skipped"] += 1
                    continue
                if "folder" in item:
                    if not dry_run:
                        try:
                            # Idempotency check: reuse an existing target folder instead of creating
                            # a duplicate / risking data loss via conflictBehavior=replace
                            target_folder_id = None
                            try:
                                existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}")
                                target_folder_id = existing["id"]
                                state.mark("onedrive", source_id, "completed", target_folder_id)
                            except GraphError:
                                pass  # Folder doesn't exist yet, proceed with creation
                            if target_folder_id is None:
                                created = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}/children", json={"name": item["name"], "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                                target_folder_id = created["id"]
                                state.mark("onedrive", source_id, "completed", target_folder_id)
                            discover_and_prepare_folders(item_id, target_folder_id)
                        except (GraphError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
                            state.mark("onedrive", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:300]}")
                            with stats_lock:
                                stats["failed"] += 1
                                stats["errors"].append({"item": item.get("name", "unknown"), "type": "folder", "error": f"{type(e).__name__}: {str(e)[:200]}"})
                    else:
                        discover_and_prepare_folders(item_id, target_parent)
                    continue
                if dry_run:
                    with stats_lock:
                        stats["files_copied"] += 1
                    continue
                file_work.append(_FileWork(item, source_id, target_parent))
        except (GraphError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
            print(f"  ✗ Error walking folder {source_parent}: {type(e).__name__}: {str(e)[:200]}")
            with stats_lock:
                stats["errors"].append({"folder": source_parent, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            # Always re-raise - a failed children listing means this whole subtree
            # was skipped, not just one item (see sharepoint/migration.py for why).
            raise

    discover_and_prepare_folders("root", "root")

    if dry_run:
        return stats

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    run_workers(
        file_work,
        lambda work: _transfer_one_file(source_graph, target_graph, source_user, target_user, state, stats, stats_lock, work),
        max_workers=content_concurrency,
        gate=content_gate,
    )

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats
