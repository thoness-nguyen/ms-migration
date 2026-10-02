from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable
from urllib.parse import unquote

from ..common.checkpoint import StateStore
from ..common.graph import GraphClient, GraphError
from ..onedrive.migration import CHUNK_SIZE, _copy_item_permissions
from ..sharepoint.services import copy_item_permissions as _copy_site_item_permissions
from ..sharepoint.services import get_site_libraries, resolve_site_url

# One short retry to absorb a brief replication lag between a chat's
# startMigration call (backdating createdDateTime) and that backdate becoming
# visible on GET - see _wait_for_chat_migration_window. If it's still not
# visible after this, the backdate itself failed to apply (not just delayed):
# startMigration can only be called once per chat, so further retries at any
# backoff would never succeed and would only waste time and Graph quota.
_MIGRATION_WINDOW_RETRIES = 2
_MIGRATION_WINDOW_INITIAL_DELAY = 2.0

# matches both <attachment id="X"></attachment> and <attachment id="X"/>
_ATTACHMENT_TAG = re.compile(r'<attachment\s+id="([^"]+)"(?:\s*/>|>\s*</attachment>)', re.DOTALL)

# SharePoint/OneDrive personal-site attachment links, e.g.
# https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf
_SHAREPOINT_PERSONAL_URL = re.compile(r"https://[^/]+/personal/(?P<owner>[^/]+)/Documents/(?P<path>.+)$")

# SharePoint TEAM SITE document library links - what channel-message attachments
# actually use (they live in the team's associated SharePoint site, not a
# personal OneDrive), e.g.
# https://contoso.sharepoint.com/sites/HarbourPD/Shared Documents/foo.pdf
_SHAREPOINT_SITE_LIBRARY_URL = re.compile(r"https://(?P<host>[^/]+)/sites/(?P<site>[^/]+)/(?P<library>[^/]+)/(?P<path>.+)$")

# Inline images embedded in a chatMessage's HTML body reference the SOURCE
# tenant's Graph hostedContents endpoint, e.g.
# https://graph.microsoft.com/v1.0/teams/{team}/channels/{channel}/messages/{id}/hostedContents/{contentId}/$value
# - unreachable once the message is re-created on the target tenant.
_IMG_HOSTED_CONTENT_SRC = re.compile(r'(<img\b[^>]*\bsrc=")(https://graph\.microsoft\.com/[^"]*/hostedContents/[^"/?]+/\$value[^"]*)(")', re.IGNORECASE)

AttachmentResolver = Callable[[str], str]
ImageResolver = Callable[[str], tuple[bytes, str] | None]


def _upn_to_personal_segment(upn: str) -> str:
    """SharePoint/OneDrive personal-site URLs encode a UPN as <local>_<domain-with-dots-as-_>."""
    return re.sub(r"[.@]", "_", upn.lower())


def make_attachment_resolver(source_graph: GraphClient, target_graph: GraphClient, upn_map: dict[str, str], user_id_map: dict[str, str] | None = None, site_map: dict[str, str] | None = None) -> AttachmentResolver:
    """Best-effort resolver for message attachment links - handles both link shapes
    Teams messages actually use:
    - personal OneDrive links (1:1/group chats): if the owner is in upn_map, look up
      the same relative path under the target-tenant owner's OneDrive, migrating the
      file on demand (with permissions via user_id_map) if it isn't there yet.
    - SharePoint TEAM SITE library links (channel messages - these live in the team's
      associated SharePoint site, not a personal drive): if the site is in site_map
      (source "host/sites/site" -> target_site_id), resolve both drives and migrate
      the file on demand (with link-share permissions) the same way.
    Falls back to the original source link whenever the owner/site is unknown or the
    on-demand migration itself fails - never raises."""
    segment_to_target_upn = {_upn_to_personal_segment(source_upn): target_upn for source_upn, target_upn in upn_map.items()}
    segment_to_source_upn = {_upn_to_personal_segment(source_upn): source_upn for source_upn in upn_map}
    site_map = site_map or {}
    cache: dict[str, str] = {}
    drive_cache: dict[str, str | None] = {}

    def library_drive_id(graph: GraphClient, site_id: str, library_name: str) -> str | None:
        cache_key = f"{site_id}:{library_name.lower()}"
        if cache_key in drive_cache:
            return drive_cache[cache_key]
        drive_id = None
        try:
            libraries = get_site_libraries(graph, site_id)
            for drive in libraries:
                if (drive.get("name") or "").lower() == library_name.lower():
                    drive_id = drive["id"]
                    break
            if drive_id is None and libraries:
                # library name didn't match exactly (e.g. localized "Documents"
                # vs "Shared Documents") - fall back to the first library.
                drive_id = libraries[0]["id"]
        except GraphError:
            drive_id = None
        drive_cache[cache_key] = drive_id
        return drive_id

    def resolve_personal(match: re.Match) -> str:  # type: ignore[type-arg]
        owner_segment = match.group("owner").lower()
        target_upn = segment_to_target_upn.get(owner_segment)
        if not target_upn:
            return match.group(0)
        relative_path = unquote(match.group("path"))
        try:
            item = target_graph.request("GET", f"/users/{target_upn}/drive/root:/{relative_path}")
        except GraphError:
            item = None
        if item is None:
            source_upn = segment_to_source_upn.get(owner_segment)
            if source_upn:
                item = _migrate_missing_attachment(source_graph, target_graph, source_upn, target_upn, relative_path, user_id_map)
        target_url = item.get("webUrl") if isinstance(item, dict) else None
        return target_url or match.group(0)

    def resolve_site_library(match: re.Match) -> str:  # type: ignore[type-arg]
        host = match.group("host")
        site = match.group("site")
        library = unquote(match.group("library"))
        relative_path = unquote(match.group("path"))
        target_site_id = site_map.get(f"{host}/sites/{site}".lower())
        if not target_site_id:
            return match.group(0)
        try:
            source_site_id = resolve_site_url(source_graph, f"https://{host}/sites/{site}")
        except GraphError:
            return match.group(0)
        source_drive_id = library_drive_id(source_graph, source_site_id, library)
        target_drive_id = library_drive_id(target_graph, target_site_id, library)
        if not source_drive_id or not target_drive_id:
            return match.group(0)
        try:
            item = target_graph.request("GET", f"/drives/{target_drive_id}/root:/{relative_path}")
        except GraphError:
            item = None
        if item is None:
            item = _migrate_missing_site_file(source_graph, target_graph, source_drive_id, target_drive_id, relative_path)
        target_url = item.get("webUrl") if isinstance(item, dict) else None
        return target_url or match.group(0)

    def resolve(url: str) -> str:
        if url in cache:
            return cache[url]
        personal_match = _SHAREPOINT_PERSONAL_URL.match(url)
        if personal_match:
            cache[url] = resolve_personal(personal_match)
            return cache[url]
        library_match = _SHAREPOINT_SITE_LIBRARY_URL.match(url)
        if library_match:
            cache[url] = resolve_site_library(library_match)
            return cache[url]
        cache[url] = url
        return url

    return resolve


def _migrate_missing_site_file(source_graph: GraphClient, target_graph: GraphClient, source_drive_id: str, target_drive_id: str, relative_path: str) -> dict[str, Any] | None:
    """On-demand single-file copy for a channel-message attachment that lives in a
    SharePoint TEAM SITE library (addressed by drive id, unlike personal OneDrive
    attachments which are addressed by user UPN) - mirrors _migrate_missing_attachment's
    small/large-file upload split, and best-effort copies its sharing links (see
    sharepoint.services.copy_item_permissions - link shares only, no identity mapping
    needed at the site-library level). Returns None (never raises) on any failure."""
    try:
        source_item = source_graph.request("GET", f"/drives/{source_drive_id}/root:/{relative_path}")
    except GraphError:
        return None
    try:
        response = source_graph.raw_request("GET", f"{source_graph.base_url}/drives/{source_drive_id}/root:/{relative_path}:/content", timeout=120)
        response.raise_for_status()
        data = response.content
    except (GraphError, OSError):
        return None
    try:
        target_item: dict[str, Any] | None
        if len(data) <= 4 * 1024 * 1024:
            target_item = target_graph.request("PUT", f"/drives/{target_drive_id}/root:/{relative_path}:/content", data=data)
        else:
            session = target_graph.request(
                "POST",
                f"/drives/{target_drive_id}/root:/{relative_path}:/createUploadSession",
                json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            )
            upload_url = session["uploadUrl"]
            target_item = None
            for start in range(0, len(data), CHUNK_SIZE):
                chunk = data[start:start + CHUNK_SIZE]
                chunk_response = target_graph.raw_request(
                    "PUT",
                    upload_url,
                    headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"},
                    data=chunk,
                    timeout=120,
                )
                chunk_response.raise_for_status()
                if chunk_response.content:
                    target_item = chunk_response.json()
    except (GraphError, OSError):
        return None
    if isinstance(source_item, dict) and isinstance(target_item, dict) and target_item.get("id"):
        try:
            _copy_site_item_permissions(source_graph, target_graph, source_drive_id, target_drive_id, source_item["id"], target_item["id"])
        except GraphError:
            pass
    return target_item


def _migrate_missing_attachment(source_graph: GraphClient, target_graph: GraphClient, source_upn: str, target_upn: str, relative_path: str, user_id_map: dict[str, str] | None = None) -> dict[str, Any] | None:
    """On-demand single-file copy for an attachment link that doesn't exist on the
    target yet - mirrors onedrive.migration's small/large-file upload split, just
    addressed by path (under the same relative path as the source) instead of by
    parent item id. Also best-effort copies the source item's sharing permissions
    (see _copy_item_permissions) once uploaded, so a shared attachment doesn't
    silently become private on the target. Returns None (never raises) on any
    failure so the caller falls back to the original source link."""
    try:
        source_item = source_graph.request("GET", f"/users/{source_upn}/drive/root:/{relative_path}")
    except GraphError:
        return None
    try:
        response = source_graph.raw_request("GET", f"{source_graph.base_url}/users/{source_upn}/drive/root:/{relative_path}:/content", timeout=120)
        response.raise_for_status()
        data = response.content
    except (GraphError, OSError):
        return None
    try:
        target_item: dict[str, Any] | None
        if len(data) <= 4 * 1024 * 1024:
            target_item = target_graph.request("PUT", f"/users/{target_upn}/drive/root:/{relative_path}:/content", data=data)
        else:
            session = target_graph.request(
                "POST",
                f"/users/{target_upn}/drive/root:/{relative_path}:/createUploadSession",
                json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            )
            upload_url = session["uploadUrl"]
            target_item = None
            for start in range(0, len(data), CHUNK_SIZE):
                chunk = data[start:start + CHUNK_SIZE]
                chunk_response = target_graph.raw_request(
                    "PUT",
                    upload_url,
                    headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{len(data)}"},
                    data=chunk,
                    timeout=120,
                )
                chunk_response.raise_for_status()
                if chunk_response.content:
                    target_item = chunk_response.json()
    except (GraphError, OSError):
        return None
    if user_id_map and isinstance(source_item, dict) and isinstance(target_item, dict) and target_item.get("id"):
        try:
            _copy_item_permissions(source_graph, target_graph, source_upn, target_upn, source_item["id"], target_item["id"], user_id_map)
        except GraphError:
            pass
    return target_item


# chatMessageHostedContent only accepts these three - Graph returns a 400
# ("Unsupported content type in hostedContent") for anything else (gif, webp,
# bmp, generic application/octet-stream, a mislabeled/missing Content-Type, ...).
_ALLOWED_HOSTED_IMAGE_TYPES = {"image/jpg", "image/jpeg", "image/png"}

# Graph rejects the whole message POST if the request body exceeds 4194304
# bytes (4 MiB) - base64 inflates raw bytes by ~4/3, and the body still needs
# room for the surrounding JSON/HTML, so cap the RAW image size well under
# that ceiling rather than right at the base64-encoded limit.
_MAX_INLINE_IMAGE_BYTES = 3 * 1024 * 1024

# A single message can embed multiple inline images - each may pass the
# per-image cap above yet still overflow the 4 MiB body limit combined, so
# also cap the RAW total across every image resolved for one message.
_MAX_TOTAL_HOSTED_CONTENT_BYTES = 3 * 1024 * 1024


def _sniff_image_content_type(data: bytes) -> str | None:
    """Identify the real image format from its magic bytes - the source Graph
    response's Content-Type header isn't always one of the three allowed values
    even when the underlying bytes are a supported format."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def make_image_resolver(source_graph: GraphClient) -> ImageResolver:
    """Downloads a source-tenant inline chatMessage image (its Graph hostedContents
    $value URL is embedded straight in the message HTML and only reachable with the
    SOURCE tenant's credentials) so it can be re-uploaded as a new hostedContents
    entry on the target message. Never raises - a download failure, or an image
    whose format Teams can't host inline (only jpg/jpeg/png), leaves the original
    <img> tag untouched instead of failing the whole message."""
    cache: dict[str, tuple[bytes, str] | None] = {}

    def resolve(url: str) -> tuple[bytes, str] | None:
        if url in cache:
            return cache[url]
        try:
            response = source_graph.raw_request("GET", url, timeout=60)
            response.raise_for_status()
            data = response.content
            if len(data) > _MAX_INLINE_IMAGE_BYTES:
                # Too large to inline as base64 hostedContents without risking
                # the 4 MiB Graph request-body limit - leave the <img> tag
                # pointing at the (unreachable) source URL rather than 400ing
                # the entire message.
                result = None
                cache[url] = result
                return result
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type not in _ALLOWED_HOSTED_IMAGE_TYPES:
                content_type = _sniff_image_content_type(data)
            result = (data, content_type) if content_type else None
        except (GraphError, OSError):
            result = None
        cache[url] = result
        return result

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


def _import_body(message: dict[str, Any], attachment_resolver: AttachmentResolver | None = None, image_resolver: ImageResolver | None = None) -> dict[str, Any]:
    body = message.get("body") or {"contentType": "html", "content": ""}
    content = body.get("content", "")
    hosted_contents: list[dict[str, Any]] = []

    if image_resolver:
        counter = 0
        total_bytes = 0

        def _resolve_image(match: re.Match) -> str:  # type: ignore[type-arg]
            nonlocal counter, total_bytes
            resolved = image_resolver(match.group(2))
            if not resolved:
                return match.group(0)
            data, content_type = resolved
            if total_bytes + len(data) > _MAX_TOTAL_HOSTED_CONTENT_BYTES:
                # A single message can embed several inline images - even ones
                # under the per-image cap can together push the POST body past
                # Graph's 4 MiB limit, so cap the message's combined total too.
                return match.group(0)
            total_bytes += len(data)
            counter += 1
            temp_id = str(counter)
            hosted_contents.append({
                "@microsoft.graph.temporaryId": temp_id,
                "contentBytes": base64.b64encode(data).decode("ascii"),
                "contentType": content_type,
            })
            return f"{match.group(1)}../hostedContents/{temp_id}/$value{match.group(3)}"

        content = _IMG_HOSTED_CONTENT_SRC.sub(_resolve_image, content)

    raw_attachments = message.get("attachments") or []
    if not raw_attachments:
        # Image substitution rewrites an existing <img> tag's src attribute in
        # place (it never introduces new markup), so it's safe regardless of
        # contentType - only force "html" when it actually happened, to avoid
        # corrupting a genuinely plain-text message that happens to contain a
        # literal '<' or '&'.
        content_type = "html" if hosted_contents else body.get("contentType", "html")
        result: dict[str, Any] = {"contentType": content_type, "content": content}
        if hosted_contents:
            result["_hosted_contents"] = hosted_contents
        return result

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

    resolved = _ATTACHMENT_TAG.sub(_resolve, content)
    # An <attachment id="..."> tag is itself HTML markup, and _resolve always
    # substitutes it with a real <p><a>...</a></p> link (or drops it) - so the
    # resulting content is HTML regardless of what contentType the source
    # message claimed. Forcing it here is what fixes messages whose source
    # contentType was "text" (pure-attachment messages) rendering as literal,
    # escaped HTML instead of a clickable link on the target.
    result = {"contentType": "html", "content": resolved}
    if hosted_contents:
        result["_hosted_contents"] = hosted_contents
    return result


def _event_actor_name(user_ref: dict[str, Any] | None) -> str:
    user_ref = user_ref or {}
    return str(user_ref.get("displayName") or user_ref.get("id") or "unknown user")


def _event_detail_text(message: dict[str, Any]) -> str | None:
    """Renders a membership/topic-change system message (`from` is null,
    Graph never lets those be authored by a real user) as plain audit text,
    so the event survives migration instead of being silently dropped."""
    detail = message.get("eventDetail")
    if not detail:
        return None
    odata_type = str(detail.get("@odata.type", "")).rsplit(".", 1)[-1]
    initiator = _event_actor_name((detail.get("initiator") or {}).get("user"))
    members = ", ".join(_event_actor_name(m) for m in (detail.get("members") or [])) or "unknown user(s)"
    if odata_type == "membersAddedEventMessageDetail":
        return f"[Audit] {initiator} added {members} to the chat."
    if odata_type == "membersRemovedEventMessageDetail" or odata_type == "membersLeftEventMessageDetail":
        return f"[Audit] {initiator} removed {members} from the chat."
    if odata_type == "chatRenamedEventMessageDetail":
        new_topic = detail.get("chatDisplayName") or "(no topic)"
        return f'[Audit] {initiator} renamed the chat to "{new_topic}".'
    return f"[Audit] {odata_type or 'system event'} by {initiator}."


def _import_payload(message: dict[str, Any], user_map: dict[str, str], last: datetime | None, attachment_resolver: AttachmentResolver | None = None, image_resolver: ImageResolver | None = None) -> dict[str, Any]:
    source_from = (message.get("from") or {}).get("user") or {}
    source_id = source_from.get("id")
    target_id = user_map.get(source_id)
    if not target_id:
        raise ValueError(f"No target identity mapping exists for message sender {source_id}")
    body = _import_body(message, attachment_resolver, image_resolver)
    hosted_contents = body.pop("_hosted_contents", None)
    payload: dict[str, Any] = {
        "createdDateTime": _timestamp(message["createdDateTime"], last),
        "from": {"user": {"id": target_id, "userIdentityType": "aadUser"}},
        "body": body,
    }
    if hosted_contents:
        payload["hostedContents"] = hosted_contents
    elif message.get("hostedContents"):
        payload["hostedContents"] = message["hostedContents"]
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


def _fallback_identity_payload(body: dict[str, Any], sender_id: str, fallback_sender_id: str, created: str, last: datetime | None) -> dict[str, Any]:
    """Builds a message payload posted under a proxy identity for a sender with no
    target mapping - used only when include_unmapped_senders=True. Prefixes the
    body with a note so the misattribution is visible to anyone reading the target
    chat/channel later, instead of silently posting under someone else's name."""
    note = f"[Originally sent by {sender_id}, who has no target tenant account] "
    noted_body = dict(body)
    if noted_body.get("contentType") == "html":
        noted_body["content"] = f"<p><em>{note}</em></p>{noted_body.get('content', '')}"
    else:
        noted_body["content"] = f"{note}{noted_body.get('content', '')}"
    return {
        "createdDateTime": _timestamp(created, last),
        "from": {"user": {"id": fallback_sender_id, "userIdentityType": "aadUser"}},
        "body": noted_body,
    }


def _is_timestamp_too_early(error: GraphError) -> bool:
    """True for the Graph 403 raised when a chat/channel is still in migration mode
    but this message's createdDateTime is earlier than the thread's real (backdated)
    creation time (e.g. "MessageWritesBlocked-OriginalArrivalTime ... is less than
    thread creation time ...") - fixable by retrying with a later, non-historical
    timestamp. Must be checked before _is_migration_mode_closed: both reasons share
    the same "MessageWritesBlocked-" error prefix, only the suffix differs."""
    return "is less than thread creation time" in str(error)


def _is_migration_mode_closed(error: GraphError) -> bool:
    """True for the Graph 403 raised once completeMigration has already run for a
    chat/channel (e.g. "MessageWritesBlocked-Thread is not marked for import") - per
    Microsoft Graph, application-permission message writes (Teamwork.Migrate.All) are
    only accepted while the thread is in migration mode, so this is NOT fixable by
    adjusting the timestamp: every retry will fail identically, no matter what
    createdDateTime is used."""
    return "not marked for import" in str(error)



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
    fix is a new, properly backdated channel (see _is_timestamp_too_early)."""
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


def _channel_created_datetime(graph: GraphClient, target_team: str, target_channel: str) -> datetime | None:
    """Best-effort read of the target channel's actual createdDateTime - unlike
    chats, a channel's creation time can't be backdated after the fact via
    startMigration, so this is used purely for the non-historical fallback
    (see import_channel_messages) rather than to confirm a backdate propagated."""
    try:
        channel = graph.request("GET", f"/teams/{target_team}/channels/{target_channel}?$select=createdDateTime")
    except GraphError:
        return None
    value = channel.get("createdDateTime") if isinstance(channel, dict) else None
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def import_channel_messages(graph: GraphClient, target_team: str, target_channel: str, messages: list[dict[str, Any]], state: StateStore, dry_run: bool = False, user_map: dict[str, str] | None = None, attachment_resolver: AttachmentResolver | None = None, image_resolver: ImageResolver | None = None, allow_non_historical_import: bool = False, sync_new_messages: bool = False, include_unmapped_senders: bool = False) -> int:
    # user_map is optional: when omitted, the source "from" identity is sent as-is
    # (only valid if the caller already sends target-tenant ids). When provided,
    # senders without a target mapping are skipped instead of failing the batch -
    # Graph rejects channel messages whose sender isn't in the token's tenant.
    ordered = sorted(messages, key=lambda message: message["createdDateTime"])
    total = len(ordered)
    last: datetime | None = None
    imported = 0
    skipped_no_mapping = 0
    non_historical_fallback = False
    # Proxy identity for messages that can't be posted as their true sender
    # (system/event messages have no sender at all; some senders may have no
    # target mapping) - "migrate everything" takes priority over exact sender
    # fidelity for those, so fall back to any known target user instead of
    # dropping the message.
    fallback_sender_id = next(iter(user_map.values()), None) if user_map else None
    already_completed = not dry_run and state.status("teams-channel", target_channel) == "completed"
    if already_completed and not sync_new_messages:
        print(f"  [=] channel {target_channel}: already completed - skipping")
        return 0
    pending_exists = dry_run or any(state.status("channel-message", str(message["id"])) != "completed" for message in ordered)
    if already_completed and not pending_exists:
        print(f"  [=] channel {target_channel}: already completed - no new messages to sync")
        return 0
    # Reopens a completed channel's migration window when sync_new_messages found
    # pending messages - per Graph docs, startMigration can be called again after
    # completeMigration to import additional historical messages.
    should_open_session = not already_completed or pending_exists
    if already_completed:
        print(f"  [=] channel {target_channel}: already completed - reopening migration mode for new messages")
    if (
        not dry_run
        and state.status("teams-channel", target_channel) is None
        and _channel_already_has_messages(graph, target_team, target_channel)
    ):
        state.mark("teams-channel", target_channel, "completed", detail="target channel already had messages - skipped to avoid duplicates", teams_type="channel")
        print(f"  [=] channel {target_channel}: target already had messages - skipped to avoid duplicates")
        return 0
    print(f"  [+] channel {target_channel}: starting migration ({total} messages)")
    if not dry_run and should_open_session and state.status("teams-channel", target_channel) != "started":
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
        sender_id = ((message.get("from") or {}).get("user") or {}).get("id")
        if not sender_id and user_map is not None:
            # System/event message (member added/removed, rename, etc.) -
            # recreate it as a plain audit-log text message under the proxy
            # identity instead of dropping it, if we have one available.
            event_text = _event_detail_text(message)
            if event_text is None or fallback_sender_id is None:
                skipped_no_mapping += 1
                if not dry_run:
                    state.mark("channel-message", source_id, "skipped", detail=f"Unsupported message type: {message.get('messageType', 'unknown')}", teams_type="channel_message")
                continue
            from_field = {"user": {"id": fallback_sender_id, "userIdentityType": "aadUser"}}
            body = {"contentType": "text", "content": event_text}
            hosted_contents = None
        elif message.get("deletedDateTime"):
            # A deleted source message has empty body content - Graph rejects
            # that POST with 400 "Missing body content" (matches import_chat's
            # handling of the same source data shape).
            if not dry_run:
                state.mark("channel-message", source_id, "skipped", detail="deleted", teams_type="channel_message")
            continue
        else:
            from_field = message.get("from")
            if user_map is not None:
                target_sender_id = user_map.get(sender_id) if sender_id else None
                if not target_sender_id:
                    if include_unmapped_senders and fallback_sender_id:
                        raw_body = _import_body(message, attachment_resolver, image_resolver)
                        hosted_contents = raw_body.pop("_hosted_contents", None)
                        fallback_payload = _fallback_identity_payload(raw_body, sender_id, fallback_sender_id, message["createdDateTime"], last)
                        if hosted_contents:
                            fallback_payload["hostedContents"] = hosted_contents
                        if not dry_run:
                            try:
                                graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/messages", json=fallback_payload)
                            except GraphError as error:
                                if _is_duplicate_message(error):
                                    print(f"  [!] channel {target_channel}: message {source_id} already exists on target - marking completed")
                                else:
                                    raise
                            state.mark("channel-message", source_id, "completed", detail=f"sender {sender_id} has no target mapping - migrated under fallback identity", teams_type="channel_message")
                        last = datetime.fromisoformat(fallback_payload["createdDateTime"].replace("Z", "+00:00"))
                        imported += 1
                        continue
                    skipped_no_mapping += 1
                    if not dry_run:
                        state.mark("channel-message", source_id, "skipped", detail=f"sender {sender_id} has no target mapping", teams_type="channel_message")
                    continue
                from_field = {"user": {"id": target_sender_id, "userIdentityType": "aadUser"}}
            body = _import_body(message, attachment_resolver, image_resolver)
            hosted_contents = body.pop("_hosted_contents", None)
        payload = {
            "createdDateTime": _timestamp(message["createdDateTime"], last),
            "from": from_field,
            "body": body,
        }
        if hosted_contents:
            payload["hostedContents"] = hosted_contents
        elif message.get("hostedContents"):
            payload["hostedContents"] = message["hostedContents"]
        if not dry_run:
            try:
                graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/messages", json=payload)
            except GraphError as error:
                if _is_duplicate_message(error):
                    print(f"  [!] channel {target_channel}: message {source_id} already exists on target - marking completed")
                elif _is_migration_mode_closed(error):
                    # Unfixable: completeMigration already ran for this channel (or it
                    # was never put into migration mode) - Graph blocks ALL
                    # application-permission message writes to it now, no matter the
                    # timestamp, so retrying (here or on a future sync_new_messages
                    # resume) will keep failing identically.
                    raise GraphError(
                        f"Channel {target_channel} is no longer in migration mode (completeMigration already ran, "
                        "or it wasn't provisioned in migration mode) - Microsoft Graph only accepts "
                        "application-permission message writes while a channel is in migration mode, so no "
                        "further messages can be added to this channel via this tool. Verify target channel "
                        f"content before retrying. {imported}/{total} messages were imported before this happened. "
                        f"Original error: {error}"
                    ) from error
                elif _is_timestamp_too_early(error):
                    if not allow_non_historical_import or non_historical_fallback:
                        raise GraphError(
                            f"Channel {target_channel} rejected a historical timestamp as earlier than its own "
                            "creation time - it likely wasn't provisioned with a properly backdated creation time. "
                            f"{imported}/{total} messages were imported before this happened. "
                            f"Original error: {error}"
                        ) from error
                    # The target channel's real creation time was never backdated
                    # (unlike chats, startMigration doesn't move a channel's
                    # createdDateTime) - fall back to sequential timestamps seeded
                    # from that real creation time so the rest of the channel can
                    # still be imported, just without true historical dates.
                    frozen_created = _channel_created_datetime(graph, target_team, target_channel)
                    if frozen_created is None:
                        raise GraphError(
                            f"Channel {target_channel}: could not read the channel's current creation time to "
                            f"apply the non-historical fallback. {imported}/{total} messages were imported before "
                            f"this happened. Original error: {error}"
                        ) from error
                    last = frozen_created
                    non_historical_fallback = True
                    print(f"  [!] channel {target_channel}: target creation time was never backdated - falling back to non-historical (sequential) timestamps")
                    payload["createdDateTime"] = _timestamp(message["createdDateTime"], last)
                    try:
                        graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/messages", json=payload)
                    except GraphError as retry_error:
                        if _is_duplicate_message(retry_error):
                            print(f"  [!] channel {target_channel}: message {source_id} already exists on target - marking completed")
                        else:
                            raise
                else:
                    raise
            state.mark("channel-message", source_id, "completed", teams_type="channel_message")
        last = datetime.fromisoformat(payload["createdDateTime"].replace("Z", "+00:00"))
        imported += 1
        if total and (imported % 20 == 0 or imported == total):
            pct = int(imported / total * 100)
            print(f"    >> channel {target_channel}: {imported}/{total} messages migrated ({pct}%)")
    if not dry_run and should_open_session:
        graph.request("POST", f"/teams/{target_team}/channels/{target_channel}/completeMigration")
        state.mark("teams-channel", target_channel, "completed", teams_type="channel")
        if already_completed:
            print(f"  [+] channel {target_channel}: synced {imported} new message(s)")
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

def _chat_already_has_messages(
    graph: GraphClient,
    target_chat_id: str,
) -> bool:
    """Return True if the target chat contains at least one message."""
    try:
        existing = graph.request(
            "GET",
            f"/chats/{target_chat_id}/messages?$top=1",
        )
    except GraphError:
        return False

    values = existing.get("value") if isinstance(existing, dict) else None
    return bool(values)

def _chat_created_datetime(graph: GraphClient, chat_id: str) -> datetime | None:
    """Best-effort read of the target chat's current createdDateTime - used to
    confirm a startMigration backdate has actually propagated (see
    _wait_for_chat_migration_window)."""
    try:
        chat = graph.request("GET", f"/chats/{chat_id}?$select=createdDateTime")
    except GraphError:
        return None
    value = chat.get("createdDateTime") if isinstance(chat, dict) else None
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _wait_for_chat_migration_window(graph: GraphClient, chat_id: str, migration_time_iso: str, who: str, source_chat_id: str) -> bool:
    """Check (with one short retry) whether the target chat's createdDateTime
    reflects the backdated conversationCreationDateTime requested via
    startMigration. Returns False if it never lands - since startMigration
    can only be called once per chat, that means the backdate genuinely
    failed to apply and no amount of extra waiting will fix it."""
    cutoff = datetime.fromisoformat(migration_time_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    delay = _MIGRATION_WINDOW_INITIAL_DELAY
    for attempt in range(_MIGRATION_WINDOW_RETRIES):
        created = _chat_created_datetime(graph, chat_id)
        if created is not None and created <= cutoff:
            return True
        if attempt < _MIGRATION_WINDOW_RETRIES - 1:
            print(f"  [!] {who}chat {source_chat_id}: target creation time not backdated yet - waiting {delay:.0f}s before retrying")
            time.sleep(delay)
            delay *= 2
    return False


def import_chat(graph: GraphClient, bundle: dict[str, Any], user_map: dict[str, str], state: StateStore, dry_run: bool = False, stats: dict[str, int] | None = None, user_key: str | None = None, attachment_resolver: AttachmentResolver | None = None, image_resolver: ImageResolver | None = None, allow_non_historical_import: bool = False, sync_new_messages: bool = False, include_unmapped_senders: bool = False) -> tuple[str, int]:
    source_chat_id = bundle["chat"]["id"]
    chat_type = bundle["chat"].get("chatType")
    who = f"[{user_key}] " if user_key else ""
    target_chat_id = state.target("teams-chat", source_chat_id)

    already_completed = bool(
        not dry_run
        and target_chat_id
        and state.status("teams-chat", source_chat_id) == "completed"
        and _chat_already_has_messages(graph, target_chat_id)
    )
    if already_completed and not sync_new_messages:
        print(
            f"  [=] {who}chat {source_chat_id}: "
            "already completed with messages - skipping"
        )
        return target_chat_id, 0
    if already_completed:
        print(f"  [=] {who}chat {source_chat_id}: already completed - checking for new messages")

    # Proxy identity for messages that can't be posted as their true sender
    # (system/event messages have no sender at all) - "migrate everything"
    # takes priority over exact sender fidelity for those, so fall back to
    # any mapped member instead of dropping the message. Computed
    # unconditionally (not just on creation) since it's also needed when
    # resuming an already-created chat.
    fallback_sender_id = next(
        (user_map[m["userId"]] for m in bundle["members"] if user_map.get(m.get("userId"))),
        None,
    )

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
    # Tracks whether messages are being posted with sequential (non-historical)
    # timestamps instead of their true source ones - set either below (backdate
    # never applied) or mid-loop (unexpected migration-mode closure).
    non_historical_fallback = False
    pending_exists = dry_run or any(state.status("teams-chat-message", str(message["id"])) != "completed" for message in messages)
    if already_completed and not pending_exists:
        print(f"  [=] {who}chat {source_chat_id}: already completed - no new messages to sync")
        return target_chat_id, 0
    # Reopens a completed chat's migration window when sync_new_messages found
    # pending messages - per Graph docs, startMigration can be called again after
    # completeMigration to import additional historical messages.
    should_open_session = not already_completed or pending_exists
    if already_completed:
        print(f"  [=] {who}chat {source_chat_id}: already completed - reopening migration mode for new messages")
    if not dry_run and should_open_session and already_completed:
        # The target chat's createdDateTime was already backdated during the
        # original migration, so re-sending the same conversationCreationDateTime
        # here would fail (Graph requires it to be strictly older than the
        # chat's *current* createdDateTime) - reopening just needs migration
        # mode back on, not another backdate.
        try:
            graph.request("POST", f"/chats/{target_chat_id}/startMigration")
            print(f"  [+] {who}chat {source_chat_id}: migration mode reopened")
        except GraphError as error:
            if "already in migration mode" not in str(error):
                raise
            print(f"  [!] {who}chat {source_chat_id}: already in migration mode; resuming")
    elif not dry_run and should_open_session:
        earliest_message = messages[0]["createdDateTime"] if messages else None
        # Prefer the source chat's own creation time over the first message's
        # timestamp - it's the true origin of the thread, is guaranteed to be
        # no later than any message in it, and is still available even when
        # there are no messages left to migrate (e.g. all already completed).
        migration_basis = bundle["chat"].get("createdDateTime") or earliest_message

        if migration_basis:
            migration_time = _migration_conversation_creation_time(migration_basis)
            # startMigration fails with 400 if the chat is already in
            # migration mode - only call it once, on the run that actually
            # created/started the chat, so resumes don't hit that error.
            if state.status("teams-chat", source_chat_id) != "started":
                try:
                    graph.request(
                        "POST",
                        f"/chats/{target_chat_id}/startMigration",
                        json={
                            "conversationCreationDateTime": migration_time
                        },
                    )

                    print(
                        f"  [+] {who}chat {source_chat_id}: "
                        "migration mode started"
                    )

                except GraphError as error:
                    error_text = str(error)

                    if "already in migration mode" in error_text:
                        print(
                            f"  [!] {who}chat {source_chat_id}: "
                            "already in migration mode; resuming"
                        )

                    else:
                        raise

                state.mark(
                    "teams-chat",
                    source_chat_id,
                    "started",
                    target_chat_id,
                    user_key=user_key,
                    teams_type=chat_type,
                )

            if messages and not _wait_for_chat_migration_window(graph, target_chat_id, migration_time, who, source_chat_id):
                # The backdate may not have applied on the very first
                # startMigration call (e.g. Graph hadn't finished replicating
                # the newly created chat yet). Retrying with the same time is
                # harmless: Graph either accepts it, or rejects it with
                # "already in migration mode" if the window is truly locked.
                print(f"  [!] {who}chat {source_chat_id}: creation time still not backdated - retrying startMigration")
                try:
                    graph.request(
                        "POST",
                        f"/chats/{target_chat_id}/startMigration",
                        json={"conversationCreationDateTime": migration_time},
                    )
                except GraphError as error:
                    if "already in migration mode" not in str(error):
                        raise
                if not _wait_for_chat_migration_window(graph, target_chat_id, migration_time, who, source_chat_id):
                    stuck_message = (
                        f"Chat {source_chat_id}: target chat's creation time was not backdated to {migration_time} "
                        "even after retrying startMigration. This chat is now permanently unable to accept "
                        "historical timestamps earlier than its actual creation time - retrying (including on "
                        "resume) will keep failing the same way, since startMigration cannot reopen an already "
                        "locked-in window."
                    )
                    if not allow_non_historical_import:
                        raise GraphError(f"{stuck_message} 0 messages were imported into this chat.")
                    # Fallback: seed `last` to the chat's real (frozen) creation
                    # time so _timestamp() below pushes every message just past
                    # it in order - messages lose their true historical dates but
                    # keep correct relative ordering and sender attribution.
                    frozen_created = _chat_created_datetime(graph, target_chat_id)
                    if frozen_created is None:
                        raise GraphError(f"{stuck_message} Could not read the chat's current creation time to apply the non-historical fallback.")
                    last = frozen_created
                    non_historical_fallback = True
                    print(
                        f"  [!] {who}chat {source_chat_id}: {stuck_message} "
                        "Falling back to non-historical (sequential) timestamps for this chat's messages."
                    )
    for message in messages:
        source_id = str(message["id"])
        if state.status("teams-chat-message", source_id) == "completed":
            continue
        source_from = (message.get("from") or {}).get("user") or {}
        sender_id = source_from.get("id")
        if not sender_id:
            # System/event message (member added/removed, rename, etc.) -
            # Graph never lets these be authored by a real user, so recreate
            # the event as a plain audit-log text message instead of dropping
            # it, as long as we have a proxy identity to post it under.
            event_text = _event_detail_text(message)
            if event_text is None or fallback_sender_id is None:
                if stats is not None:
                    stats["skipped_system"] = stats.get("skipped_system", 0) + 1
                if not dry_run:
                    state.mark("teams-chat-message", source_id, "skipped", detail=f"Unsupported message type: {message.get('messageType', 'unknown')}", user_key=user_key, teams_type=chat_type)
                continue
            payload = {
                "createdDateTime": _timestamp(message["createdDateTime"], last),
                "from": {"user": {"id": fallback_sender_id, "userIdentityType": "aadUser"}},
                "body": {"contentType": "text", "content": event_text},
            }
        elif message.get("deletedDateTime"):
            # Deleted source message has no content to migrate - nothing to
            # fall back to, this one is genuinely unrecoverable.
            if not dry_run:
                state.mark("teams-chat-message", source_id, "skipped", detail="deleted", user_key=user_key, teams_type=chat_type)
            continue
        elif sender_id not in user_map:
            if include_unmapped_senders and fallback_sender_id:
                raw_body = _import_body(message, attachment_resolver, image_resolver)
                hosted_contents = raw_body.pop("_hosted_contents", None)
                payload = _fallback_identity_payload(raw_body, sender_id, fallback_sender_id, message["createdDateTime"], last)
                if hosted_contents:
                    payload["hostedContents"] = hosted_contents
                elif message.get("hostedContents"):
                    payload["hostedContents"] = message["hostedContents"]
            else:
                if stats is not None:
                    stats["skipped_no_mapping"] = stats.get("skipped_no_mapping", 0) + 1
                if not dry_run:
                    state.mark("teams-chat-message", source_id, "skipped", detail=f"sender {sender_id} has no target mapping", user_key=user_key, teams_type=chat_type)
                continue
        else:
            try:
                payload = _import_payload(message, user_map, last, attachment_resolver, image_resolver)
            except ValueError as error:
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
                elif _is_migration_mode_closed(error):
                    # Unfixable: completeMigration already ran for this chat -
                    # Graph blocks ALL application-permission message writes to it
                    # now, no matter the timestamp (see _is_migration_mode_closed).
                    # A sync_new_messages resume of an already-completed chat will
                    # always hit this for any message not previously imported - the
                    # only way to add more history is to recreate the target chat
                    # and re-run the migration in one pass before completeMigration.
                    raise GraphError(
                        f"Chat {source_chat_id} is no longer in migration mode (completeMigration already ran) - "
                        "Microsoft Graph only accepts application-permission message writes while a chat is in "
                        "migration mode, so no further messages can be added to this chat via this tool. Verify "
                        f"target chat content before retrying. {imported}/{total} messages were imported before "
                        f"this happened. Original error: {error}"
                    ) from error
                elif _is_timestamp_too_early(error):
                    # The pre-loop backdate check already confirmed the window was
                    # open, so hitting this mid-loop is unexpected but potentially
                    # recoverable - retry once with a later, sequential timestamp.
                    if not allow_non_historical_import or non_historical_fallback:
                        raise GraphError(
                            f"Chat {source_chat_id} rejected a historical timestamp as earlier than its own "
                            "creation time. "
                            f"{imported}/{total} messages were imported before this happened. "
                            f"Original error: {error}"
                        ) from error
                    frozen_created = _chat_created_datetime(graph, target_chat_id)
                    if frozen_created is None:
                        raise GraphError(
                            f"Chat {source_chat_id}: could not read the chat's current creation time to apply "
                            f"the non-historical fallback. {imported}/{total} messages were imported before this "
                            f"happened. Original error: {error}"
                        ) from error
                    last = frozen_created
                    non_historical_fallback = True
                    print(f"  [!] {who}chat {source_chat_id}: target creation time was never backdated - falling back to non-historical (sequential) timestamps")
                    payload["createdDateTime"] = _timestamp(message["createdDateTime"], last)
                    try:
                        graph.request("POST", f"/chats/{target_chat_id}/messages", json=payload)
                    except GraphError as retry_error:
                        if _is_duplicate_message(retry_error):
                            print(f"  [!] {who}chat {source_chat_id}: message {source_id} already exists on target - marking completed")
                        else:
                            raise
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
    if not dry_run and should_open_session:
        graph.request("POST", f"/chats/{target_chat_id}/completeMigration")
        # PATCH (405) is unsupported; delete+re-add each member with full history for
        # group chats - only needed once, on the initial full migration.
        if not already_completed and bundle["chat"].get("chatType") == "group":
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
        if already_completed:
            print(f"  [+] {who}chat {source_chat_id}: synced {imported} new message(s)")
        else:
            print(f"  [+] {who}Completed chat migration: {imported} messages")
    return target_chat_id, imported

