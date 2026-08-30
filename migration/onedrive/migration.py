from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
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


def copy_drive(source_graph: GraphClient, target_graph: GraphClient, source_user: str, target_user: str, state: StateStore, dry_run: bool = False, content_concurrency: int = 4) -> int:
    """Copy a user's OneDrive. Sequential discovery + folder pre-creation
    (parent must exist before child), then concurrent bounded file-content
    workers - same pipeline as sharepoint/migration.py's copy_library()."""
    copied = 0
    copied_lock = threading.Lock()
    file_work: list[_FileWork] = []

    def discover_and_prepare_folders(source_parent: str, target_parent: str) -> None:
        nonlocal copied
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
                        state.mark("onedrive", source_id, "completed", target_folder_id)
                    except GraphError:
                        pass  # Folder doesn't exist yet, proceed with creation
                    if target_folder_id is None:
                        created = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}/children", json={"name": item["name"], "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
                        target_folder_id = created["id"]
                        state.mark("onedrive", source_id, "completed", target_folder_id)
                    discover_and_prepare_folders(item_id, target_folder_id)
                else:
                    discover_and_prepare_folders(item_id, target_parent)
                continue
            if dry_run:
                copied += 1
                continue
            file_work.append(_FileWork(item, source_id, target_parent))

    discover_and_prepare_folders("root", "root")

    if dry_run:
        return copied

    content_gate = AdaptiveGate(initial=content_concurrency, minimum=1, maximum=content_concurrency)
    source_graph.set_throttle_hook(content_gate.record_throttle)
    target_graph.set_throttle_hook(content_gate.record_throttle)

    def copy_one_file(work: _FileWork) -> None:
        nonlocal copied
        item, source_id, target_parent = work.item, work.source_id, work.target_parent
        # skip file if an item with the same name already exists in the target folder
        try:
            existing = target_graph.request("GET", f"/users/{target_user}/drive/items/{target_parent}:/{item['name']}")
            state.mark("onedrive", source_id, "completed", existing.get("id") if existing else None)
            with copied_lock:
                copied += 1
            return
        except GraphError:
            pass
        content = source_graph.raw_request("GET", f"{source_graph.base_url}/users/{source_user}/drive/items/{item['id']}/content", timeout=120)
        content.raise_for_status()
        data = content.content
        if len(data) <= 4 * 1024 * 1024:
            target = target_graph.request("PUT", f"/users/{target_user}/drive/items/{target_parent}:/{item['name']}:/content", data=data)
        else:
            session = target_graph.request("POST", f"/users/{target_user}/drive/items/{target_parent}:/{item['name']}:/createUploadSession", json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": item["name"]}})
            upload_url = session["uploadUrl"]
            target = None
            for start in range(0, len(data), CHUNK_SIZE):
                chunk = data[start:start + CHUNK_SIZE]
                response = target_graph.raw_request("PUT", upload_url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"}, data=chunk, timeout=120)
                response.raise_for_status()
                target = response.json() if response.content else target
        state.mark("onedrive", source_id, "completed", target.get("id") if target else None)
        with copied_lock:
            copied += 1

    run_workers(file_work, copy_one_file, max_workers=content_concurrency, gate=content_gate)

    source_graph.set_throttle_hook(None)
    target_graph.set_throttle_hook(None)
    return copied
