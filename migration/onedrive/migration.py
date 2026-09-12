from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from ..common.checkpoint import StateStore
from ..common.concurrency import AdaptiveGate, run_workers
from ..common.graph import GraphClient, GraphError

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
    """Recreate one item's sharing (direct grants + anyone/organization links) on the target.

    Returns counters plus `had_shared_permissions`: True as soon as any real
    (non-owner) grant was found on the source item, regardless of whether the
    recreation call on the target actually succeeded - this drives the
    checkpoint's `permission_status` ("shared" vs "private") for that item.
    """
    result: dict[str, Any] = {"links_created": 0, "invites_sent": 0, "invites_to_source_tenant": 0, "skipped": 0, "errors": [], "had_shared_permissions": False}
    try:
        permissions = list(source_graph.pages(f"/users/{source_user}/drive/items/{source_item_id}/permissions"))
    except GraphError as error:
        result["errors"].append(f"list permissions failed: {str(error)[:200]}")
        return result

    for permission in permissions:
        if permission.get("inheritedFrom"):
            # inherited from a parent folder - the target's own folder tree already covers this
            result["skipped"] += 1
            continue

        link = permission.get("link")
        if link:
            result["had_shared_permissions"] = True
            try:
                target_graph.request(
                    "POST",
                    f"/users/{target_user}/drive/items/{target_item_id}/createLink",
                    json={"type": link.get("type", "view"), "scope": link.get("scope", "anonymous")},
                )
                result["links_created"] += 1
            except GraphError as error:
                result["errors"].append(f"createLink failed: {str(error)[:200]}")
            continue

        identities = []
        granted = permission.get("grantedToV2") or permission.get("grantedTo")
        if granted:
            identities.append(granted)
        identities.extend(permission.get("grantedToIdentitiesV2") or permission.get("grantedToIdentities") or [])

        recipients: list[dict[str, str]] = []
        for identity in identities:
            user_identity = identity.get("user") or {}
            identity_id = user_identity.get("id")
            identity_email = user_identity.get("email") or user_identity.get("userPrincipalName")
            if identity_email and identity_email.lower() in (source_user.lower(), target_user.lower()):
                # the owner's own baseline permission - not a real share
                continue
            target_identity_id = user_map.get(identity_id) if identity_id else None
            if target_identity_id:
                recipients.append({"objectId": target_identity_id})
            elif identity_email:
                recipients.append({"email": identity_email})
                result["invites_to_source_tenant"] += 1
            else:
                result["errors"].append(f"no mapping or email for identity {identity_id}")

        if not recipients:
            result["skipped"] += 1
            continue

        result["had_shared_permissions"] = True
        try:
            target_graph.request(
                "POST",
                f"/users/{target_user}/drive/items/{target_item_id}/invite",
                json={"requireSignIn": False, "sendInvitation": False, "roles": permission.get("roles", ["read"]), "recipients": recipients},
            )
            result["invites_sent"] += 1
        except GraphError as error:
            result["errors"].append(f"invite failed: {str(error)[:200]}")

    return result


def _permission_status_for(perm_result: dict[str, Any]) -> str:
    return "shared" if perm_result.get("had_shared_permissions") else "private"


def _transfer_one_file(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_user: str,
    target_user: str,
    item: dict[str, Any],
    source_id: str,
    target_parent: str,
    state: StateStore,
    user_key: str | None,
    user_map: dict[str, str] | None,
    migrate_permissions: bool,
    stats: dict[str, Any],
    stats_lock: threading.Lock,
) -> None:
    who = f"[{user_key}] " if user_key else ""
    item_name = item["name"]
    # skip file if an item with the same name already exists in the target folder
    try:
        existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{item_name}")
        state.mark("onedrive", source_id, "completed", existing.get("id") if existing else None, user_key=user_key)
        with stats_lock:
            stats["files_copied"] += 1
        return
    except GraphError:
        pass

    try:
        content = source_graph.raw_request("GET", f"{source_graph.base_url}/users/{source_user}/drive/items/{item['id']}/content", timeout=120)
        content.raise_for_status()
        data = content.content
        file_size_mb = len(data) / (1024 * 1024)
        if len(data) <= 4 * 1024 * 1024:
            target = target_graph.request("PUT", f"/users/{target_user}/drive/items/{target_parent}:/{item_name}:/content", data=data)
        else:
            session = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}:/{item_name}:/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": item_name}})
            upload_url = session["uploadUrl"]
            target = None
            for chunk_index, start in enumerate(range(0, len(data), CHUNK_SIZE), start=1):
                chunk = data[start:start + CHUNK_SIZE]
                response = target_graph.raw_request("PUT", upload_url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, data=chunk, timeout=120)
                response.raise_for_status()
                target = response.json() if response.content else target
                if chunk_index % 5 == 0:
                    uploaded_mb = (start + len(chunk)) / (1024 * 1024)
                    pct = int(100 * (start + len(chunk)) / len(data))
                    print(f"    >> {who}{item_name}: {pct}% ({uploaded_mb:.1f} MB / {file_size_mb:.1f} MB)")
    except (GraphError, OSError) as error:
        state.mark("onedrive", source_id, "failed", detail=str(error)[:200], user_key=user_key)
        with stats_lock:
            stats["failed"] = stats.get("failed", 0) + 1
        return

    target_item_id = target.get("id") if target else None
    state.mark("onedrive", source_id, "completed", target_item_id, user_key=user_key)

    permission_status = None
    if migrate_permissions and user_map and target_item_id:
        perm_result = _copy_item_permissions(source_graph, target_graph, source_user, target_user, item["id"], target_item_id, user_map)
        if perm_result["errors"]:
            with stats_lock:
                stats.setdefault("permission_errors", []).append({"item": item_name, "errors": perm_result["errors"]})
        permission_status = _permission_status_for(perm_result)
        state.mark("onedrive", source_id, "completed", target_item_id, user_key=user_key, permission_status=permission_status)

    suffix = " with 'shared' permission." if permission_status == "shared" else ""
    print(f"  [+] {who}Copied file: {item_name} ({file_size_mb:.2f} MB){suffix}")
    with stats_lock:
        stats["files_copied"] += 1
        if permission_status == "shared":
            stats["permission_shared"] = stats.get("permission_shared", 0) + 1
        elif permission_status == "private":
            stats["permission_private"] = stats.get("permission_private", 0) + 1


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
    workers - same pipeline as sharepoint/migration.py's copy_library()."""
    stats: dict[str, Any] = {"files_copied": 0}
    stats_lock = threading.Lock()
    file_work: list[_FileWork] = []
    who = f"[{user_key}] " if user_key else ""

    def discover_and_prepare_folders(source_parent: str, target_parent: str) -> None:
        for item in source_graph.pages(f"/users/{source_user}/drive/items/{source_parent}/children" if source_parent != "root" else f"/users/{source_user}/drive/root/children"):
            item_id = item["id"]
            source_id = f"{source_user}:{item_id}"
            if state.status("onedrive", source_id) == "completed":
                if "folder" in item and not dry_run:
                    # still recurse to pick up new files added since last run
                    stored = state.target("onedrive", source_id)
                    if stored:
                        discover_and_prepare_folders(item_id, stored)
                continue
            if "folder" in item:
                if not dry_run:
                    # Idempotency check: reuse an existing target folder instead of creating
                    # a duplicate / risking data loss via conflictBehavior=replace
                    target_folder_id = None
                    try:
                        existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{item['name']}")
                        target_folder_id = existing["id"]
                        state.mark("onedrive", source_id, "completed", target_folder_id, user_key=user_key)
                    except GraphError:
                        pass  # Folder doesn't exist yet, proceed with creation
                    if target_folder_id is None:
                        created = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}/children", json={"name": item["name"], "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                        target_folder_id = created["id"]
                        state.mark("onedrive", source_id, "completed", target_folder_id, user_key=user_key)
                    if migrate_permissions and user_map:
                        perm_result = _copy_item_permissions(source_graph, target_graph, source_user, target_user, item_id, target_folder_id, user_map)
                        if perm_result["errors"]:
                            with stats_lock:
                                stats.setdefault("permission_errors", []).append({"item": item["name"], "errors": perm_result["errors"]})
                        state.mark("onedrive", source_id, "completed", target_folder_id, user_key=user_key, permission_status=_permission_status_for(perm_result))
                    print(f"  [+] {who}Created folder: {item['name']}")
                    discover_and_prepare_folders(item_id, target_folder_id)
                else:
                    discover_and_prepare_folders(item_id, target_parent)
                continue
            if dry_run:
                with stats_lock:
                    stats["files_copied"] += 1
                continue
            file_work.append(_FileWork(item, source_id, target_parent))

    discover_and_prepare_folders("root", "root")

    if dry_run:
        return stats

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    def copy_one_file(work: _FileWork) -> None:
        _transfer_one_file(source_graph, target_graph, source_user, target_user, work.item, work.source_id, work.target_parent, state, user_key, user_map, migrate_permissions, stats, stats_lock)

    run_workers(file_work, copy_one_file, max_workers=content_concurrency, gate=content_gate)

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats


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
    """Re-attempt only items previously marked "failed" for this drive, without
    re-walking the whole folder tree - used by `batch --retry-failed-only`."""
    stats: dict[str, Any] = {"files_copied": 0}
    stats_lock = threading.Lock()
    prefix = f"{source_user}:"
    failed_ids = [source_id for source_id in state.list_ids("onedrive", "failed") if source_id.startswith(prefix)]
    file_work: list[_FileWork] = []

    for source_id in failed_ids:
        item_id = source_id[len(prefix):]
        try:
            item = source_graph.request("GET", f"/users/{source_user}/drive/items/{item_id}?$select=id,name,folder,parentReference")
        except GraphError:
            continue
        if "folder" in item:
            continue
        parent_ref = item.get("parentReference") or {}
        target_parent = state.target("onedrive", f"{source_user}:{parent_ref.get('id')}") or "root"
        file_work.append(_FileWork(item, source_id, target_parent))

    if not file_work:
        return stats

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    def copy_one_file(work: _FileWork) -> None:
        _transfer_one_file(source_graph, target_graph, source_user, target_user, work.item, work.source_id, work.target_parent, state, user_key, user_map, migrate_permissions, stats, stats_lock)

    run_workers(file_work, copy_one_file, max_workers=content_concurrency, gate=content_gate)

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return stats
