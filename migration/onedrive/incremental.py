"""Incremental OneDrive copy: re-copy only files created or modified since a
cutoff (plus files that failed in an earlier full copy), for every user at once.

Enabled with `modified_since:` in the OneDrive mapping (see
orchestration.batch._run_batch_onedrive). Used after a full copy already ran:
a file that exists on the target IS overwritten, because the source version is
newer.

Pipeline (throughput):
1. Discovery - one drive delta listing per user (flat, paged; far fewer calls
   than the recursive folder walk of copy_drive), several users in parallel.
2. Copy - ONE worker pool + ONE AdaptiveGate over the changed files of all
   users, so small users don't leave workers idle and 429s from any user slow
   the whole pool (the GraphClient throttle hook is per client, so per-user
   pools running side by side would overwrite each other's hook).
   Large files are streamed piece by piece (constant memory, resumable within
   the file).

Checkpoints:
- `onedrive-incremental` per file, key `{source_user}:{item_id}@{lastModified}`:
  a later edit of the same file produces a new key, so it is copied again.
- on success the full-copy row `onedrive` `{source_user}:{item_id}` is pointed
  at the fresh target item too.
- `batch-onedrive-incremental` per user, key `{source_user}@{since}`: completed
  only when discovery finished and every changed file of that user succeeded;
  otherwise pending, and the next run re-enters the user.

Known limits (reported, not hidden): source deletions after the cutoff are not
propagated; an item only moved/renamed after the cutoff (no content change)
keeps its old target location; files whose folder path cannot be rebuilt
(e.g. inside a OneNote notebook package) are listed as `unresolved`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError
from ..sharepoint.incremental import find_changed_files
from ..sharepoint.migration import SIMPLE_UPLOAD_LIMIT, stream_copy_large_file
from .migration import _copy_item_permissions, _permission_status_for

FILE_WORKLOAD = "onedrive-incremental"
USER_WORKLOAD = "batch-onedrive-incremental"


@dataclass
class UserJob:
    user_key: str
    source_id: str
    target_id: str
    source_drive_id: str = ""
    target_drive_id: str = ""
    changed: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    scanned: int = 0
    discovery_error: str | None = None
    stats: dict[str, Any] = field(default_factory=lambda: {
        "files_copied": 0, "skipped": 0, "failed": 0, "folders_created": 0, "changed_bytes": 0, "errors": []})
    folder_cache: dict[str, str] = field(default_factory=lambda: {"": "root"})
    folder_lock: threading.Lock = field(default_factory=threading.Lock)


def user_key_for(job: UserJob, since: datetime) -> str:
    return f"{job.source_id}@{since.isoformat()}"


def _file_key(job: UserJob, item: dict[str, Any]) -> str:
    return f"{job.source_id}:{item['id']}@{item.get('lastModifiedDateTime')}"


def discover_user(source_graph: GraphClient, target_graph: GraphClient, job: UserJob, since: datetime, state: StateStore) -> None:
    """Fill job.changed / job.unresolved from the source drive delta feed."""
    try:
        job.source_drive_id = source_graph.request("GET", f"/users/{job.source_id}/drive?$select=id")["id"]
        job.target_drive_id = target_graph.request("GET", f"/users/{job.target_id}/drive?$select=id")["id"]
        job.changed, job.scanned = find_changed_files(
            source_graph, job.source_drive_id, since, state,
            failed_key=lambda item: ("onedrive", f"{job.source_id}:{item['id']}"),
            unresolved=job.unresolved,
        )
        job.stats["changed_bytes"] = sum(item.get("size") or 0 for item, _ in job.changed)
    except (GraphError, OSError, KeyError, TypeError, ValueError) as error:
        job.discovery_error = f"{type(error).__name__}: {str(error)[:300]}"


def _ensure_folder(target_graph: GraphClient, job: UserJob, path: str) -> str:
    """Target folder id for a drive-relative path, creating missing levels
    (check-before-create, conflictBehavior fail, 409 -> re-read)."""
    with job.folder_lock:
        if path in job.folder_cache:
            return job.folder_cache[path]
    parent_path, _, name = path.rpartition("/")
    parent_id = _ensure_folder(target_graph, job, parent_path)
    with job.folder_lock:
        if path in job.folder_cache:
            return job.folder_cache[path]
        lookup = f"/drives/{job.target_drive_id}/root:/{quote(path)}?$select=id"
        try:
            folder_id = target_graph.request("GET", lookup)["id"]
        except GraphError as error:
            if error.status_code != 404:
                raise
            try:
                folder_id = target_graph.request("POST", f"/drives/{job.target_drive_id}/items/{parent_id}/children",
                                                 json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})["id"]
                job.stats["folders_created"] += 1
            except GraphError as create_error:
                if create_error.status_code != 409:
                    raise
                folder_id = target_graph.request("GET", lookup)["id"]
        job.folder_cache[path] = folder_id
        return folder_id


def copy_one_changed_file(
    source_graph: GraphClient,
    target_graph: GraphClient,
    job: UserJob,
    item: dict[str, Any],
    folder_path: str,
    state: StateStore,
    lock: threading.Lock,
    user_map: dict[str, str] | None,
    migrate_permissions: bool,
    max_file_size_mb: int,
) -> None:
    name = item.get("name", "unknown")
    shown = f"{folder_path}/{name}" if folder_path else name
    key = _file_key(job, item)
    if state.status(FILE_WORKLOAD, key) == "completed":
        with lock:
            job.stats["skipped"] += 1
        return
    full_key = f"{job.source_id}:{item['id']}"
    # Copied before -> its sharing was already recreated (and an overwrite keeps
    # the target item and its permissions); re-inviting would spam users.
    copied_before = bool(state.target("onedrive", full_key))
    try:
        size = item.get("size") or 0
        if size > max_file_size_mb * 1024 * 1024:
            raise ValueError(f"file is {size / 1024**2:.0f} MB, above the {max_file_size_mb} MB limit")
        parent_id = _ensure_folder(target_graph, job, folder_path)
        target_path = f"/drives/{job.target_drive_id}/items/{parent_id}:/{quote(name)}:"
        if size > SIMPLE_UPLOAD_LIMIT:
            session = target_graph.request("POST", f"{target_path}/createUploadSession",
                                           json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}})
            target_item = stream_copy_large_file(source_graph, target_graph, job.source_drive_id, item["id"], size, session["uploadUrl"])
        else:
            response = source_graph.raw_request("GET", f"{source_graph.base_url}/drives/{job.source_drive_id}/items/{item['id']}/content", timeout=300)
            response.raise_for_status()
            target_item = target_graph.request("PUT", f"{target_path}/content", data=response.content)
        target_id = (target_item or {}).get("id")
        if not target_id:
            raise GraphError(f"upload of {shown} finished without returning the created item")
    except (GraphError, OSError, TypeError, ValueError, KeyError, AttributeError) as error:
        detail = f"{type(error).__name__}: {str(error)[:180]}"
        state.mark(FILE_WORKLOAD, key, "failed", detail=detail, user_key=job.user_key)
        with lock:
            job.stats["failed"] += 1
            job.stats["errors"].append({"item": shown, "error": detail[:150]})
        print(f"  [!] [{job.user_key}] Failed: {shown} - {detail[:150]}")
        return

    state.mark(FILE_WORKLOAD, key, "completed", target_id, detail=shown[:300], user_key=job.user_key)
    state.mark("onedrive", full_key, "completed", target_id, user_key=job.user_key)
    if migrate_permissions and user_map and not copied_before:
        try:
            perm_result = _copy_item_permissions(source_graph, target_graph, job.source_id, job.target_id, item["id"], target_id, user_map)
        except Exception as error:  # noqa: BLE001 - file is copied; report permissions separately
            perm_result = {"errors": [f"permissions failed: {error}"[:200]]}
        if perm_result["errors"]:
            with lock:
                job.stats.setdefault("permission_errors", []).append({"item": shown, "errors": perm_result["errors"]})
        state.mark("onedrive", full_key, "completed", target_id, user_key=job.user_key,
                   permission_status=_permission_status_for(perm_result))
    print(f"  [+] [{job.user_key}] Updated: {shown} ({(item.get('size') or 0) / 1024**2:.2f} MB)")
    with lock:
        job.stats["files_copied"] += 1


def copy_changes_since(
    source_graph: GraphClient,
    target_graph: GraphClient,
    jobs: list[UserJob],
    state: StateStore,
    since: datetime,
    dry_run: bool = False,
    content_concurrency: int = 8,
    discovery_concurrency: int = 4,
    user_map: dict[str, str] | None = None,
    migrate_permissions: bool = False,
    max_file_size_mb: int = 250_000,
) -> None:
    """Discover and copy changes for all jobs; results are left in job.stats."""
    def discover(job: UserJob) -> None:
        discover_user(source_graph, target_graph, job, since, state)
        status = f"FAILED ({job.discovery_error})" if job.discovery_error else (
            f"scanned {job.scanned} items, {len(job.changed)} changed ({job.stats['changed_bytes'] / 1024**3:.2f} GB)"
            + (f", {len(job.unresolved)} unresolved" if job.unresolved else ""))
        print(f"[onedrive {job.user_key}] since {since:%Y-%m-%d}: {status}")

    run_workers(jobs, discover, max_workers=discovery_concurrency)
    total = sum(len(job.changed) for job in jobs)
    total_bytes = sum(job.stats["changed_bytes"] for job in jobs)
    print(f"[onedrive] {total} changed file(s), {total_bytes / 1024**3:.2f} GB across {len(jobs)} user(s)")
    if dry_run:
        return

    work = [(job, item, path) for job in jobs if not job.discovery_error for item, path in job.changed]
    # Biggest first: a multi-GB file started last would otherwise become the tail.
    work.sort(key=lambda entry: entry[1].get("size") or 0, reverse=True)
    lock = threading.Lock()
    gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(gate.record_throttle)
    target_graph.set_throttle_hook(gate.record_throttle)
    try:
        outcomes = run_workers(
            work,
            lambda entry: copy_one_changed_file(source_graph, target_graph, entry[0], entry[1], entry[2], state, lock,
                                                user_map, migrate_permissions, max_file_size_mb),
            max_workers=content_concurrency,
            gate=gate,
        )
    finally:
        source_graph.set_throttle_hook(None)
        target_graph.set_throttle_hook(None)
    # copy_one_changed_file records its own failures; anything raised past it
    # (unexpected bug) must still count as a failure, never as success.
    for (job, item, path), _result, error in outcomes:
        if error is not None:
            if isinstance(error, KeyboardInterrupt):
                raise error
            detail = f"{type(error).__name__}: {str(error)[:180]}"
            state.mark(FILE_WORKLOAD, _file_key(job, item), "failed", detail=detail, user_key=job.user_key)
            with lock:
                job.stats["failed"] += 1
                job.stats["errors"].append({"item": f"{path}/{item.get('name')}", "error": detail[:150]})


def incomplete_reason(job: UserJob) -> str | None:
    if job.discovery_error:
        return f"discovery failed: {job.discovery_error}"
    if job.stats["failed"]:
        return f"{job.stats['failed']} file(s) failed"
    return None
