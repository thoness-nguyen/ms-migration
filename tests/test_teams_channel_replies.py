from __future__ import annotations

import pytest

from migration.common.checkpoint import StateStore
from migration.common.graph import GraphError
from migration.orchestration.batch import _chat_precheck_exclusion
from migration.teams.services import extract_channel, import_channel_messages, import_chat

BASE = "/teams/tt/channels/tc/messages"


def _msg(message_id, sender, text, ts, replies=None, **extra):
    message = {"id": message_id, "createdDateTime": ts, "from": {"user": {"id": sender}}, "body": {"contentType": "text", "content": text}, **extra}
    if replies is not None:
        message["replies"] = replies
    return message


class Graph:
    def __init__(self, target_messages=None, fail_paths=()):
        self.posts: list[tuple[str, dict]] = []
        self.target_messages = target_messages or []
        self.fail_paths = fail_paths
        self.counter = 0

    def request(self, method, path, **kwargs):
        if method == "POST" and path.endswith(("/messages", "/replies")):
            self.posts.append((path, kwargs["json"]))
            self.counter += 1
            return {"id": f"t{self.counter}"}
        return {}

    def pages(self, path):
        if path == BASE:
            return iter(self.target_messages)
        return iter([])


MAP = {"s1": "u1", "s2": "u2"}


def test_extract_channel_attaches_replies_to_each_root():
    class Source:
        def request(self, method, path, **kwargs):
            return {"id": "tc"}

        def pages(self, path):
            if path.endswith("/messages"):
                return iter([{"id": "r1"}])
            if path.endswith("/messages/r1/replies"):
                return iter([{"id": "x1"}, {"id": "x2"}])
            raise AssertionError(path)

    bundle = extract_channel(Source(), "tt", "tc")
    assert [r["id"] for r in bundle["messages"][0]["replies"]] == ["x1", "x2"]


def test_replies_are_posted_under_their_new_parent_with_true_timestamps(tmp_path):
    graph = Graph()
    state = StateStore(tmp_path / "s.sqlite")
    root = _msg("r1", "s1", "post", "2026-01-01T10:00:00Z", replies=[
        _msg("x2", "s1", "second", "2026-01-01T12:00:00Z"),
        _msg("x1", "s2", "first", "2026-01-01T11:00:00Z"),
    ])
    count = import_channel_messages(graph, "tt", "tc", [root], state, user_map=dict(MAP))
    assert count == 3
    paths = [p for p, _ in graph.posts]
    assert paths == [BASE, f"{BASE}/t1/replies", f"{BASE}/t1/replies"]
    assert [payload["createdDateTime"] for _, payload in graph.posts[1:]] == ["2026-01-01T11:00:00.000Z", "2026-01-01T12:00:00.000Z"]
    assert graph.posts[1][1]["from"]["user"]["id"] == "u2"
    assert state.target("channel-message", "r1") == "t1"
    assert state.status("channel-reply", "r1/x1") == "completed"


def test_completed_channel_backfills_replies_for_legacy_root_found_by_timestamp(tmp_path):
    """Channels migrated before reply support: root completed WITHOUT a stored
    target id - sync reopens the channel and locates the parent by timestamp."""
    graph = Graph(target_messages=[{"id": "legacy-target", "createdDateTime": "2026-01-01T10:00:00.000Z"}])
    state = StateStore(tmp_path / "s.sqlite")
    state.mark("teams-channel", "tc", "completed")
    state.mark("channel-message", "r1", "completed")
    root = _msg("r1", "s1", "post", "2026-01-01T10:00:00Z", replies=[_msg("x1", "s2", "late reply", "2026-03-01T00:00:00Z")])
    count = import_channel_messages(graph, "tt", "tc", [root], state, user_map=dict(MAP), sync_new_messages=True)
    assert count == 1
    assert graph.posts[0][0] == f"{BASE}/legacy-target/replies"
    assert state.target("channel-message", "r1") == "legacy-target"
    assert state.status("teams-channel", "tc") == "completed"


def test_deleted_root_with_replies_gets_labelled_placeholder(tmp_path):
    graph = Graph()
    state = StateStore(tmp_path / "s.sqlite")
    root = _msg("r1", "s1", "", "2026-01-01T10:00:00Z", deletedDateTime="2026-01-02T00:00:00Z", replies=[_msg("x1", "s2", "still here", "2026-01-01T11:00:00Z")])
    import_channel_messages(graph, "tt", "tc", [root], state, user_map=dict(MAP))
    assert "Original post deleted" in graph.posts[0][1]["body"]["content"]
    assert graph.posts[1][0] == f"{BASE}/t1/replies"
    assert state.status("channel-message", "r1") == "skipped"
    assert state.status("channel-reply", "r1/x1") == "completed"


def test_unlocatable_parent_fails_channel_and_leaves_replies_pending(tmp_path):
    graph = Graph(target_messages=[])
    state = StateStore(tmp_path / "s.sqlite")
    state.mark("teams-channel", "tc", "completed")
    state.mark("channel-message", "r1", "completed")
    root = _msg("r1", "s1", "post", "2026-01-01T10:00:00Z", replies=[_msg("x1", "s2", "orphan", "2026-01-01T11:00:00Z")])
    with pytest.raises(GraphError, match="parent post was not found"):
        import_channel_messages(graph, "tt", "tc", [root], state, user_map=dict(MAP), sync_new_messages=True)
    assert state.status("channel-reply", "r1/x1") is None


def test_excluded_user_is_skipped_in_channels_and_chats(tmp_path):
    graph = Graph()
    state = StateStore(tmp_path / "s.sqlite")
    root = _msg("r1", "s1", "post", "2026-01-01T10:00:00Z", replies=[_msg("x1", "sx", "by excluded", "2026-01-01T11:00:00Z")])
    import_channel_messages(graph, "tt", "tc", [root, _msg("r2", "sx", "excluded root", "2026-01-01T12:00:00Z")], state, user_map=dict(MAP), include_unmapped_senders=True, skip_sender_ids={"sx"})
    assert [p for p, _ in graph.posts] == [BASE]  # only r1 - nothing re-posted under a proxy
    assert state.status("channel-reply", "r1/x1") == "skipped"
    assert state.status("channel-message", "r2") == "skipped"

    chat_state = StateStore(tmp_path / "c.sqlite")
    bundle = {"chat": {"id": "c1", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"}, "members": [{"userId": "s1"}, {"userId": "s2"}],
              "messages": [_msg("m1", "sx", "hi", "2025-01-02T00:00:00Z")]}
    _, count = import_chat(None, bundle, dict(MAP), chat_state, dry_run=True, include_unmapped_senders=True, skip_sender_ids={"sx"})
    assert count == 0


def test_one_on_one_with_excluded_user_is_excluded_by_precheck():
    a, b = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
    error = _chat_precheck_exclusion(f"19:{a}_{b}@unq.gbl.spaces", {a: "ta"}, {b})
    assert error is not None and error.reason == "excluded-user"
