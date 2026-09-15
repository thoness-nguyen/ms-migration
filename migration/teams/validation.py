from __future__ import annotations

from typing import Any

from ..common.graph import GraphClient, GraphError


def validate_chat_migration(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_chat_id: str,
    target_chat_id: str,
) -> dict[str, Any]:
    """Post-migration integrity check for one Teams chat.

    Teams chat migration doesn't copy binary file content (attachments become
    links back to the source, see teams/services.py's _import_body), so
    "file integrity" doesn't apply here - the equivalent check is message and
    member counts matching, since a dropped message is the chat-migration
    analogue of a corrupted/missing file.
    """
    report: dict[str, Any] = {"source_chat_id": source_chat_id, "target_chat_id": target_chat_id}
    try:
        source_messages = list(source_graph.pages(f"/chats/{source_chat_id}/messages"))
        target_messages = list(target_graph.pages(f"/chats/{target_chat_id}/messages"))
        source_members = list(source_graph.pages(f"/chats/{source_chat_id}/members"))
        target_members = list(target_graph.pages(f"/chats/{target_chat_id}/members"))
    except GraphError as e:
        report["status"] = "error"
        report["error"] = str(e)[:500]
        return report

    # System/deleted messages and ones with no user sender are intentionally
    # skipped during import (see teams/services.py's import_chat), so compare
    # against the subset that was actually eligible to be imported.
    importable = [m for m in source_messages if not m.get("deletedDateTime") and (m.get("from") or {}).get("user")]
    report.update({
        "source_messages": len(importable),
        "target_messages": len(target_messages),
        "source_members": len(source_members),
        "target_members": len(target_members),
    })
    report["match"] = report["source_messages"] == report["target_messages"] and report["source_members"] == report["target_members"]
    report["status"] = "verified" if report["match"] else "mismatch"
    return report
