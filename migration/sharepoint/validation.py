from __future__ import annotations

from typing import Any

from ..common.graph import GraphClient, GraphError


def validate_library_migration(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
) -> dict[str, Any]:
    """Validate that source and target libraries match.
    
    Args:
        source_graph: Source tenant Graph client
        target_graph: Target tenant Graph client
        source_drive_id: Source library ID
        target_drive_id: Target library ID
        
    Returns:
        Validation report with file/folder counts and discrepancies
    """
    report = {
        "source_files": 0,
        "target_files": 0,
        "source_folders": 0,
        "target_folders": 0,
        "missing_files": [],
        "status": "unknown",
        "match": False,
    }
    
    try:
        def count_source(item_id: str = "root") -> tuple[int, int]:
            files, folders = 0, 0
            path = (
                f"/drives/{source_drive_id}/root/children"
                if item_id == "root"
                else f"/drives/{source_drive_id}/items/{item_id}/children"
            )
            for item in source_graph.pages(path):
                if "folder" in item:
                    folders += 1
                    sub_files, sub_folders = count_source(item["id"])
                    files += sub_files
                    folders += sub_folders
                else:
                    files += 1
            return files, folders
        
        def count_target(item_id: str = "root") -> tuple[int, int]:
            files, folders = 0, 0
            path = (
                f"/drives/{target_drive_id}/root/children"
                if item_id == "root"
                else f"/drives/{target_drive_id}/items/{item_id}/children"
            )
            for item in target_graph.pages(path):
                if "folder" in item:
                    folders += 1
                    sub_files, sub_folders = count_target(item["id"])
                    files += sub_files
                    folders += sub_folders
                else:
                    files += 1
            return files, folders
        
        source_files, source_folders = count_source()
        target_files, target_folders = count_target()
        
        report["source_files"] = source_files
        report["source_folders"] = source_folders
        report["target_files"] = target_files
        report["target_folders"] = target_folders
        
        file_match = source_files == target_files
        folder_match = source_folders == target_folders
        report["match"] = file_match and folder_match
        report["status"] = "success" if report["match"] else "mismatch"
        
        if report["match"]:
            print(f"[OK] Validation passed: {source_files} files, {source_folders} folders")
        else:
            print(f"⚠ Mismatch: source {source_files}F/{source_folders}D, target {target_files}F/{target_folders}D")
        
    except GraphError as e:
        report["status"] = "error"
        report["error"] = str(e)
        print(f"✗ Validation failed: {e}")
    
    return report
