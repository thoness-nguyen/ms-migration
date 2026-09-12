from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError
from .services import copy_item_permissions

CHUNK_SIZE = 10 * 320 * 1024


@dataclass
class _FileWork:
    item: dict[str, Any]
    source_id: str
    item_name: str
    target_parent: str


@dataclass
class _PermissionWork:
    source_item_id: str
    target_item_id: str
    item_name: str


def copy_library(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    state: StateStore,
    dry_run: bool = False,
    max_file_size_mb: int = 5000,  # 5 GB limit for individual files
    content_concurrency: int = 4,
    permission_concurrency: int = 2,
) -> dict[str, Any]:
    """Copy files and folders from source to target library.

    Runs in stages (ARCHITECHTURE_UPGRADE.md ss8): a sequential discovery pass
    that also creates/reuses folders (parent must exist before its children,
    and folder creation is cheap), followed by concurrent bounded file-content
    workers, followed by concurrent bounded permission-grant workers. A file
    or folder failure is isolated to that item and never blocks the others.

    Args:
        source_graph: Source tenant Graph client
        target_graph: Target tenant Graph client
        source_drive_id: Source library ID
        target_drive_id: Target library ID
        state: Migration state tracker
        dry_run: Preview without making changes
        max_file_size_mb: Skip files larger than this (default: 5000 MB = 5 GB)
        content_concurrency: max concurrent file downloads/uploads (default: 4)
        permission_concurrency: max concurrent permission grants (default: 2 -
            kept lower than content since permission APIs can have different
            service limits and side effects)

    Returns:
        Dictionary with copy statistics
    """
    stats = {
        "files_copied": 0,
        "folders_created": 0,
        "skipped": 0,
        "failed": 0,
        "oversized_skipped": 0,
        "errors": []
    }
    stats_lock = threading.Lock()
    file_work: list[_FileWork] = []
    permission_work: list[_PermissionWork] = []

    def discover_and_prepare_folders(source_parent: str, target_parent: str, depth: int = 0) -> None:
        """Sequential discovery + folder pre-creation pass (stage 2/3).

        Recurses into folders only (parent-before-child dependency). Files
        are just collected into `file_work` for the concurrent stage below -
        no content transfer happens here.
        """
        if depth > 100:  # Prevent runaway recursion
            print(f"  ⚠ Max folder depth exceeded at {source_parent}")
            stats["errors"].append({"folder": source_parent, "error": "Max folder depth exceeded"})
            return
        
        try:
            path = (
                f"/drives/{source_drive_id}/root/children"
                if source_parent == "root"
                else f"/drives/{source_drive_id}/items/{source_parent}/children"
            )
            
            for item in source_graph.pages(path):
                item_id = item["id"]
                source_id = f"{source_drive_id}:{item_id}"
                item_name = item.get("name", "unknown")
                
                # Check if already processed
                if state.status("sharepoint", source_id) == "completed":
                    if "folder" in item and not dry_run:
                        stored = state.target("sharepoint", source_id)
                        if stored:
                            discover_and_prepare_folders(item_id, stored, depth + 1)
                    stats["skipped"] += 1
                    continue
                
                # Process folders
                if "folder" in item:
                    if not dry_run:
                        try:
                            # Idempotency check: reuse an existing target folder instead of
                            # creating a duplicate / risking data loss via conflictBehavior=replace
                            target_folder_id = None
                            try:
                                existing = target_graph.request(
                                    "GET",
                                    f"/drives/{target_drive_id}/items/{target_parent}:/{item_name}"
                                )
                                target_folder_id = existing["id"]
                                state.mark("sharepoint", source_id, "completed", target_folder_id)
                                stats["skipped"] += 1
                            except GraphError:
                                pass  # Folder doesn't exist yet, proceed with creation
                            
                            if target_folder_id is None:
                                created = target_graph.request(
                                    "POST",
                                    f"/drives/{target_drive_id}/items/{target_parent}/children",
                                    json={
                                        "name": item_name,
                                        "folder": {},
                                        "@microsoft.graph.conflictBehavior": "fail"
                                    },
                                )
                                target_folder_id = created["id"]
                                state.mark("sharepoint", source_id, "completed", target_folder_id)
                                print(f"  [+] Created folder: {item_name}")
                                stats["folders_created"] += 1
                            
                            # Permissions are granted later, concurrently, in their own stage
                            permission_work.append(_PermissionWork(item["id"], target_folder_id, item_name))
                            
                            discover_and_prepare_folders(item_id, target_folder_id, depth + 1)
                        except (GraphError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
                            state.mark("sharepoint", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:180]}")
                            stats["failed"] += 1
                            stats["errors"].append({"item": item_name, "type": "folder", "error": f"{type(e).__name__}: {str(e)[:100]}"})
                    else:
                        discover_and_prepare_folders(item_id, target_parent, depth + 1)
                    continue
                
                # Files: just enqueue for the concurrent content stage below
                if dry_run:
                    stats["files_copied"] += 1
                    continue
                file_work.append(_FileWork(item, source_id, item_name, target_parent))
        
        except (GraphError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
            error_type = type(e).__name__
            print(f"  ✗ Error walking folder {source_parent}: {error_type}: {str(e)[:100]}")
            stats["errors"].append({
                "folder": source_parent,
                "error": f"{error_type}: {str(e)[:100]}"
            })
            # Re-raise connection errors to trigger retry logic (everything else is swallowed here, not fatal to the whole tree)
            if "Connection" in error_type or "timeout" in str(e).lower():
                raise

    discover_and_prepare_folders("root", "root")

    if dry_run:
        return stats

    # Stage 4: concurrent bounded file-content transfer
    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    def copy_one_file(work: _FileWork) -> None:
        item, source_id, item_name, target_parent = work.item, work.source_id, work.item_name, work.target_parent
        file_size_mb = 0  # guard against undefined ref in except block if size lookup itself fails
        try:
            # Check if file exists in target
            try:
                existing = target_graph.request(
                    "GET",
                    f"/drives/{target_drive_id}/items/{target_parent}:/{item_name}"
                )
                state.mark("sharepoint", source_id, "completed", existing.get("id"))
                with stats_lock:
                    stats["skipped"] += 1
                return
            except GraphError:
                pass  # File doesn't exist, proceed with copy
            
            # Get file size before downloading
            file_size_bytes = item.get("size") or 0
            file_size_mb = file_size_bytes / (1024 * 1024) if file_size_bytes else 0
            
            # Skip oversized files
            if file_size_bytes > max_file_size_mb * 1024 * 1024:
                print(f"  ⊘ Skipped oversized file: {item_name} ({file_size_mb:.1f} MB > {max_file_size_mb} MB limit)")
                state.mark("sharepoint", source_id, "completed", "oversized-skipped")
                with stats_lock:
                    stats["oversized_skipped"] += 1
                return
            
            # Download source file
            content_response = source_graph.raw_request(
                "GET",
                f"{source_graph.base_url}/drives/{source_drive_id}/items/{item['id']}/content",
                timeout=300,  # 5 min per file download
            )
            content_response.raise_for_status()
            data = content_response.content
            file_size_mb = len(data) / (1024 * 1024)
            
            # Upload to target
            if len(data) <= 4 * 1024 * 1024:
                # Simple upload for small files
                target_item = target_graph.request(
                    "PUT",
                    f"/drives/{target_drive_id}/items/{target_parent}:/{item_name}:/content",
                    data=data,
                )
            else:
                # Resumable upload for large files (with better error handling)
                try:
                    session = target_graph.request(
                        "POST",
                        f"/drives/{target_drive_id}/items/{target_parent}:/{item_name}:/createUploadSession",
                        json={
                            "item": {
                                "@microsoft.graph.conflictBehavior": "replace",
                                "name": item_name
                            }
                        },
                    )
                except GraphError as e:
                    if "ConflictBehavior" in str(e) or "not supported" in str(e).lower():
                        # Fallback: try without createUploadSession for large files
                        print(f"  ⚠ Resumable upload not supported for {item_name}, using single upload...")
                        target_item = target_graph.request(
                            "PUT",
                            f"/drives/{target_drive_id}/items/{target_parent}:/{item_name}:/content",
                            data=data,
                        )
                    else:
                        raise
                else:
                    upload_url = session["uploadUrl"]
                    target_item = None
                    
                    for chunk_num, start in enumerate(range(0, len(data), CHUNK_SIZE), 1):
                        chunk = data[start : start + CHUNK_SIZE]
                        response = target_graph.raw_request(
                            "PUT",
                            upload_url,
                            headers={
                                "Content-Length": str(len(chunk)),
                                "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}",
                            },
                            data=chunk,
                            timeout=300,
                        )
                        response.raise_for_status()
                        target_item = response.json() if response.content else target_item
                        
                        # Show progress for large files
                        if chunk_num % 5 == 0:
                            pct = min(100, int(100 * (start + len(chunk)) / len(data)))
                            print(f"    >> {item_name}: {pct}% ({(start + len(chunk)) / (1024*1024):.1f} MB / {file_size_mb:.1f} MB)")
            
            state.mark("sharepoint", source_id, "completed", target_item.get("id"))
            print(f"  [+] Copied file: {item_name} ({file_size_mb:.2f} MB)")
            with stats_lock:
                stats["files_copied"] += 1
                permission_work.append(_PermissionWork(item["id"], target_item.get("id"), item_name))
            
        except (GraphError, OSError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError) as e:
            state.mark("sharepoint", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:180]}")
            error_type = type(e).__name__
            with stats_lock:
                stats["failed"] += 1
                stats["errors"].append({
                    "item": item_name,
                    "type": "file",
                    "size_mb": file_size_mb,
                    "error": f"{error_type}: {str(e)[:100]}"
                })

    run_workers(file_work, copy_one_file, max_workers=content_concurrency, gate=content_gate)

    # Stage 6: concurrent bounded permission grants (folders + files together)
    permission_gate = AdaptiveGate(initial=permission_concurrency, minimum=1, maximum=permission_concurrency)
    source_graph.set_throttle_hook(permission_gate.record_throttle)
    target_graph.set_throttle_hook(permission_gate.record_throttle)

    def grant_one_permission(work: _PermissionWork) -> None:
        try:
            copy_item_permissions(
                source_graph, target_graph,
                source_drive_id, target_drive_id,
                work.source_item_id, work.target_item_id
            )
        except (GraphError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError, OSError) as perm_error:
            # Permission failures must never undo a successful copy/creation
            print(f"  ⚠ Permission grant failed for {work.item_name}: {perm_error}")
            with stats_lock:
                stats["errors"].append({"item": work.item_name, "type": "permission", "error": str(perm_error)[:150]})

    run_workers(permission_work, grant_one_permission, max_workers=permission_concurrency, gate=permission_gate)

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    stats["throttle_events"] = content_gate.throttle_events + permission_gate.throttle_events
    return stats
