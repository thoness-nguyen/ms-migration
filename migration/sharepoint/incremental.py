"""Incremental SharePoint library copy: only files created or modified since a
cutoff (plus items that failed in the previous full copy).

Used for a library that was fully migrated earlier and is too large to re-walk
(enabled per site with `modified_since:` in the SharePoint plan - see
orchestration.batch._run_batch_sharepoint). Unlike copy_library, a file that
already exists on the target IS overwritten: the source version is newer.

Discovery uses the drive delta feed (one flat, paged listing of every item)
instead of a recursive folder walk. Folder paths are rebuilt from parent ids,
because delta responses do not include parentReference.path.

Known limits (reported, not silently ignored): deletions on the source after the
cutoff are not propagated, and an item only MOVED/renamed after the cutoff
(without a content change) keeps its old target location.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError
from .migration import SIMPLE_UPLOAD_LIMIT, stream_copy_large_file
from .services import copy_item_permissions


def parse_since(value: str) -> datetime:
    """'2026-09-10' or a full ISO timestamp -> aware UTC datetime."""
    text = value.strip().replace("Z", "+00:00")
    if len(text) == 10:
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _stamp(item: dict[str, Any]) -> datetime | None:
    values = [item.get("lastModifiedDateTime"), (item.get("fileSystemInfo") or {}).get("lastModifiedDateTime"), item.get("createdDateTime")]
    parsed = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in values if v]
    return max(parsed) if parsed else None


def find_changed_files(
    source_graph: GraphClient,
    source_drive_id: str,
    since: datetime,
    state: StateStore,
    failed_key: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
    unresolved: list[dict[str, Any]] | None = None,
) -> tuple[list[tuple[dict[str, Any], str]], int]:
    """(changed files with their library-relative path, total items scanned).

    failed_key(item) -> (workload, checkpoint id) of the item's full-copy row, so a
    file that failed there is re-copied even if it is older than `since`
    (default: the SharePoint key). Changed files whose folder path cannot be
    rebuilt (e.g. inside a OneNote package) are appended to `unresolved` when
    given, so the caller can report them instead of losing them silently."""
    folders: dict[str, tuple[str, str | None]] = {}  # id -> (name, parent id)
    root_id: str | None = None
    files: list[dict[str, Any]] = []
    scanned = 0
    select = "id,name,file,folder,root,parentReference,size,lastModifiedDateTime,createdDateTime,fileSystemInfo,deleted"
    for item in source_graph.pages(f"/drives/{source_drive_id}/root/delta?$select={select}"):
        scanned += 1
        if item.get("deleted"):
            continue
        parent = (item.get("parentReference") or {}).get("id")
        if "root" in item:
            root_id = item["id"]
        elif "folder" in item:
            folders[item["id"]] = (item.get("name", ""), parent)
        elif "file" in item:
            files.append(item)

    def path_of(folder_id: str | None) -> str | None:
        parts: list[str] = []
        seen: set[str] = set()
        while folder_id and folder_id != root_id:
            if folder_id in seen or folder_id not in folders:
                return None  # orphan / unknown parent - reported as a failure
            seen.add(folder_id)
            name, folder_id = folders[folder_id]
            parts.append(name)
        return "/".join(reversed(parts))

    changed: list[tuple[dict[str, Any], str]] = []
    for item in files:
        stamp = _stamp(item)
        workload, key = failed_key(item) if failed_key else ("sharepoint", f"{source_drive_id}:{item['id']}")
        previously_failed = state.status(workload, key) == "failed"
        if not previously_failed and (stamp is None or stamp < since):
            continue
        folder_path = path_of((item.get("parentReference") or {}).get("id"))
        if folder_path is None:
            if unresolved is not None:
                unresolved.append(item)
            continue
        changed.append((item, folder_path))
    return changed, scanned


def copy_library_changes_since(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    state: StateStore,
    since: datetime,
    dry_run: bool = False,
    max_file_size_mb: int = 5000,
    content_concurrency: int = 4,
    permission_concurrency: int = 2,
) -> dict[str, Any]:
    stats: dict[str, Any] = {"mode": "incremental", "since": since.isoformat(), "scanned": 0, "changed": 0, "changed_bytes": 0,
                             "files_copied": 0, "folders_created": 0, "skipped": 0, "failed": 0, "oversized_skipped": 0, "errors": []}
    changed, stats["scanned"] = find_changed_files(source_graph, source_drive_id, since, state)
    stats["changed"] = len(changed)
    stats["changed_bytes"] = sum(item.get("size") or 0 for item, _ in changed)
    print(f"  [incremental] scanned {stats['scanned']} items, {len(changed)} file(s) changed since {since:%Y-%m-%d} ({stats['changed_bytes'] / 1024**3:.2f} GB)")
    if dry_run:
        return stats

    lock = threading.Lock()
    folder_cache: dict[str, str] = {"": "root"}
    folder_lock = threading.Lock()

    def ensure_folder(path: str) -> str:
        """Target folder id for a library-relative path, creating missing levels."""
        with folder_lock:
            if path in folder_cache:
                return folder_cache[path]
            parent_path, _, name = path.rpartition("/")
        parent_id = ensure_folder(parent_path)
        with folder_lock:
            if path in folder_cache:
                return folder_cache[path]
            try:
                folder_id = target_graph.request("GET", f"/drives/{target_drive_id}/root:/{quote(path)}?$select=id")["id"]
            except GraphError:
                try:
                    folder_id = target_graph.request("POST", f"/drives/{target_drive_id}/items/{parent_id}/children",
                                                     json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})["id"]
                    with lock:
                        stats["folders_created"] += 1
                except GraphError as error:
                    if error.status_code != 409:
                        raise
                    folder_id = target_graph.request("GET", f"/drives/{target_drive_id}/root:/{quote(path)}?$select=id")["id"]
            folder_cache[path] = folder_id
            return folder_id

    permission_work: list[tuple[str, str, str]] = []
    gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(gate.record_throttle)
    target_graph.set_throttle_hook(gate.record_throttle)

    def copy_one(work: tuple[dict[str, Any], str]) -> None:
        item, folder_path = work
        name = item.get("name", "unknown")
        key = f"{source_drive_id}:{item['id']}@{item.get('lastModifiedDateTime')}"
        if state.status("sharepoint-incremental", key) == "completed":
            with lock:
                stats["skipped"] += 1
            return
        try:
            size = item.get("size") or 0
            if size > max_file_size_mb * 1024 * 1024:
                state.mark("sharepoint-incremental", key, "completed", "oversized-skipped")
                with lock:
                    stats["oversized_skipped"] += 1
                return
            parent_id = ensure_folder(folder_path)
            if size > SIMPLE_UPLOAD_LIMIT:
                # Streamed, constant memory, resumable within the file.
                session = target_graph.request("POST", f"/drives/{target_drive_id}/items/{parent_id}:/{quote(name)}:/createUploadSession",
                                               json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}})
                target_item = stream_copy_large_file(source_graph, target_graph, source_drive_id, item["id"], size, session["uploadUrl"])
                copied_bytes = size
            else:
                response = source_graph.raw_request("GET", f"{source_graph.base_url}/drives/{source_drive_id}/items/{item['id']}/content", timeout=300)
                response.raise_for_status()
                data = response.content
                target_item = target_graph.request("PUT", f"/drives/{target_drive_id}/items/{parent_id}:/{quote(name)}:/content", data=data)
                copied_bytes = len(data)
            target_id = (target_item or {}).get("id")
            if not target_id:
                raise GraphError(f"upload of {folder_path}/{name} finished without returning the created item")
            state.mark("sharepoint-incremental", key, "completed", target_id, detail=f"{folder_path}/{name}")
            # The full-copy checkpoint for this item now points at the fresh copy too.
            state.mark("sharepoint", f"{source_drive_id}:{item['id']}", "completed", target_id)
            print(f"  [+] Updated: {folder_path}/{name} ({copied_bytes / 1024**2:.2f} MB)")
            with lock:
                stats["files_copied"] += 1
                if target_id:
                    permission_work.append((item["id"], target_id, name))
        except (GraphError, OSError, TypeError, ValueError, KeyError, AttributeError) as error:
            state.mark("sharepoint-incremental", key, "failed", detail=f"{type(error).__name__}: {str(error)[:180]}")
            with lock:
                stats["failed"] += 1
                stats["errors"].append({"item": f"{folder_path}/{name}", "error": f"{type(error).__name__}: {str(error)[:100]}"})

    run_workers(changed, copy_one, max_workers=content_concurrency, gate=gate)

    def grant(work: tuple[str, str, str]) -> None:
        try:
            copy_item_permissions(source_graph, target_graph, source_drive_id, target_drive_id, work[0], work[1])
        except (GraphError, OSError, TypeError, ValueError, KeyError, AttributeError) as error:
            with lock:
                stats["errors"].append({"item": work[2], "type": "permission", "error": str(error)[:150]})

    permission_gate = AdaptiveGate(initial=permission_concurrency, minimum=1, maximum=permission_concurrency)
    source_graph.set_throttle_hook(permission_gate.record_throttle)
    target_graph.set_throttle_hook(permission_gate.record_throttle)
    run_workers(permission_work, grant, max_workers=permission_concurrency, gate=permission_gate)
    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats
