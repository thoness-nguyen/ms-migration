from datetime import datetime, timezone

from migration.teams.services import _timestamp, make_attachment_resolver, make_image_resolver


def test_timestamp_is_unique_to_millisecond():
    first = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _timestamp("2025-01-01T00:00:00Z", first) == "2025-01-01T00:00:00.001Z"


def test_timestamp_preserves_order():
    assert _timestamp("2025-01-01T00:00:01.123Z", None) == "2025-01-01T00:00:01.123Z"


def test_image_resolver_normalizes_unsupported_content_type_header():
    class Response:
        content = b"\x89PNG\r\n\x1a\n" + b"rest-of-png-bytes"
        headers = {"Content-Type": "application/octet-stream"}

        def raise_for_status(self):
            pass

    class SourceGraph:
        def raw_request(self, method, url, **kwargs):
            return Response()

    resolve = make_image_resolver(SourceGraph())
    data, content_type = resolve("https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/1/$value")
    assert content_type == "image/png"
    assert data == Response.content


def test_image_resolver_drops_unsupported_image_format():
    class Response:
        content = b"GIF89a" + b"rest-of-gif-bytes"
        headers = {"Content-Type": "image/gif"}

        def raise_for_status(self):
            pass

    class SourceGraph:
        def raw_request(self, method, url, **kwargs):
            return Response()

    resolve = make_image_resolver(SourceGraph())
    assert resolve("https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/1/$value") is None


def test_image_resolver_drops_image_over_size_limit():
    class Response:
        content = b"\x89PNG\r\n\x1a\n" + b"x" * (3 * 1024 * 1024 + 1)
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            pass

    class SourceGraph:
        def raw_request(self, method, url, **kwargs):
            return Response()

    resolve = make_image_resolver(SourceGraph())
    assert resolve("https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/1/$value") is None


def test_attachment_resolver_rewrites_to_target_file_when_found():
    class TargetGraph:
        def request(self, method, path, **kwargs):
            assert path == "/users/nguyet@paizes.com/drive/root:/foo.pdf"
            return {"webUrl": "https://target-my.sharepoint.com/personal/nguyet_paizes_com/Documents/foo.pdf"}

    resolve = make_attachment_resolver(None, TargetGraph(), {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"})
    url = resolve("https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf")
    assert url == "https://target-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf".replace("harbouroutdoor", "paizes")


def test_attachment_resolver_falls_back_to_source_when_not_found():
    from migration.common.graph import GraphError

    class TargetGraph:
        def request(self, method, path, **kwargs):
            raise GraphError("404 not found")

    class SourceGraph:
        base_url = "https://graph.microsoft.com/v1.0"

        def request(self, method, path, **kwargs):
            raise GraphError("404 not found")

        def raw_request(self, method, url, **kwargs):
            raise GraphError("404 not found")

    resolve = make_attachment_resolver(SourceGraph(), TargetGraph(), {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"})
    source_url = "https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf"
    assert resolve(source_url) == source_url


def test_attachment_resolver_migrates_file_when_missing_on_target():
    from migration.common.graph import GraphError

    class TargetGraph:
        def __init__(self):
            self.uploaded = None

        def request(self, method, path, **kwargs):
            if method == "GET":
                raise GraphError("404 not found")
            assert method == "PUT"
            self.uploaded = kwargs.get("data")
            return {"webUrl": "https://target-my.sharepoint.com/personal/nguyet_paizes_com/Documents/foo.pdf"}

    class Response:
        content = b"file-bytes"

        def raise_for_status(self):
            pass

    class SourceGraph:
        base_url = "https://graph.microsoft.com/v1.0"

        def request(self, method, path, **kwargs):
            assert path == "/users/nguyet@harbouroutdoor.com/drive/root:/foo.pdf"
            return {"id": "source-item-1"}

        def raw_request(self, method, url, **kwargs):
            assert url.endswith("/users/nguyet@harbouroutdoor.com/drive/root:/foo.pdf:/content")
            return Response()

    target = TargetGraph()
    resolve = make_attachment_resolver(SourceGraph(), target, {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"})
    url = resolve("https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf")
    assert url == "https://target-my.sharepoint.com/personal/nguyet_paizes_com/Documents/foo.pdf"
    assert target.uploaded == b"file-bytes"


def test_attachment_resolver_falls_back_when_owner_unmapped():
    resolve = make_attachment_resolver(None, None, {})
    source_url = "https://contoso-my.sharepoint.com/personal/someone_harbouroutdoor_com/Documents/foo.pdf"
    assert resolve(source_url) == source_url


def test_attachment_resolver_copies_permissions_when_migrating():
    from migration.common.graph import GraphError

    calls: dict[str, str] = {}

    class TargetGraph:
        def request(self, method, path, **kwargs):
            if method == "GET":
                raise GraphError("404 not found")
            assert method == "PUT"
            return {"id": "target-item-1", "webUrl": "https://target-my.sharepoint.com/personal/nguyet_paizes_com/Documents/foo.pdf"}

    class Response:
        content = b"file-bytes"

        def raise_for_status(self):
            pass

    class SourceGraph:
        base_url = "https://graph.microsoft.com/v1.0"

        def request(self, method, path, **kwargs):
            assert path == "/users/nguyet@harbouroutdoor.com/drive/root:/foo.pdf"
            return {"id": "source-item-1"}

        def raw_request(self, method, url, **kwargs):
            return Response()

        def pages(self, path):
            calls["permissions_path"] = path
            return iter([])

    resolve = make_attachment_resolver(
        SourceGraph(),
        TargetGraph(),
        {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"},
        {"source-user-id": "target-user-id"},
    )
    resolve("https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf")
    # _copy_item_permissions must have actually been invoked against the source item
    assert calls["permissions_path"] == "/users/nguyet@harbouroutdoor.com/drive/items/source-item-1/permissions"


def test_import_channel_messages_falls_back_when_creation_time_not_backdated(tmp_path):
    from migration.common.checkpoint import StateStore
    from migration.common.graph import GraphError
    from migration.teams.services import import_channel_messages

    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration"):
                return {}
            if path.endswith("/completeMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if "?$select=createdDateTime" in path:
                return {"createdDateTime": "2026-09-18T00:00:00Z"}
            if path.endswith("/messages") and method == "POST":
                payload = kwargs["json"]
                posts.append(dict(payload))
                if len(posts) == 1:
                    raise GraphError("MessageWritesBlocked-OriginalArrivalTime ... is less than thread creation time ...")
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "hi"}},
        {"id": "2", "createdDateTime": "2020-01-02T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "hi2"}},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    imported = import_channel_messages(
        Graph(),
        "team-1",
        "channel-1",
        messages,
        state,
        user_map={"u1": "target-u1"},
        allow_non_historical_import=True,
    )
    assert imported == 2
    # first attempt used the true historical timestamp and was rejected; the
    # retry fell back to the channel's real (frozen) creation time instead
    assert posts[0]["createdDateTime"].startswith("2020-01-01")
    assert posts[1]["createdDateTime"].startswith("2026-09-18")
    assert posts[2]["createdDateTime"] > posts[1]["createdDateTime"]


def test_import_channel_messages_raises_when_fallback_disabled(tmp_path):
    from migration.common.checkpoint import StateStore
    from migration.common.graph import GraphError
    from migration.teams.services import import_channel_messages

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if path.endswith("/messages") and method == "POST":
                raise GraphError("MessageWritesBlocked-OriginalArrivalTime ... is less than thread creation time ...")
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "hi"}},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    try:
        import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"})
        assert False, "expected GraphError"
    except GraphError as error:
        assert "rejected a historical timestamp" in str(error)


def test_import_channel_messages_skips_deleted_messages(tmp_path):
    """A deleted source message has empty body content - posting it as-is gets
    a 400 'Missing body content' from Graph and fails the whole channel batch
    (matches import_chat's existing deletedDateTime skip)."""
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration"):
                return {}
            if path.endswith("/completeMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if path.endswith("/messages") and method == "POST":
                posts.append(dict(kwargs["json"]))
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "deletedDateTime": "2020-02-01T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "html", "content": ""}, "attachments": []},
        {"id": "2", "createdDateTime": "2020-01-02T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "hi"}, "attachments": []},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    imported = import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"})
    assert imported == 1
    assert len(posts) == 1
    assert posts[0]["body"]["content"] == "hi"


def test_import_channel_messages_preserves_member_removed_event_as_audit_text(tmp_path):
    """Mirrors import_chat's handling: a member-removal mid-conversation has
    no real sender (Graph sets `from: null`) - recreate it as plain audit text
    under a fallback (already-mapped) sender instead of dropping it."""
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration") or path.endswith("/completeMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if path.endswith("/messages") and method == "POST":
                posts.append(dict(kwargs["json"]))
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {
            "id": "event-1",
            "createdDateTime": "2020-01-01T00:00:00Z",
            "from": None,
            "body": {"contentType": "html", "content": "<systemEventMessage/>"},
            "attachments": [],
            "eventDetail": {
                "@odata.type": "#microsoft.graph.membersRemovedEventMessageDetail",
                "members": [{"id": "u2", "displayName": "Removed Person"}],
                "initiator": {"user": {"id": "u1", "displayName": "Owner"}},
            },
        },
    ]
    state = StateStore(tmp_path / "state.sqlite")
    imported = import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"})
    assert imported == 1
    assert "Owner removed Removed Person from the chat" in posts[0]["body"]["content"]
    assert posts[0]["from"]["user"]["id"] == "target-u1"


def test_import_channel_messages_skips_message_from_unmapped_sender(tmp_path):
    """A message from a sender with no target mapping must be skipped instead
    of migrated under a fallback identity."""
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration") or path.endswith("/completeMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if path.endswith("/messages") and method == "POST":
                posts.append(dict(kwargs["json"]))
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u2", "displayName": "Departed User"}}, "body": {"contentType": "text", "content": "hello"}, "attachments": []},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    imported = import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"})
    assert imported == 0
    assert posts == []
    assert state.status("channel-message", "1") == "skipped"


def test_import_channel_messages_migrates_unmapped_sender_when_opted_in(tmp_path):
    """With include_unmapped_senders=True, a message from an unmapped sender is
    migrated under a proxy identity with a note instead of being skipped."""
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path.endswith("/startMigration") or path.endswith("/completeMigration"):
                return {}
            if "?$top=1" in path:
                return {"value": []}
            if path.endswith("/messages") and method == "POST":
                posts.append(dict(kwargs["json"]))
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u2", "displayName": "Departed User"}}, "body": {"contentType": "text", "content": "hello"}, "attachments": []},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    imported = import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"}, include_unmapped_senders=True)
    assert imported == 1
    assert posts[0]["from"]["user"]["id"] == "target-u1"
    assert "u2" in posts[0]["body"]["content"]
    assert "hello" in posts[0]["body"]["content"]
    assert state.status("channel-message", "1") == "completed"


def test_import_body_forces_html_contenttype_when_attachment_injected():
    """A source message with contentType 'text' but an <attachment> tag must be
    re-typed as 'html' on import - otherwise the synthesized <p><a>...</a></p>
    markup renders as literal, escaped text on the target instead of a link."""
    from migration.teams.services import _import_body

    message = {
        "body": {"contentType": "text", "content": '<attachment id="a1"></attachment>'},
        "attachments": [{"id": "a1", "contentType": "reference", "contentUrl": "https://contoso.com/foo.pdf", "name": "foo.pdf"}],
    }
    result = _import_body(message)
    assert result["contentType"] == "html"
    assert '<a href="https://contoso.com/foo.pdf">foo.pdf</a>' in result["content"]


def test_import_body_preserves_contenttype_when_no_attachments():
    from migration.teams.services import _import_body

    message = {"body": {"contentType": "text", "content": "plain message, no markup"}}
    result = _import_body(message)
    assert result["contentType"] == "text"
    assert result["content"] == "plain message, no markup"


def test_attachment_resolver_rewrites_site_library_url_when_found():
    """Channel-message attachments link to a SharePoint TEAM SITE library
    (/sites/{site}/{library}/{path}), not a personal OneDrive - a distinct
    resolver path from the personal-site one above."""
    source_url = "https://harbouroutdoorasialimited.sharepoint.com/sites/HarbourPD/Shared Documents/foo.pdf"

    class SourceGraph:
        def request(self, method, path, **kwargs):
            assert path == "/sites/harbouroutdoorasialimited.sharepoint.com:/sites/HarbourPD?$select=id"
            return {"id": "source-site-id"}

        def pages(self, path):
            assert path == "/sites/source-site-id/drives?$select=id,name,driveType,webUrl"
            return iter([{"id": "source-drive-1", "name": "Shared Documents", "driveType": "documentLibrary"}])

    class TargetGraph:
        def pages(self, path):
            assert path == "/sites/target-site-id/drives?$select=id,name,driveType,webUrl"
            return iter([{"id": "target-drive-1", "name": "Shared Documents", "driveType": "documentLibrary"}])

        def request(self, method, path, **kwargs):
            assert path == "/drives/target-drive-1/root:/foo.pdf"
            return {"webUrl": "https://paizesoffice.sharepoint.com/sites/HarbourPD/Shared%20Documents/foo.pdf"}

    site_map = {"harbouroutdoorasialimited.sharepoint.com/sites/harbourpd": "target-site-id"}
    resolve = make_attachment_resolver(SourceGraph(), TargetGraph(), {}, site_map=site_map)
    assert resolve(source_url) == "https://paizesoffice.sharepoint.com/sites/HarbourPD/Shared%20Documents/foo.pdf"


def test_attachment_resolver_migrates_site_library_file_when_missing():
    from migration.common.graph import GraphError

    calls: dict[str, str] = {}

    class SourceGraph:
        base_url = "https://graph.microsoft.com/v1.0"

        def request(self, method, path, **kwargs):
            if path.endswith(":/sites/HarbourPD?$select=id"):
                return {"id": "source-site-id"}
            assert path == "/drives/source-drive-1/root:/foo.pdf"
            return {"id": "source-item-1"}

        def raw_request(self, method, url, **kwargs):
            class Response:
                content = b"file-bytes"

                def raise_for_status(self):
                    pass

            return Response()

        def pages(self, path):
            assert path == "/sites/source-site-id/drives?$select=id,name,driveType,webUrl"
            return iter([{"id": "source-drive-1", "name": "Shared Documents", "driveType": "documentLibrary"}])

    class TargetGraph:
        def pages(self, path):
            return iter([{"id": "target-drive-1", "name": "Shared Documents", "driveType": "documentLibrary"}])

        def request(self, method, path, **kwargs):
            if method == "GET":
                raise GraphError("404 not found")
            assert method == "PUT"
            return {"id": "target-item-1", "webUrl": "https://paizesoffice.sharepoint.com/sites/HarbourPD/Shared%20Documents/foo.pdf"}

        def pages_permissions(self, path):
            calls["permissions_path"] = path
            return iter([])

    target = TargetGraph()
    # Patch in permission-listing support via .pages, called on the SOURCE graph
    # by sharepoint.services.copy_item_permissions.
    source = SourceGraph()

    def source_pages(path):
        if "permissions" in path:
            calls["permissions_path"] = path
            return iter([])
        return SourceGraph.pages(source, path)

    source.pages = source_pages

    site_map = {"harbouroutdoorasialimited.sharepoint.com/sites/harbourpd": "target-site-id"}
    resolve = make_attachment_resolver(source, target, {}, site_map=site_map)
    url = resolve("https://harbouroutdoorasialimited.sharepoint.com/sites/HarbourPD/Shared Documents/foo.pdf")
    assert url == "https://paizesoffice.sharepoint.com/sites/HarbourPD/Shared%20Documents/foo.pdf"
    assert calls["permissions_path"] == "/drives/source-drive-1/items/source-item-1/permissions"


def test_attachment_resolver_falls_back_when_site_not_in_site_map():
    resolve = make_attachment_resolver(None, None, {}, site_map={})
    source_url = "https://harbouroutdoorasialimited.sharepoint.com/sites/HarbourPD/Shared Documents/foo.pdf"
    assert resolve(source_url) == source_url


def test_import_channel_messages_syncs_new_messages_when_already_completed(tmp_path):
    """sync_new_messages=True must not skip an already-completed channel outright -
    it should reopen migration mode, post only messages not yet marked as
    completed checkpoints, then complete migration again."""
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    calls: list[tuple[str, str]] = []
    posts: list[dict] = []

    class Graph:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            if path.endswith("/startMigration") or path.endswith("/completeMigration"):
                return {}
            if path.endswith("/messages") and method == "POST":
                posts.append(dict(kwargs["json"]))
                return {}
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "old"}},
        {"id": "2", "createdDateTime": "2020-01-02T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "new"}},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-channel", "channel-1", "completed", teams_type="channel")
    state.mark("channel-message", "1", "completed", teams_type="channel_message")

    imported = import_channel_messages(
        Graph(),
        "team-1",
        "channel-1",
        messages,
        state,
        user_map={"u1": "target-u1"},
        sync_new_messages=True,
    )
    assert imported == 1
    assert len(posts) == 1
    assert posts[0]["body"]["content"] == "new"
    assert any(path.endswith("/startMigration") for _, path in calls)
    assert any(path.endswith("/completeMigration") for _, path in calls)
    assert state.status("teams-channel", "channel-1") == "completed"


def test_import_channel_messages_skips_entirely_when_sync_disabled(tmp_path):
    from migration.common.checkpoint import StateStore
    from migration.teams.services import import_channel_messages

    class Graph:
        def request(self, method, path, **kwargs):
            raise AssertionError(f"unexpected call {method} {path}")

    messages = [
        {"id": "1", "createdDateTime": "2020-01-01T00:00:00Z", "from": {"user": {"id": "u1"}}, "body": {"contentType": "text", "content": "old"}},
    ]
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-channel", "channel-1", "completed", teams_type="channel")

    imported = import_channel_messages(Graph(), "team-1", "channel-1", messages, state, user_map={"u1": "target-u1"})
    assert imported == 0

