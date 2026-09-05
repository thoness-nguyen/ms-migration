from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError
from .services import encode_graph_path_segment

CHUNK_SIZE = 10 * 320 * 1024


@dataclass
class _FileWork:
    item: dict[str, Any]
    source_id: str
    item_name: str
    target_parent: str


def _transfer_one_file(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    state: StateStore,
    stats: dict[str, Any],
    stats_lock: threading.Lock,
    work: _FileWork,
    max_file_size_mb: int,
) -> None:
    """Download one file from source and upload it to target, recording the
    outcome in `state`/`stats`. Shared by the full-tree copy (copy_library)
    and the targeted retry (retry_failed_sharepoint_files) so both paths
    behave identically for an individual file."""
    item, source_id, item_name, target_parent = work.item, work.source_id, work.item_name, work.target_parent
    file_size_mb = 0  # guard against undefined ref in except block if size lookup itself fails
    try:
        # Check if file exists in target
        try:
            existing = target_graph.request(
                "GET",
                f"/drives/{target_drive_id}/items/{target_parent}:/{encode_graph_path_segment(item_name)}"
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
                f"/drives/{target_drive_id}/items/{target_parent}:/{encode_graph_path_segment(item_name)}:/content",
                data=data,
            )
        else:
            # Resumable upload for large files (with better error handling)
            try:
                session = target_graph.request(
                    "POST",
                    f"/drives/{target_drive_id}/items/{target_parent}:/{encode_graph_path_segment(item_name)}:/createUploadSession",
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
                        f"/drives/{target_drive_id}/items/{target_parent}:/{encode_graph_path_segment(item_name)}:/content",
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

    except (GraphError, OSError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError) as e:
        # Keep enough of the message to see the actual HTTP status/reason (not
        # just "GraphError: PUT https://...verylongurl") when diagnosing later.
        state.mark("sharepoint", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:2000]}")
        error_type = type(e).__name__
        with stats_lock:
            stats["failed"] += 1
            stats["errors"].append({
                "item": item_name,
                "type": "file",
                "size_mb": file_size_mb,
                "error": f"{error_type}: {str(e)[:1000]}"
            })


def retry_failed_sharepoint_files(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    state: StateStore,
    content_concurrency: int = 4,
    max_file_size_mb: int = 20000,
) -> dict[str, Any]:
    """Re-attempt only the items already recorded as "failed" for this
    library, instead of re-walking the whole (mostly already-migrated) tree.

    A full copy_library() run always starts with a sequential, single Graph
    call per folder discovery pass so it knows what's already done - on a
    library with hundreds of thousands of items that alone can take hours
    before it even reaches the handful of items that actually need retrying.
    This looks up the small failed set directly from the checkpoint store and
    only re-fetches/re-copies those, using the already-recorded parent folder
    mapping instead of rediscovering it.
    """
    stats: dict[str, Any] = {"files_copied": 0, "skipped": 0, "failed": 0, "still_missing_parent": 0, "errors": []}
    stats_lock = threading.Lock()

    prefix = f"{source_drive_id}:"
    failed_ids = [source_id for source_id in state.list_ids("sharepoint", "failed") if source_id.startswith(prefix)]
    if not failed_ids:
        return stats

    root_id = source_graph.request("GET", f"/drives/{source_drive_id}/root?$select=id")["id"]

    file_work: list[_FileWork] = []
    for source_id in failed_ids:
        item_id = source_id[len(prefix):]
        try:
            item = source_graph.request("GET", f"/drives/{source_drive_id}/items/{item_id}?$select=id,name,size,parentReference,folder")
        except GraphError as e:
            state.mark("sharepoint", source_id, "failed", detail=f"re-fetch failed: {type(e).__name__}: {str(e)[:2000]}")
            stats["failed"] += 1
            stats["errors"].append({"item": item_id, "type": "file", "error": str(e)[:1000]})
            continue

        if "folder" in item:
            # Folder listing failures need a full discovery pass to find their
            # (possibly still-unlisted) children - not handled by this fast path.
            stats["errors"].append({"item": item.get("name", item_id), "type": "folder", "error": "folder retry needs a full discovery pass"})
            continue

        parent_id = (item.get("parentReference") or {}).get("id")
        target_parent = "root" if parent_id == root_id else state.target("sharepoint", f"{source_drive_id}:{parent_id}")
        if not target_parent:
            state.mark("sharepoint", source_id, "failed", detail="parent folder not migrated yet - run a full discovery pass")
            stats["still_missing_parent"] += 1
            continue

        file_work.append(_FileWork(item, source_id, item.get("name", "unknown"), target_parent))

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)
    run_workers(
        file_work,
        lambda work: _transfer_one_file(source_graph, target_graph, source_drive_id, target_drive_id, state, stats, stats_lock, work, max_file_size_mb),
        max_workers=content_concurrency,
        gate=content_gate,
    )

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats


def copy_library(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    state: StateStore,
    dry_run: bool = False,
    max_file_size_mb: int = 20000,  # 20 GB limit for individual files - larger files must be migrated manually
    content_concurrency: int = 4,
) -> dict[str, Any]:
    """Copy files and folders from source to target library.

    Runs in stages (ARCHITECHTURE_UPGRADE.md ss8): a sequential discovery pass
    that also creates/reuses folders (parent must exist before its children),
    followed by concurrent bounded file-content workers. A file or folder
    failure is isolated to that item and never blocks the others.

    Args:
        source_graph: Source tenant Graph client
        target_graph: Target tenant Graph client
        source_drive_id: Source library ID
        target_drive_id: Target library ID
        state: Migration state tracker
        dry_run: Preview without making changes
        max_file_size_mb: Skip files larger than this (default: 20000 MB = 20 GB)
        content_concurrency: max concurrent file downloads/uploads (default: 4)

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
                                    f"/drives/{target_drive_id}/items/{target_parent}:/{encode_graph_path_segment(item_name)}"
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
                            
                            discover_and_prepare_folders(item_id, target_folder_id, depth + 1)
                        except (GraphError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
                            state.mark("sharepoint", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:2000]}")
                            stats["failed"] += 1
                            stats["errors"].append({"item": item_name, "type": "folder", "error": f"{type(e).__name__}: {str(e)[:1000]}"})
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
            print(f"  ✗ Error walking folder {source_parent}: {error_type}: {str(e)[:200]}")
            stats["errors"].append({
                "folder": source_parent,
                "error": f"{error_type}: {str(e)[:1000]}"
            })
            # Always re-raise: a failed children listing means this entire subtree
            # was skipped, not just one item. Swallowing it here (previously only
            # Connection/timeout errors were re-raised) let persistent 401s and
            # other listing failures pass silently - the folder above still got
            # marked "completed" with stats["failed"] left at 0, so future resumes
            # skipped it forever and the source/target counts silently diverged.
            # Re-raising lets it bubble to the nearest per-folder try (marks that
            # folder "failed" -> stats["failed"] > 0) or, for the root walk, all
            # the way out of copy_library so batch.py's with_retry retries the
            # whole library instead of falsely marking it done.
            raise

    discover_and_prepare_folders("root", "root")

    if dry_run:
        return stats

    # Stage 4: concurrent bounded file-content transfer
    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    def copy_one_file(work: _FileWork) -> None:
        _transfer_one_file(source_graph, target_graph, source_drive_id, target_drive_id, state, stats, stats_lock, work, max_file_size_mb)

    run_workers(file_work, copy_one_file, max_workers=content_concurrency, gate=content_gate)

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    stats["throttle_events"] = content_gate.throttle_events
    return stats
