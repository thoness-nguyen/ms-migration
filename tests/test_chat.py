from migration.common.checkpoint import StateStore
from migration.teams.services import _import_body, _import_payload, import_chat, target_member


def test_target_member_uses_target_identity():
    member = target_member({"userId": "source-1"}, {"source-1": "target-1"})
    assert member["user@odata.bind"].endswith("users('target-1')")


def test_import_body_plain_text():
    message = {"body": {"contentType": "text", "content": "hello"}, "attachments": []}
    assert _import_body(message) == {"contentType": "text", "content": "hello"}


def test_import_body_rewrites_inline_image_and_returns_hosted_content():
    message = {
        "body": {
            "contentType": "html",
            "content": '<div><img src="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/abc/$value"></div>',
        },
        "attachments": [],
    }

    def image_resolver(url):
        assert url.endswith("/hostedContents/abc/$value")
        return b"image-bytes", "image/png"

    body = _import_body(message, image_resolver=image_resolver)
    assert 'src="../hostedContents/1/$value"' in body["content"]
    assert body["_hosted_contents"] == [
        {"@microsoft.graph.temporaryId": "1", "contentBytes": "aW1hZ2UtYnl0ZXM=", "contentType": "image/png"}
    ]


def test_import_body_leaves_image_untouched_when_resolver_fails():
    message = {
        "body": {"contentType": "html", "content": '<img src="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/abc/$value">'},
        "attachments": [],
    }
    body = _import_body(message, image_resolver=lambda url: None)
    assert "hostedContents/abc/$value" in body["content"]
    assert "_hosted_contents" not in body


def test_import_body_skips_later_images_once_combined_size_exceeds_budget():
    message = {
        "body": {
            "contentType": "html",
            "content": (
                '<img src="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/a/$value">'
                '<img src="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/b/$value">'
            ),
        },
        "attachments": [],
    }
    big = b"x" * (2 * 1024 * 1024)

    def image_resolver(url):
        return big, "image/png"

    body = _import_body(message, image_resolver=image_resolver)
    # First image (2 MiB) fits under the 3 MiB combined budget; the second
    # would push the total to 4 MiB, so it's left as the original source URL.
    assert body["_hosted_contents"] and len(body["_hosted_contents"]) == 1
    assert "hostedContents/b/$value" in body["content"]
    assert 'src="../hostedContents/1/$value"' in body["content"]


def test_import_payload_promotes_hosted_contents_from_image_resolution():
    payload = _import_payload(
        {
            "id": "m1",
            "createdDateTime": "2025-01-01T00:00:00Z",
            "from": {"user": {"id": "src"}},
            "body": {"contentType": "html", "content": '<img src="https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages/m1/hostedContents/abc/$value">'},
        },
        {"src": "tgt"},
        None,
        image_resolver=lambda url: (b"bytes", "image/jpeg"),
    )
    assert "_hosted_contents" not in payload["body"]
    assert payload["hostedContents"] == [
        {"@microsoft.graph.temporaryId": "1", "contentBytes": "Ynl0ZXM=", "contentType": "image/jpeg"}
    ]


def test_import_body_expands_forwarded_message_reference():
    message = {
        "body": {"contentType": "html", "content": "<attachment id=\"1\"></attachment>"},
        "attachments": [{"id": "1", "contentType": "forwardedMessageReference", "content": '{"originalMessageContent":"<p>original link</p>"}'}],
    }
    assert _import_body(message) == {"contentType": "html", "content": "<p>original link</p>"}


def test_import_body_resolves_file_attachment_via_content_url():
    message = {
        "body": {"contentType": "html", "content": "Check this: <attachment id=\"42\"></attachment>"},
        "attachments": [{"id": "42", "contentType": "reference", "contentUrl": "https://sharepoint.example.com/file.pdf", "name": "file.pdf"}],
    }
    body = _import_body(message)
    assert "https://sharepoint.example.com/file.pdf" in body["content"]
    assert "file.pdf" in body["content"]
    assert "<attachment" not in body["content"]


def test_import_body_resolves_multiple_attachments():
    message = {
        "body": {"contentType": "html", "content": "<attachment id=\"1\"></attachment><attachment id=\"2\"></attachment>"},
        "attachments": [
            {"id": "1", "contentType": "reference", "contentUrl": "https://example.com/a.pdf", "name": "a.pdf"},
            {"id": "2", "contentType": "reference", "contentUrl": "https://example.com/b.pdf", "name": "b.pdf"},
        ],
    }
    body = _import_body(message)
    assert "a.pdf" in body["content"]
    assert "b.pdf" in body["content"]
    assert "<attachment" not in body["content"]


def test_import_body_unresolvable_attachment_becomes_empty():
    # adaptive card or unknown type with no contentUrl — placeholder removed
    message = {
        "body": {"contentType": "html", "content": "Hi <attachment id=\"99\"></attachment>"},
        "attachments": [{"id": "99", "contentType": "application/vnd.microsoft.card.adaptive", "content": "{}", "contentUrl": None}],
    }
    body = _import_body(message)
    assert "<attachment" not in body["content"]
    assert "Hi" in body["content"]


def test_import_body_self_closing_attachment_tag():
    message = {
        "body": {"contentType": "html", "content": "<attachment id=\"5\"/>"},
        "attachments": [{"id": "5", "contentType": "forwardedMessageReference", "content": '{"originalMessageContent":"<p>fwd</p>"}'}],
    }
    assert _import_body(message) == {"contentType": "html", "content": "<p>fwd</p>"}


def test_import_payload_preserves_supported_body_and_reports_features():
    payload = _import_payload(
        {"id": "message-1", "createdDateTime": "2025-01-01T00:00:00Z", "from": {"user": {"id": "source-1"}}, "body": {"contentType": "html", "content": "<p>hello</p>"}, "reactions": [{"reactionType": "like"}], "mentions": [{"mentionText": "Alice"}]},
        {"source-1": "target-1"},
        None,
    )
    assert payload["from"]["user"]["id"] == "target-1"
    assert payload["body"]["content"] == "<p>hello</p>"
    assert payload["_unsupported"] == ["reactions", "mentions"]


def test_import_payload_file_attachment_with_url_not_flagged_unsupported():
    payload = _import_payload(
        {"id": "m1", "createdDateTime": "2025-01-01T00:00:00Z", "from": {"user": {"id": "src"}},
         "body": {"contentType": "html", "content": "<attachment id=\"1\"></attachment>"},
         "attachments": [{"id": "1", "contentType": "reference", "contentUrl": "https://example.com/f.pdf", "name": "f.pdf"}]},
        {"src": "tgt"}, None,
    )
    assert "f.pdf" in payload["body"]["content"]
    assert "_unsupported" not in payload


def test_import_payload_unresolvable_attachment_flagged_unsupported():
    payload = _import_payload(
        {"id": "m1", "createdDateTime": "2025-01-01T00:00:00Z", "from": {"user": {"id": "src"}},
         "body": {"contentType": "html", "content": "<attachment id=\"1\"></attachment>"},
         "attachments": [{"id": "1", "contentType": "application/vnd.microsoft.card.adaptive", "content": "{}", "contentUrl": None}]},
        {"src": "tgt"}, None,
    )
    assert "attachments" in payload.get("_unsupported", [])


def test_import_chat_skips_deleted_messages(tmp_path):
    bundle = {
        "chat": {"id": "chat-1", "chatType": "oneOnOne"},
        "members": [{"userId": "source-1"}],
        "messages": [
            {"id": "del-1", "messageType": "message", "createdDateTime": "2025-01-01T00:00:00Z",
             "deletedDateTime": "2025-01-01T00:01:00Z",
             "from": {"user": {"id": "source-1"}}, "body": {"contentType": "html", "content": ""}},
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    _, count = import_chat(None, bundle, {"source-1": "target-1"}, state, dry_run=True)
    assert count == 0


def test_import_chat_dry_run_requires_mapping(tmp_path):
    from migration.common.checkpoint import StateStore

    bundle = {
        "chat": {"id": "chat-1", "chatType": "oneOnOne"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [{"id": "message-1", "createdDateTime": "2025-01-01T00:00:00Z", "from": {"user": {"id": "source-1"}}, "body": {"contentType": "html", "content": "hello"}}],
    }
    try:
        import_chat(None, bundle, {"source-1": "target-1"}, StateStore(tmp_path / "state.sqlite"), True)
    except ValueError as error:
        assert "source-2" in str(error)
    else:
        raise AssertionError("missing mapping should fail")


def test_import_chat_dry_run_skips_system_messages(tmp_path):
    from migration.common.checkpoint import StateStore

    bundle = {
        "chat": {"id": "chat-1", "chatType": "oneOnOne"},
        "members": [{"userId": "source-1"}],
        "messages": [{"id": "event-1", "messageType": "unknownFutureValue", "createdDateTime": "2025-01-01T00:00:00Z", "from": None, "body": {"contentType": "html", "content": "<systemEventMessage/>"}}],
    }
    state = StateStore(tmp_path / "state.sqlite")
    target_chat_id, count = import_chat(None, bundle, {"source-1": "target-1"}, state, True)
    assert target_chat_id == "dry-run:chat-1"
    assert count == 0
    assert state.status("teams-chat-message", "event-1") is None
    assert state.target("teams-chat", "chat-1") is None


def test_import_chat_preserves_member_removed_event_as_audit_text(tmp_path):
    """A member-removal mid-conversation must not be silently dropped - it has
    no real sender (Graph sets `from: null`), so it's recreated as a plain
    text message under a fallback (already-mapped) sender instead."""
    posted = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path == "/chats":
                return {"id": "target-chat-1"}
            if path.startswith("/chats/target-chat-1?"):
                return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
            if method == "POST" and path.endswith("/messages"):
                posted.append(kwargs["json"])
                return {}
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "event-1",
                "messageType": "unknownFutureValue",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": None,
                "body": {"contentType": "html", "content": "<systemEventMessage/>"},
                "eventDetail": {
                    "@odata.type": "#microsoft.graph.membersRemovedEventMessageDetail",
                    "members": [{"id": "source-2", "displayName": "Removed Person"}],
                    "initiator": {"user": {"id": "source-1", "displayName": "Owner"}},
                },
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    target_chat_id, count = import_chat(Graph(), bundle, {"source-1": "target-1", "source-2": "target-2"}, state)
    assert count == 1
    assert "Owner removed Removed Person from the chat" in posted[0]["body"]["content"]
    assert posted[0]["from"]["user"]["id"] == "target-1"


def test_import_chat_skips_message_from_unmapped_sender(tmp_path):
    """A message from a sender with no target mapping must be skipped instead
    of migrated under a fallback identity."""
    posted = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path == "/chats":
                return {"id": "target-chat-1"}
            if path.startswith("/chats/target-chat-1?"):
                return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
            if method == "POST" and path.endswith("/messages"):
                posted.append(kwargs["json"])
                return {}
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-3", "displayName": "Departed User"}},
                "body": {"contentType": "text", "content": "hello everyone"},
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    target_chat_id, count = import_chat(Graph(), bundle, {"source-1": "target-1", "source-2": "target-2"}, state)
    assert count == 0
    assert posted == []
    assert state.status("teams-chat-message", "message-1") == "skipped"


def test_import_chat_migrates_unmapped_sender_when_opted_in(tmp_path):
    """With include_unmapped_senders=True, a message from an unmapped sender is
    migrated under a proxy identity with a note instead of being skipped."""
    posted = []

    class Graph:
        def request(self, method, path, **kwargs):
            if path == "/chats":
                return {"id": "target-chat-1"}
            if path.startswith("/chats/target-chat-1?"):
                return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
            if method == "POST" and path.endswith("/messages"):
                posted.append(kwargs["json"])
                return {}
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-3", "displayName": "Departed User"}},
                "body": {"contentType": "text", "content": "hello everyone"},
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    target_chat_id, count = import_chat(
        Graph(),
        bundle,
        {"source-1": "target-1", "source-2": "target-2"},
        state,
        include_unmapped_senders=True,
    )
    assert count == 1
    assert posted[0]["from"]["user"]["id"] == "target-1"
    assert "source-3" in posted[0]["body"]["content"]
    assert "hello everyone" in posted[0]["body"]["content"]
    assert state.status("teams-chat-message", "message-1") == "completed"


def test_import_chat_resume_reopens_migration_and_completes_again(tmp_path):
    """sync_new_messages resume of an already-completed chat must call
    startMigration again (Graph allows re-entering migration mode after
    completeMigration) before importing pending messages, then call
    completeMigration again - not silently post as a live message."""
    calls: list[tuple[str, str]] = []
    posted = []

    class Graph:
        def request(self, method, path, **kwargs):
            calls.append((method, path))
            if path == "/chats/target-chat-1/messages?$top=1":
                return {"value": [{"id": "existing"}]}
            if path.startswith("/chats/target-chat-1?"):
                return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
            if method == "POST" and path.endswith("/messages"):
                posted.append(kwargs["json"])
                return {}
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-1"}},
                "body": {"contentType": "text", "content": "backfilled"},
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "completed", "target-chat-1", teams_type="group")
    target_chat_id, count = import_chat(
        Graph(),
        bundle,
        {"source-1": "target-1", "source-2": "target-2"},
        state,
        sync_new_messages=True,
    )
    assert count == 1
    assert posted[0]["body"]["content"] == "backfilled"
    assert any(path.endswith("/startMigration") for _, path in calls)
    assert any(path.endswith("/completeMigration") for _, path in calls)
    assert state.status("teams-chat", "chat-1") == "completed"


def test_import_chat_resume_still_raises_clear_error_if_graph_keeps_blocking_writes(tmp_path):
    """Even after reopening the migration window, Graph might still refuse to
    accept messages (e.g. the resuming app isn't the same one that started
    the original migration session) - this must raise immediately with a
    clear, actionable error rather than looping forever."""
    from migration.common.graph import GraphError

    attempts = {"count": 0}

    class Graph:
        def request(self, method, path, **kwargs):
            if path == "/chats/target-chat-1/messages?$top=1":
                return {"value": [{"id": "existing"}]}
            if path.startswith("/chats/target-chat-1?"):
                return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
            if method == "POST" and path.endswith("/messages"):
                attempts["count"] += 1
                raise GraphError('POST failed (403): {"error":{"code":"Forbidden","message":"InsufficientPrivileges","innerError":{"code":"1","message":"MessageWritesBlocked-Thread is not marked for import"}}}')
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-1", "displayName": "Owner"}},
                "body": {"contentType": "text", "content": "backfilled message"},
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "completed", "target-chat-1", teams_type="group")
    try:
        import_chat(
            Graph(),
            bundle,
            {"source-1": "target-1", "source-2": "target-2"},
            state,
            sync_new_messages=True,
            allow_non_historical_import=True,
        )
        assert False, "expected a GraphError"
    except GraphError as error:
        assert "no longer in migration mode" in str(error)
    # No retry attempted - this failure mode is unfixable by adjusting the timestamp.
    assert attempts["count"] == 1


def test_import_chat_retries_with_sequential_timestamp_when_backdate_rejected(tmp_path):
    """A message rejected only because its timestamp predates the chat's
    (still open) migration window - genuinely fixable by retrying with a
    later, sequential timestamp instead of the message's true original one."""
    from migration.common.graph import GraphError

    posted = []
    attempts = {"count": 0}

    class Graph:
        def request(self, method, path, **kwargs):
            if path == "/chats/target-chat-1/messages?$top=1":
                return {"value": [{"id": "existing"}]}
            if path == "/chats/target-chat-1?$select=createdDateTime":
                # Resyncing an already-completed chat skips the backdate check
                # entirely (its createdDateTime was already set on the original
                # migration) - this is only hit by the mid-loop fallback lookup.
                return {"createdDateTime": "2025-01-05T00:00:00Z"}
            if method == "POST" and path.endswith("/messages"):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise GraphError('POST failed (403): {"error":{"code":"Forbidden","message":"createdDateTime is less than thread creation time"}}')
                posted.append(kwargs["json"])
                return {}
            return {}

        def pages(self, path):
            return []

    bundle = {
        "chat": {"id": "chat-1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}, {"userId": "source-2"}],
        "messages": [
            {
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-1", "displayName": "Owner"}},
                "body": {"contentType": "text", "content": "backfilled message"},
            },
        ],
    }
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "completed", "target-chat-1", teams_type="group")
    target_chat_id, count = import_chat(
        Graph(),
        bundle,
        {"source-1": "target-1", "source-2": "target-2"},
        state,
        sync_new_messages=True,
        allow_non_historical_import=True,
    )
    assert count == 1
    assert posted[0]["body"]["content"] == "backfilled message"
    assert posted[0]["createdDateTime"] == "2025-01-05T00:00:00.001Z"


def test_import_chat_starts_with_source_creation_time(tmp_path):
    class Graph:
        def __init__(self):
            self.calls = []

        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            if path == "/chats":
                return {"id": "target-chat-1"}
            return None

    bundle = {
        "chat": {"id": "chat-1", "chatType": "oneOnOne", "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": "source-1"}],
        "messages": [],
    }
    graph = Graph()
    state = StateStore(tmp_path / "state.sqlite")
    import_chat(graph, bundle, {"source-1": "target-1"}, state)
    start = next(call for call in graph.calls if call[1] == "/chats/target-chat-1/startMigration")
    # conversationCreationDateTime is backdated 1s before the source chat's own
    # createdDateTime so it's strictly earlier than the first migrated message.
    assert start[2]["json"] == {"conversationCreationDateTime": "2024-12-31T23:59:59.000Z"}
