from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from html import escape
from typing import Any, Callable
from urllib.parse import unquote

from ..common.checkpoint import StateStore
from ..common.graph import GraphClient, GraphError

# matches both <attachment id="X"></attachment> and <attachment id="X"/>
_ATTACHMENT_TAG = re.compile(r'<attachment\s+id="([^"]+)"(?:\s*/>|>\s*</attachment>)', re.DOTALL)

# SharePoint/OneDrive personal-site attachment links, e.g.
# https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf
_SHAREPOINT_PERSONAL_URL = re.compile(r"https://[^/]+/personal/(?P<owner>[^/]+)/Documents/(?P<path>.+)$")

AttachmentResolver = Callable[[str], str]


def _upn_to_personal_segment(upn: str) -> str:
    """SharePoint/OneDrive personal-site URLs encode a UPN as <local>_<domain-with-dots-as-_>."""
    return re.sub(r"[.@]", "_", upn.lower())


def make_attachment_resolver(target_graph: GraphClient, upn_map: dict[str, str]) -> AttachmentResolver:
    """Best-effort resolver for message attachment links: if a source SharePoint/OneDrive
    personal-site link's owner is in upn_map, look up the same relative file path under the
    target-tenant owner's OneDrive (already migrated separately) and use its webUrl instead.
    Falls back to the original source link when the owner is unknown or the file isn't found
    on the target side - never raises, so a lookup miss never blocks message migration."""
    segment_to_target_upn = {_upn_to_personal_segment(source_upn): target_upn for source_upn, target_upn in upn_map.items()}
    cache: dict[str, str] = {}

    def resolve(url: str) -> str:
        if url in cache:
            return cache[url]
        match = _SHAREPOINT_PERSONAL_URL.match(url)
        if not match:
            cache[url] = url
            return url
        target_upn = segment_to_target_upn.get(match.group("owner").lower())
        if not target_upn:
            cache[url] = url
            return url
        relative_path = unquote(match.group("path"))
        try:
            item = target_graph.request("GET", f"/users/{target_upn}/drive/root:/{relative_path}")
        except GraphError:
            item = None
        target_url = item.get("webUrl") if isinstance(item, dict) else None
        cache[url] = target_url or url
        return cache[url]

    return resolve


def _timestamp(
    value: str,
    last: datetime | None,
    minimum: datetime | None = None,
) -> str:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ).astimezone(timezone.utc)

    if minimum and parsed <= minimum:
        parsed = minimum + timedelta(milliseconds=1)

    if last and parsed <= last:
        parsed = last + timedelta(milliseconds=1)

    return parsed.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _import_body(message: dict[str, Any], attachment_resolver: AttachmentResolver | None = None) -> dict[str, str]:
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
        if url and attachment_resolver:
            url = attachment_resolver(url)
        name = att.get("name") or url
        if url:
            return f'<p>Attachment: <a href="{escape(url)}">{escape(str(name))}</a></p>'
        return ""

    resolved = _ATTACHMENT_TAG.sub(_resolve, body.get("content", ""))
    return {"contentType": body.get("contentType", "html"), "content": resolved}


def _import_payload(message: dict[str, Any], user_map: dict[str, str], last: datetime | None, attachment_resolver: AttachmentResolver | None = None) -> dict[str, Any]:
    source_from = (message.get("from") or {}).get("user") or {}
    source_id = source_from.get("id")
    target_id = user_map.get(source_id)
    if not target_id:
        raise ValueError(f"No target identity mapping exists for message sender {source_id}")
    payload: dict[str, Any] = {
        "createdDateTime": _timestamp(message["createdDateTime"], last),
        "from": {"user": {"id": target_id, "userIdentityType": "aadUser"}},
        "body": _import_body(message, attachment_resolver),
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

def _is_migration_window_closed(error: GraphError) -> bool:
    """True for the Graph 403 raised when a channel/chat is no longer in migration
    mode (e.g. completeMigration already ran) - message-level writes then require
    createdDateTime >= the thread's real creation time, so old timestamps are rejected."""
    message = str(error)
    return "MessageWritesBlocked" in message or "is less than thread creation time" in message


def _is_duplicate_message(error: GraphError) -> bool:
    """True for the Graph 409 raised when a message with the same identity already
    exists on the target thread - happens when a prior run's POST succeeded but the
    local checkpoint db doesn't know it (e.g. checkpoint reset/lost mid-migration, or
    the process crashed after the write but before the checkpoint mark). Safe to treat
    as already-migrated rather than failing the whole chat/channel."""
    message = str(error)
    return "(409)" in message and ("CreateConflictException" in message or '"code":"Conflict"' in message)


def create_backdated_channel(graph: GraphClient, target_team_id: str, display_name: str, membership_type: str, earliest_message_iso: str) -> str:
    """Provisions a replacement channel whose createdDateTime is set safely before
    the earliest historical message. A channel's createdDateTime can't be changed
    after creation, so if an existing target channel was provisioned with "now" as
    its creation time, no historical message can ever be migrated into it - the only
    fix is a new, properly backdated channel (see _is_migration_window_closed)."""
    earliest = datetime.fromisoformat(earliest_message_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    backdated = (earliest - timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    payload = {"displayName": display_name, "membershipType": membership_type, "createdDateTime": backdated}
    result = graph.request("POST", f"/teams/{target_team_id}/channels", json=payload)
    return result["id"]


def _channel_already_has_messages(graph: GraphClient, target_team: str, target_channel: str) -> bool:
    """Best-effort check for messages already present on the target channel -
    guards against duplicate imports when the local checkpoint db was lost/reset
    but the target channel was already migrated in a prior run."""
    try:
        existing = graph.request("GET", f"/teams/{target_team}/channels/{target_channel}/messages?$top=1")
    except GraphError:
        return False
    values = existing.get("value") if isinstance(existing, dict) else None
    return bool(values)


def import_channel_messages(graph: GraphClient, target_team: str, target_channel: str, messages: list[dict[str, Any]], state: StateStore, dry_run: bool = False, user_map: dict[str, str] | None = None, attachment_resolver: AttachmentResolver | None = None) -> int:
    # user_map is optional: when omitted, the source "from" identity is sent as-is
    # (only valid if the caller already sends target-tenant ids). When provided,
    # senders without a target mapping are skipped instead of failing the batch -
    # Graph rejects channel messages whose sender isn't in the token's tenant.
    ordered = sorted(messages, key=lambda message: message["createdDateTime"])
    total = len(ordered)
    last: datetime | None = None
    imported = 0
    skipped_no_mapping = 0
    if state.status("teams-channel", target_channel) == "completed":
        print(f"  [=] channel {target_channel}: already completed - skipping")
        return 0
    if (
        not dry_run
        and state.status("teams-channel", target_channel) is None
        and _channel_already_has_messages(graph, target_team, target_channel)
    ):
        state.mark("teams-channel", target_channel, "completed", detail="target channel already had messages - skipped to avoid duplicates", teams_type="channel")
        print(f"  [=] channel {target_channel}: target already had messages - skipped to avoid duplicates")
        return 0
    print(f"  [+] channel {target_channel}: starting migration ({total} messages)")
    if not dry_run and state.status("teams-channel", target_channel) != "started":
        try:
            graph.request(
                "POST",
                f"/teams/{target_team}/channels/{target_channel}/startMigration"
            )
        except GraphError as error:
            if "already in migration mode" not in str(error):
                raise
            print(f"  [!] channel {target_channel}: already in migration mode; resuming")
        state.mark("teams-channel", target_channel, "started", teams_type="channel",)
    for message in ordered:
        source_id = str(message["id"])
        if state.status("channel-message", source_id) == "completed":
            continue
        from_field = message.get("from")
        if user_map is not None:
            sender_id = ((message.get("from") or {}).get("user") or {}).get("id")
            target_sender_id = user_map.get(sender_id) if sender_id else None
            if not target_sender_id:
                skipped_no_mapping += 1
                if not dry_run:
                    state.mark("channel-message", source_id, "skipped", detail=f"sender {sender_id} has no target mapping", teams_type="channel_message")
                continue
            from_field = {"user": {"id": target_sender_id, "userIdentityType": "aadUser"}}
        payload = {
            "createdDateTime": _timestamp(message["createdDateTime"], last),
            "from": from_field,
            "body": _import_body(message, attachment_resolver),
        }
        if message.get("hostedContents"):
            payload["hostedContents"] = message["hostedContents"]
        if not dry_run:
            try:
                graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/messages", json=payload)
            except GraphError as error:
                if _is_duplicate_message(error):
                    print(f"  [!] channel {target_channel}: message {source_id} already exists on target - marking completed")
                elif _is_migration_window_closed(error):
                    raise GraphError(
                        f"Channel {target_channel} is no longer accepting historical (migration-mode) "
                        "timestamps - it was likely already fully migrated in a prior run (completeMigration "
                        "already ran) or wasn't provisioned in migration mode. Verify target channel content "
                        f"before retrying. {imported}/{total} messages were imported before this happened. "
                        f"Original error: {error}"
                    ) from error
                else:
                    raise
            state.mark("channel-message", source_id, "completed", teams_type="channel_message")
        last = datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
        imported += 1
        if total and (imported % 20 == 0 or imported == total):
            pct = int(imported / total * 100)
            print(f"    >> channel {target_channel}: {imported}/{total} messages migrated ({pct}%)")
    if not dry_run:
        graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/completeMigration")
        state.mark("teams-channel", target_channel, "completed", teams_type="channel")
    skip_suffix = f", {skipped_no_mapping} skipped (no target mapping)" if skipped_no_mapping else ""
    print(f"  [+] channel {target_channel}: completed ({imported}/{total} messages migrated{skip_suffix})")
    return imported


def extract_chat(graph: GraphClient, chat_id: str) -> dict[str, Any]:
    chat = graph.request("GET", f"/chats/{chat_id}")
    members = list(graph.pages(f"/chats/{chat_id}/members"))
    messages = list(graph.pages(f"/chats/{chat_id}/messages"))
    return {"chat": chat, "members": members, "messages": messages}


def invite_guest_user(target_graph: GraphClient, source_upn: str, display_name: str | None = None, redirect_url: str = "https://myapps.microsoft.com") -> str | None:
    """Best-effort B2B guest invite for a source-tenant user with no target-tenant
    account, so chats/messages can still reference them instead of being skipped.
    Requires the target token to have Invitation.ReadWrite.All consented - any
    failure (missing permission, throttling, already-exists, etc.) returns None so
    callers can fall back to skipping that user rather than failing the whole chat."""
    payload: dict[str, Any] = {
        "invitedUserEmailAddress": source_upn,
        "inviteRedirectUrl": redirect_url,
        "sendInvitationMessage": False,
    }
    if display_name:
        payload["invitedUserDisplayName"] = display_name
    try:
        result = target_graph.request("POST", "/invitations", json=payload)
    except GraphError as error:
        print(f"  [!] Guest invite failed for {source_upn}: {error}")
        return None
    invited_user = result.get("invitedUser") if isinstance(result, dict) else None
    return invited_user.get("id") if isinstance(invited_user, dict) else None


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

def _migration_conversation_creation_time(value: str) -> str:
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        - timedelta(seconds=1)
    )
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")

def import_chat(graph: GraphClient, bundle: dict[str, Any], user_map: dict[str, str], state: StateStore, dry_run: bool = False, stats: dict[str, int] | None = None, user_key: str | None = None, attachment_resolver: AttachmentResolver | None = None) -> tuple[str, int]:
    source_chat_id = bundle["chat"]["id"]
    chat_type = bundle["chat"].get("chatType")
    who = f"[{user_key}] " if user_key else ""
    target_chat_id = state.target("teams-chat", source_chat_id)
    if not target_chat_id:
        chat_type = bundle["chat"].get("chatType")
        if chat_type not in {"oneOnOne", "group"}:
            raise ValueError(f"Unsupported chat type: {chat_type}")
        members: list[dict[str, Any]] = []
        unmapped_members: list[str] = []
        for member in bundle["members"]:
            try:
                members.append(target_member(member, user_map))
            except ValueError:
                unmapped_members.append(str(member.get("userId")))
        if chat_type == "oneOnOne":
            # A 1:1 chat needs both parties to exist in the target tenant.
            if unmapped_members:
                raise ValueError(f"Missing target mapping for source user(s): {', '.join(unmapped_members)}")
        else:
            # Group chats can tolerate some source members having no target
            # account (e.g. source tenant has more users than target) -
            # skip them instead of failing the whole chat migration.
            if len(members) < 2:
                raise ValueError(f"Not enough target-mapped members to create chat (no target mapping for: {', '.join(unmapped_members)})")
            if unmapped_members:
                print(f"  [!] {who}Skipping {len(unmapped_members)} member(s) with no target mapping: {', '.join(unmapped_members)}")
        payload: dict[str, Any] = {"chatType": chat_type, "members": members}
        if chat_type == "group" and bundle["chat"].get("topic"):
            payload["topic"] = bundle["chat"]["topic"]
        if dry_run:
            target_chat_id = f"dry-run:{source_chat_id}"
        else:
            target_chat_id = graph.request("POST", "/chats", json=payload)["id"]
        if not dry_run:
            state.mark("teams-chat", source_chat_id, "created", target_chat_id, user_key=user_key, teams_type=chat_type)
            print(f"  [+] {who}Created chat: {target_chat_id}")
    messages = sorted(bundle["messages"], key=lambda message: message["createdDateTime"])
    total = len(messages)
    last: datetime | None = None
    imported = 0
    if not dry_run and state.status("teams-chat", source_chat_id) != "started":
        try:
            graph.request(
                "POST",
                f"/chats/{target_chat_id}/startMigration",
                json={"conversationCreationDateTime": _migration_conversation_creation_time(
                    bundle["chat"]["createdDateTime"]
            )},
            )
        except GraphError as error:
            if "already in migration mode" not in str(error):
                raise
            print(f"  [!] {who}chat {source_chat_id}: already in migration mode; resuming")
        state.mark("teams-chat", source_chat_id, "started", target_chat_id, user_key=user_key, teams_type=chat_type)
    for message in messages:
        source_id = str(message["id"])
        if state.status("teams-chat-message", source_id) == "completed":
            continue
        source_from = (message.get("from") or {}).get("user") or {}
        if not source_from.get("id"):
            if stats is not None:
                stats["skipped_system"] = stats.get("skipped_system", 0) + 1
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail=f"Unsupported message type: {message.get('messageType', 'unknown')}", user_key=user_key, teams_type=chat_type)
            continue
        if message.get("deletedDateTime"):
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail="deleted", user_key=user_key, teams_type=chat_type)
            continue
        try:
            payload = _import_payload(message, user_map, last, attachment_resolver)
        except ValueError as error:
            # Sender has no target mapping (e.g. member skipped above) - skip
            # just this message instead of failing the whole chat migration.
            if stats is not None:
                stats["skipped_no_mapping"] = stats.get("skipped_no_mapping", 0) + 1
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail=str(error), user_key=user_key, teams_type=chat_type)
            continue
        unsupported = payload.pop("_unsupported", [])
        if stats is not None:
            stats["unsupported_features"] = stats.get("unsupported_features", 0) + len(unsupported)
        if not dry_run:
            try:
                graph.request("POST", f"/chats/{target_chat_id}/messages", json=payload)
            except GraphError as error:
                if _is_duplicate_message(error):
                    print(f"  [!] {who}chat {source_chat_id}: message {source_id} already exists on target - marking completed")
                elif _is_migration_window_closed(error):
                    raise GraphError(
                        f"Chat {source_chat_id} is no longer accepting historical (migration-mode) "
                        "timestamps - it was likely already fully migrated in a prior run (completeMigration "
                        "already ran) or the resumed startMigration call didn't reopen it. Verify target chat "
                        f"content before retrying. {imported}/{total} messages were imported before this happened. "
                        f"Original error: {error}"
                    ) from error
                else:
                    raise
            state.mark("teams-chat-message", source_id, "completed", user_key=user_key, teams_type=chat_type)
        last = datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
        imported += 1
        if stats is not None:
            stats["imported"] = stats.get("imported", 0) + 1
        if imported % 20 == 0:
            pct = int(imported / total * 100) if total else 100
            print(f"    >> {who}chat {source_chat_id}: {imported}/{total} messages migrated ({pct}%)")
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
        state.mark("teams-chat", source_chat_id, "completed", target_chat_id, user_key=user_key, teams_type=chat_type)
        print(f"  [+] {who}Completed chat migration: {imported} messages")
    return target_chat_id, imported

