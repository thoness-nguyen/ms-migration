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


def _copy_item_permissions(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    source_item_id: str,
    target_item_id: str,
    user_map: dict[str, str],
) -> dict[str, Any]:
    """Recreate one OneDrive item's sharing on the target: direct user grants
    (specific user permissions / "shared with me" is just the other side of
    the same grant) and sharing links (anyone / organization). Permissions
    the item only has via folder inheritance are skipped on purpose - the
    target's own folder tree already provides that access, and re-applying
    it per-file would just create redundant unique permissions.
    """
    result: dict[str, Any] = {"links_created": 0, "invites_sent": 0, "skipped": 0, "errors": []}
    try:
        permissions = list(source_graph.pages(f"/users/{source_user}/drive/items/{source_item_id}/permissions"))
    except GraphError as e:
        result["errors"].append(f"list permissions failed: {type(e).__name__}: {str(e)[:500]}")
        return result

    for perm in permissions:
        if perm.get("inheritedFrom"):
            result["skipped"] += 1
            continue
        try:
            link = perm.get("link")
            if link:
                # Anonymous ("Anyone") and organization-scope sharing links.
                target_graph.request(
                    "POST",
                    f"/users/{target_user}/drive/items/{target_item_id}/createLink",
                    json={"type": link.get("type", "view"), "scope": link.get("scope", "organization")},
                )
                result["links_created"] += 1
                continue

            granted = perm.get("grantedToV2") or {}
            identities = perm.get("grantedToIdentitiesV2") or ([granted] if granted else [])
            recipients = []
            for identity in identities:
                source_identity_id = (identity.get("user") or {}).get("id")
                target_identity_id = user_map.get(source_identity_id) if source_identity_id else None
                if not target_identity_id:
                    result["errors"].append(f"no target mapping for permission grantee {source_identity_id}")
                    continue
                recipients.append({"objectId": target_identity_id})
            if not recipients:
                result["skipped"] += 1
                continue
            # Direct, specific-user permission grant (not a link).
            target_graph.request(
                "POST",
                f"/users/{target_user}/drive/items/{target_item_id}/invite",
                json={"requireSignIn": True, "sendInvitation": False, "roles": perm.get("roles", ["read"]), "recipients": recipients},
            )
            result["invites_sent"] += 1
        except (GraphError, TypeError, ValueError, KeyError) as e:
            result["errors"].append(f"{type(e).__name__}: {str(e)[:500]}")
    return result


def _transfer_one_file(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    stats: dict[str, Any],
    stats_lock: threading.Lock,
    work: _FileWork,
    user_key: str | None = None,
    user_map: dict[str, str] | None = None,
    migrate_permissions: bool = False,
) -> None:
    """Download one file from source and upload it to target, recording the
    outcome in `state`/`stats`. Shared by the full-tree copy (copy_drive) and
    the targeted retry (retry_failed_onedrive_files)."""
    item, source_id, target_parent = work.item, work.source_id, work.target_parent
    target_item_id: str | None = None
    try:
        # skip file if an item with the same name already exists in the target folder
        try:
            existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}")
            target_item_id = existing.get("id") if existing else None
            state.mark("onedrive", source_id, "completed", target_item_id, user_key=user_key)
            with stats_lock:
                stats["skipped"] += 1
            return
        except GraphError:
            pass
        item_name = item.get("name", "unknown")
        who = f"[{user_key}] " if user_key else ""
        content = source_graph.raw_request("GET", f"{source_graph.base_url}/users/{source_user}/drive/items/{item['id']}/content", timeout=120)
        content.raise_for_status()
        data = content.content
        file_size_mb = len(data) / (1024 * 1024)
        if len(data) <= 4 * 1024 * 1024:
            target = target_graph.request("PUT", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}:/content", data=data)
        else:
            session = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}:/{encode_graph_path_segment(item['name'])}:/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": item["name"]}})
            upload_url = session["uploadUrl"]
            target = None
            for chunk_num, start in enumerate(range(0, len(data), CHUNK_SIZE), 1):
                chunk = data[start:start + CHUNK_SIZE]
                response = target_graph.raw_request("PUT", upload_url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, data=chunk, timeout=120)
                response.raise_for_status()
                target = response.json() if response.content else target
                if chunk_num % 5 == 0:
                    pct = min(100, int(100 * (start + len(chunk)) / len(data)))
                    print(f"    >> {who}{item_name}: {pct}% ({(start + len(chunk)) / (1024*1024):.1f} MB / {file_size_mb:.1f} MB)")
        target_item_id = target.get("id") if target else None
        state.mark("onedrive", source_id, "completed", target_item_id, user_key=user_key)
        print(f"  [+] {who}Copied file: {item_name} ({file_size_mb:.2f} MB)")
        with stats_lock:
            stats["files_copied"] += 1
    except (GraphError, OSError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError) as e:
        state.mark("onedrive", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:2000]}", user_key=user_key)
        with stats_lock:
            stats["failed"] += 1
            stats["errors"].append({"item": item.get("name", "unknown"), "error": f"{type(e).__name__}: {str(e)[:1000]}"})
        return

    if migrate_permissions and user_map and target_item_id:
        perm_result = _copy_item_permissions(source_graph, target_graph, source_user, target_user, item["id"], target_item_id, user_map)
        if perm_result["errors"]:
            with stats_lock:
                stats.setdefault("permission_errors", []).append({"item": item.get("name", "unknown"), "errors": perm_result["errors"]})


def retry_failed_onedrive_files(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    content_concurrency: int = 4,
    user_key: str | None = None,
    user_map: dict[str, str] | None = None,
    migrate_permissions: bool = False,
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
            state.mark("onedrive", source_id, "failed", detail=f"re-fetch failed: {type(e).__name__}: {str(e)[:2000]}", user_key=user_key)
            stats["failed"] += 1
            stats["errors"].append({"item": item_id, "error": str(e)[:1000]})
            continue

        if "folder" in item:
            stats["errors"].append({"item": item.get("name", item_id), "type": "folder", "error": "folder retry needs a full discovery pass"})
            continue

        parent_id = (item.get("parentReference") or {}).get("id")
        target_parent = "root" if parent_id == root_id else state.target("onedrive", f"{source_user}:{parent_id}")
        if not target_parent:
            state.mark("onedrive", source_id, "failed", detail="parent folder not migrated yet - run a full discovery pass", user_key=user_key)
            stats["still_missing_parent"] += 1
            continue

        file_work.append(_FileWork(item, source_id, target_parent))

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)
    run_workers(
        file_work,
        lambda work: _transfer_one_file(source_graph, target_graph, source_user, target_user, state, stats, stats_lock, work, user_key=user_key, user_map=user_map, migrate_permissions=migrate_permissions),
        max_workers=content_concurrency,
        gate=content_gate,
    )
    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats


def copy_drive(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    state: StateStore,
    dry_run: bool = False,
    content_concurrency: int = 4,
    user_key: str | None = None,
    user_map: dict[str, str] | None = None,
    migrate_permissions: bool = False,
) -> dict[str, Any]:
    """Copy a user's OneDrive. Sequential discovery + folder pre-creation
    (parent must exist before child), then concurrent bounded file-content
    workers - same pipeline as sharepoint/migration.py's copy_library().

    `user_key` is the mapping-file key for this user (e.g. "thanh.nguyen") -
    stamped onto every checkpoint row so status/validation can be queried per
    user. `migrate_permissions`/`user_map` opt into recreating each file's
    sharing (direct grants + anyone/organization links) on the target; off
    by default since it adds a permissions API call per item.
    """
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
                                state.mark("onedrive", source_id, "completed", target_folder_id, user_key=user_key)
                            except GraphError:
                                pass  # Folder doesn't exist yet, proceed with creation
                            if target_folder_id is None:
                                created = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}/children", json={"name": item["name"], "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                                target_folder_id = created["id"]
                                state.mark("onedrive", source_id, "completed", target_folder_id, user_key=user_key)
                                print(f"  [+] {f'[{user_key}] ' if user_key else ''}Created folder: {item['name']}")
                            if migrate_permissions and user_map:
                                perm_result = _copy_item_permissions(source_graph, target_graph, source_user, target_user, item_id, target_folder_id, user_map)
                                if perm_result["errors"]:
                                    with stats_lock:
                                        stats.setdefault("permission_errors", []).append({"item": item.get("name", "unknown"), "errors": perm_result["errors"]})
                            discover_and_prepare_folders(item_id, target_folder_id)
                        except (GraphError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
                            state.mark("onedrive", source_id, "failed", detail=f"{type(e).__name__}: {str(e)[:2000]}", user_key=user_key)
                            with stats_lock:
                                stats["failed"] += 1
                                stats["errors"].append({"item": item.get("name", "unknown"), "type": "folder", "error": f"{type(e).__name__}: {str(e)[:1000]}"})
                    else:
                        discover_and_prepare_folders(item_id, target_parent)
                    continue
                if dry_run:
                    with stats_lock:
                        stats["files_copied"] += 1
                    continue
                file_work.append(_FileWork(item, source_id, target_parent))
        except (GraphError, ConnectionError, ConnectionResetError, BrokenPipeError, TypeError, ValueError, KeyError, AttributeError, OSError) as e:
            print(f"  ✗ {f'[{user_key}] ' if user_key else ''}Error walking folder {source_parent}: {type(e).__name__}: {str(e)[:200]}")
            with stats_lock:
                stats["errors"].append({"folder": source_parent, "error": f"{type(e).__name__}: {str(e)[:1000]}"})
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
        lambda work: _transfer_one_file(source_graph, target_graph, source_user, target_user, state, stats, stats_lock, work, user_key=user_key, user_map=user_map, migrate_permissions=migrate_permissions),
        max_workers=content_concurrency,
        gate=content_gate,
    )

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats
