from migration.common.checkpoint import StateStore
from migration.teams.services import _import_body, _import_payload, import_chat, target_member


def test_target_member_uses_target_identity():
    member = target_member({"userId": "source-1"}, {"source-1": "target-1"})
    assert member["user@odata.bind"].endswith("users('target-1')")


def test_import_body_plain_text():
    message = {"body": {"contentType": "text", "content": "hello"}, "attachments": []}
    assert _import_body(message) == {"contentType": "text", "content": "hello"}


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
    assert start[2]["json"] == {"conversationCreationDateTime": "2025-01-01T00:00:00Z"}
