from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from html import escape
from typing import Any

from ..common.checkpoint import StateStore
from ..common.graph import GraphClient, GraphError

# matches both <attachment id="X"></attachment> and <attachment id="X"/>
_ATTACHMENT_TAG = re.compile(r'<attachment\s+id="([^"]+)"(?:\s*/>|>\s*</attachment>)', re.DOTALL)


def _timestamp(value: str, last: datetime | None) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    if last and parsed <= last:
        parsed = last.replace(microsecond=last.microsecond + 1000)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _import_body(message: dict[str, Any]) -> dict[str, str]:
    body = message.get("body") or {"contentType": "html", "content": ""}
    raw_attachments = message.get("attachments") or []
    if not raw_attachments:
        return {"contentType": body.get("contentType", "html"), "content": body.get("content", "")}

    att_map = {a["id"]: a for a in raw_attachments if a.get("id")}

    def _resolve(match: re.Match) -> str:  # type: ignore[type-arg]
        att = att_map.get(match.group(1))
        if not att:
            return ""
        ctype = att.get("contentType", "")
        if ctype == "forwardedMessageReference":
            try:
                fwd = json.loads(att.get("content") or "{}")
                return fwd.get("originalMessageContent", "")
            except json.JSONDecodeError:
                return ""
        url = att.get("contentUrl")
        name = att.get("name") or url
        if url:
            return f'<p>Attachment: <a href="{escape(url)}">{escape(str(name))}</a></p>'
        return ""

    resolved = _ATTACHMENT_TAG.sub(_resolve, body.get("content", ""))
    return {"contentType": body.get("contentType", "html"), "content": resolved}


def _import_payload(message: dict[str, Any], user_map: dict[str, str], last: datetime | None) -> dict[str, Any]:
    source_from = (message.get("from") or {}).get("user") or {}
    source_id = source_from.get("id")
    target_id = user_map.get(source_id)
    if not target_id:
        raise ValueError(f"No target identity mapping exists for message sender {source_id}")
    payload: dict[str, Any] = {
        "createdDateTime": _timestamp(message["createdDateTime"], last),
        "from": {"user": {"id": target_id, "userIdentityType": "aadUser"}},
        "body": _import_body(message),
    }
    hosted_contents = message.get("hostedContents")
    if hosted_contents:
        payload["hostedContents"] = hosted_contents
    unsupported: list[str] = []
    # attachments whose placeholder couldn't be resolved (no contentUrl, not forwardedMessageReference)
    unresolvable = [
        a for a in (message.get("attachments") or [])
        if a.get("contentType") != "forwardedMessageReference" and not a.get("contentUrl")
    ]
    if unresolvable:
        unsupported.append("attachments")
    if message.get("reactions"):
        unsupported.append("reactions")
    if message.get("mentions"):
        unsupported.append("mentions")
    if unsupported:
        payload["_unsupported"] = unsupported
    return payload


def import_channel_messages(graph: GraphClient, target_team: str, target_channel: str, messages: list[dict[str, Any]], state: StateStore, dry_run: bool = False) -> int:
    ordered = sorted(messages, key=lambda message: message["createdDateTime"])
    last: datetime | None = None
    imported = 0
    if not dry_run and state.status("teams-channel", target_channel) != "started":
        graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/startMigration")
        state.mark("teams-channel", target_channel, "started")
    for message in ordered:
        source_id = str(message["id"])
        if state.status("teams-message", source_id) == "completed":
            continue
        payload = {
            "createdDateTime": _timestamp(message["createdDateTime"], last),
            "from": message.get("from"),
            "body": _import_body(message),
        }
        if message.get("hostedContents"):
            payload["hostedContents"] = message["hostedContents"]
        if not dry_run:
            graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/messages", json=payload)
            state.mark("teams-message", source_id, "completed")
        last = datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
        imported += 1
    if not dry_run:
        graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/completeMigration")
        state.mark("teams-channel", target_channel, "completed")
    return imported


def extract_chat(graph: GraphClient, chat_id: str) -> dict[str, Any]:
    chat = graph.request("GET", f"/chats/{chat_id}")
    members = list(graph.pages(f"/chats/{chat_id}/members"))
    messages = list(graph.pages(f"/chats/{chat_id}/messages"))
    return {"chat": chat, "members": members, "messages": messages}


def target_member(source_member: dict[str, Any], user_map: dict[str, str]) -> dict[str, Any]:
    source_id = source_member.get("userId")
    target_id = user_map.get(source_id)
    if not target_id:
        raise ValueError(f"No target identity mapping exists for source user {source_id}")
    return {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "roles": ["owner"],
        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{target_id}')",
    }


def import_chat(graph: GraphClient, bundle: dict[str, Any], user_map: dict[str, str], state: StateStore, dry_run: bool = False, stats: dict[str, int] | None = None) -> tuple[str, int]:
    source_chat_id = bundle["chat"]["id"]
    target_chat_id = state.target("teams-chat", source_chat_id)
    if not target_chat_id:
        chat_type = bundle["chat"].get("chatType")
        if chat_type not in {"oneOnOne", "group"}:
            raise ValueError(f"Unsupported chat type: {chat_type}")
        members = [target_member(member, user_map) for member in bundle["members"]]
        payload: dict[str, Any] = {"chatType": chat_type, "members": members}
        if chat_type == "group" and bundle["chat"].get("topic"):
            payload["topic"] = bundle["chat"]["topic"]
        if dry_run:
            target_chat_id = f"dry-run:{source_chat_id}"
        else:
            target_chat_id = graph.request("POST", "/chats", json=payload)["id"]
        if not dry_run:
            state.mark("teams-chat", source_chat_id, "created", target_chat_id)
    messages = sorted(bundle["messages"], key=lambda message: message["createdDateTime"])
    last: datetime | None = None
    imported = 0
    if not dry_run and state.status("teams-chat", source_chat_id) != "started":
        graph.request(
            "POST",
            f"/chats/{target_chat_id}/startMigration",
            json={"conversationCreationDateTime": bundle["chat"]["createdDateTime"]},
        )
        state.mark("teams-chat", source_chat_id, "started", target_chat_id)
    for message in messages:
        source_id = str(message["id"])
        if state.status("teams-chat-message", source_id) == "completed":
            continue
        source_from = (message.get("from") or {}).get("user") or {}
        if not source_from.get("id"):
            if stats is not None:
                stats["skipped_system"] = stats.get("skipped_system", 0) + 1
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail=f"Unsupported message type: {message.get('messageType', 'unknown')}")
            continue
        if message.get("deletedDateTime"):
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail="deleted")
            continue
        payload = _import_payload(message, user_map, last)
        unsupported = payload.pop("_unsupported", [])
        if stats is not None:
            stats["unsupported_features"] = stats.get("unsupported_features", 0) + len(unsupported)
        if not dry_run:
            graph.request("POST", f"/chats/{target_chat_id}/messages", json=payload)
            state.mark("teams-chat-message", source_id, "completed")
        last = datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
        imported += 1
        if stats is not None:
            stats["imported"] = stats.get("imported", 0) + 1
    if not dry_run:
        graph.request("POST", f"/chats/{target_chat_id}/completeMigration")
        # PATCH (405) is unsupported; delete+re-add each member with full history for group chats
        if bundle["chat"].get("chatType") == "group":
            existing = list(graph.pages(f"/chats/{target_chat_id}/members"))
            for member in existing:
                member_id = member.get("id")
                user_id = member.get("userId")
                if not member_id or not user_id:
                    continue
                try:
                    graph.request("DELETE", f"/chats/{target_chat_id}/members/{member_id}")
                    graph.request("POST", f"/chats/{target_chat_id}/members", json={
                        "@odata.type": "#microsoft.graph.aadUserConversationMember",
                        "roles": member.get("roles", ["owner"]),
                        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user_id}')",
                        "visibleHistoryStartDateTime": "0001-01-01T00:00:00Z",
                    })
                except GraphError:
                    pass
        state.mark("teams-chat", source_chat_id, "completed", target_chat_id)
    return target_chat_id, imported
