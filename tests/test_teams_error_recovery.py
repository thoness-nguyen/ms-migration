"""Regression tests for the Teams chat failure buckets observed in
state/teams-checkpoint.sqlite (2026-11 run) - one test per root cause."""
from __future__ import annotations

import pytest

from migration.common.checkpoint import StateStore
from migration.common.graph import GraphError
from migration.orchestration.batch import run_batch
from migration.teams import services
from migration.teams.services import ChatNotMigratableError, _import_body, _pages_resilient, import_chat

NOT_MARKED = 'POST failed (403): {"error":{"code":"Forbidden","message":"InsufficientPrivileges","innerError":{"code":"1","message":"MessageWritesBlocked-Thread is not marked for import"}}}'
USER_NOT_FOUND = "POST https://graph.microsoft.com/v1.0/chats/t/messages failed (400): {\"error\":{\"code\":\"BadRequest\",\"message\":\"Specified user with Id 'target-2' could not be found in the tenant.\"}}"


def _bundle(messages, chat_type="group", members=("source-1", "source-2")):
    return {
        "chat": {"id": "chat-1", "chatType": chat_type, "createdDateTime": "2025-01-01T00:00:00Z"},
        "members": [{"userId": member} for member in members],
        "messages": messages,
    }


def _message(message_id, sender, text, minute=0):
    return {
        "id": message_id,
        "createdDateTime": f"2025-01-02T00:{minute:02d}:00Z",
        "from": {"user": {"id": sender}},
        "body": {"contentType": "text", "content": text},
    }


class RecordingGraph:
    """Answers every call with {} unless a handler overrides it."""

    def __init__(self, handler=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.handler = handler

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.handler:
            result = self.handler(method, path, kwargs, self)
            if result is not None:
                return result
        if path.startswith("/chats/target-chat-1?"):
            return {"createdDateTime": "2024-12-31T23:59:59.000Z"}
        if path == "/chats":
            return {"id": "target-chat-1"}
        return {}

    def pages(self, path):
        return []

    def posted(self):
        return [kwargs["json"] for method, path, kwargs in self.calls if method == "POST" and path.endswith("/messages")]


# --- "Unicode '' in the payload is null or empty" ----------------------------

def test_import_body_rewrites_emoji_with_empty_alt():
    body = _import_body({"body": {"contentType": "html", "content": '<div>thanks <emoji id="praying" alt="" title=""></emoji></div>'}})
    assert body["content"] == "<div>thanks (praying)</div>"


def test_import_body_keeps_emoji_with_alt_text():
    content = '<div>ok <emoji id="like" alt="\U0001F44D" title="Like"></emoji></div>'
    assert _import_body({"body": {"contentType": "html", "content": content}})["content"] == content


# --- "has already completed migration" on completeMigration ---------------------

def test_completed_chat_without_pending_messages_is_not_rerun(tmp_path):
    """teams-chat=completed and every message completed: nothing to do, even
    when the target-side "has messages" probe says empty (it used to gate
    already_completed, so the chat was re-run as a brand-new migration)."""
    def handler(method, path, kwargs, graph):
        if path.endswith("/messages?$top=1"):
            return {"value": []}
        return None

    graph = RecordingGraph(handler)
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "completed", "target-chat-1")
    state.mark("teams-chat-message", "m1", "completed")
    target, count = import_chat(graph, _bundle([_message("m1", "source-1", "x")]), {"source-1": "t1", "source-2": "t2"}, state, sync_new_messages=True)
    assert (target, count) == ("target-chat-1", 0)
    assert not any(path.endswith(("/startMigration", "/completeMigration")) for _, path, _ in graph.calls)


def test_sync_of_completed_chat_with_only_skipped_messages_does_not_fail(tmp_path):
    """The real shape of the 62 'has already completed migration' failures:
    skipped (here: deleted) messages count as pending, so sync reopens the chat,
    posts nothing, and completeMigration reports it's already completed."""
    def handler(method, path, kwargs, graph):
        if path.endswith("/completeMigration"):
            raise GraphError("POST .../completeMigration failed (400): Chat 'x' has already completed migration.", status_code=400)
        return None

    deleted = dict(_message("m1", "source-1", ""), deletedDateTime="2025-01-03T00:00:00Z")
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "completed", "target-chat-1")
    state.mark("teams-chat-message", "m1", "skipped", detail="deleted")
    target, count = import_chat(RecordingGraph(handler), _bundle([deleted]), {"source-1": "t1", "source-2": "t2"}, state, sync_new_messages=True)
    assert (target, count) == ("target-chat-1", 0)
    assert state.status("teams-chat", "chat-1") == "completed"


def test_complete_migration_already_completed_is_treated_as_success(tmp_path):
    def handler(method, path, kwargs, graph):
        if path.endswith("/completeMigration"):
            raise GraphError("POST .../completeMigration failed (400): Chat 'x' has already completed migration.", status_code=400)
        return None

    state = StateStore(tmp_path / "state.sqlite")
    _, count = import_chat(RecordingGraph(handler), _bundle([_message("m1", "source-1", "hi")]), {"source-1": "t1", "source-2": "t2"}, state)
    assert count == 1
    assert state.status("teams-chat", "chat-1") == "completed"


# --- "no longer in migration mode" on a resumed "started" chat ------------------

def test_resumed_started_chat_reopens_migration_once_and_retries(tmp_path):
    attempts = {"posts": 0}

    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages"):
            attempts["posts"] += 1
            if attempts["posts"] == 1:
                raise GraphError(NOT_MARKED, status_code=403)
        return None

    graph = RecordingGraph(handler)
    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "started", "target-chat-1")
    _, count = import_chat(graph, _bundle([_message("m1", "source-1", "hi")]), {"source-1": "t1", "source-2": "t2"}, state)
    assert count == 1
    assert sum(1 for _, path, _ in graph.calls if path.endswith("/startMigration")) == 1
    assert state.status("teams-chat-message", "m1") == "completed"


def test_resumed_started_chat_still_fails_clearly_if_reopen_does_not_help(tmp_path):
    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages"):
            raise GraphError(NOT_MARKED, status_code=403)
        return None

    state = StateStore(tmp_path / "state.sqlite")
    state.mark("teams-chat", "chat-1", "started", "target-chat-1")
    with pytest.raises(GraphError, match="no longer in migration mode"):
        import_chat(RecordingGraph(handler), _bundle([_message("m1", "source-1", "hi")]), {"source-1": "t1", "source-2": "t2"}, state)
    assert state.status("teams-chat-message", "m1") is None


# --- "Specified user with Id ... could not be found in the tenant" ---------------

def test_invalid_target_sender_falls_back_to_proxy_identity(tmp_path):
    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages") and kwargs["json"]["from"]["user"]["id"] == "target-2":
            raise GraphError(USER_NOT_FOUND, status_code=400)
        return None

    graph = RecordingGraph(handler)
    state = StateStore(tmp_path / "state.sqlite")
    messages = [_message("m1", "source-2", "first", 0), _message("m2", "source-2", "second", 1)]
    _, count = import_chat(graph, _bundle(messages), {"source-1": "target-1", "source-2": "target-2"}, state, include_unmapped_senders=True)
    assert count == 2
    accepted = [payload for payload in graph.posted() if payload["from"]["user"]["id"] == "target-1"]
    assert len(accepted) == 2
    assert all("Originally sent by source-2" in payload["body"]["content"] for payload in accepted)
    # the invalid identity is only tried once - m2 goes straight to the fallback
    assert sum(1 for payload in graph.posted() if payload["from"]["user"]["id"] == "target-2") == 1
    assert state.status("teams-chat-message", "m2") == "completed"


def test_invalid_target_sender_is_skipped_when_unmapped_senders_not_included(tmp_path):
    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages") and kwargs["json"]["from"]["user"]["id"] == "target-2":
            raise GraphError(USER_NOT_FOUND, status_code=400)
        return None

    state = StateStore(tmp_path / "state.sqlite")
    messages = [_message("m1", "source-2", "lost", 0), _message("m2", "source-1", "kept", 1)]
    _, count = import_chat(RecordingGraph(handler), _bundle(messages), {"source-1": "target-1", "source-2": "target-2"}, state)
    assert count == 1
    assert state.status("teams-chat-message", "m1") == "skipped"
    assert state.status("teams-chat-message", "m2") == "completed"
    assert state.status("teams-chat", "chat-1") == "completed"


# --- "Creation of 'OneOnOne' chat requires 2 members" ---------------------------

def test_one_on_one_with_departed_participant_is_not_migratable(tmp_path):
    def handler(method, path, kwargs, graph):
        if path == "/chats":
            raise GraphError("POST /chats failed (400): Creation of 'OneOnOne' chat requires 2 members.", status_code=400)
        return None

    state = StateStore(tmp_path / "state.sqlite")
    with pytest.raises(ChatNotMigratableError) as raised:
        import_chat(RecordingGraph(handler), _bundle([], chat_type="oneOnOne", members=("source-1",)), {"source-1": "t1"}, state)
    assert raised.value.reason == "unmapped-participant"


# --- "GET ... failed after retries" during extraction ----------------------------

def test_pages_resilient_rereads_listing_after_transient_failure(monkeypatch):
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)
    calls = {"count": 0}

    class Graph:
        def pages(self, path):
            calls["count"] += 1
            yield {"id": "a"}
            if calls["count"] == 1:
                raise GraphError("GET .../messages?$skiptoken=x failed after retries")
            yield {"id": "b"}

    assert [item["id"] for item in _pages_resilient(Graph(), "/chats/c/messages")] == ["a", "b"]


def test_pages_resilient_retries_same_page_on_real_client(monkeypatch):
    """Real GraphClient: a page still throttled after GraphClient's own retries
    is retried in place (same nextLink) - earlier pages are not re-fetched."""
    from migration.common.graph import GraphClient

    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)
    client = GraphClient("token")
    fetched: list[str] = []
    page2_failures = {"left": 2}

    def fake_request(method, path, **kwargs):
        fetched.append(path)
        if path == "/chats/c/messages":
            return {"value": [{"id": "a"}], "@odata.nextLink": "next-page"}
        if page2_failures["left"]:
            page2_failures["left"] -= 1
            raise GraphError("GET next-page failed after retries")
        return {"value": [{"id": "b"}]}

    monkeypatch.setattr(client, "request", fake_request)
    assert [item["id"] for item in _pages_resilient(client, "/chats/c/messages")] == ["a", "b"]
    assert fetched == ["/chats/c/messages", "next-page", "next-page", "next-page"]


def test_pages_resilient_does_not_retry_permanent_errors(monkeypatch):
    monkeypatch.setattr(services.time, "sleep", lambda seconds: pytest.fail("must not wait on a permanent error"))

    class Graph:
        def pages(self, path):
            raise GraphError("GET failed (403): Forbidden", status_code=403)
            yield  # pragma: no cover

    with pytest.raises(GraphError):
        _pages_resilient(Graph(), "/chats/c/messages")


# --- batch-level classification ---------------------------------------------------

def _states(tmp_path):
    return {area: StateStore(tmp_path / f"{area}-checkpoint.sqlite") for area in ("sharepoint", "onedrive", "teams")}


def _run_chats(tmp_path, source, chats, user_mapping):
    states = _states(tmp_path)
    plan_dir = tmp_path / "config"
    plan_dir.mkdir(exist_ok=True)
    plan = {"chats": chats, "user_mapping": user_mapping}
    report = run_batch(source, RecordingGraph(), plan, plan_dir, states, tmp_path / "reports", area="teams", teams_scope="chats")
    return report["areas"]["teams"]["results"], states["teams"]


A = "aaaaaaaa-0000-0000-0000-000000000001"
B = "bbbbbbbb-0000-0000-0000-000000000002"


def test_unmapped_one_on_one_and_meeting_chats_are_excluded_without_graph_calls(tmp_path):
    class Source:
        def request(self, *args, **kwargs):
            pytest.fail("pre-check exclusions must not call Graph")

        def pages(self, path):
            pytest.fail("pre-check exclusions must not call Graph")

    chats = [
        {"name": "one", "source_chat_id": f"19:{A}_{B}@unq.gbl.spaces"},
        {"name": "meeting", "source_chat_id": "19:meeting_abc@thread.v2"},
    ]
    results, state = _run_chats(tmp_path, Source(), chats, [{"source_id": A, "target_id": "t-a"}])
    assert [result["status"] for result in results] == ["excluded", "excluded"]
    assert results[0]["reason"] == "unmapped-participant" and B in results[0]["error"]
    assert results[1]["reason"] == "unsupported-chat-type"
    # checkpoint "skipped" (panel: deliberately excluded) - never "completed"
    assert state.status("batch-chat", f"19:{A}_{B}@unq.gbl.spaces") == "skipped"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (GraphError("GET /chats/x failed (404): NotFound", status_code=404), "source-chat-not-found"),
        (GraphError("GET /chats/x/messages failed (403): AclCheckFailed ... Tenant Id mismatch.", status_code=403), "source-chat-external-tenant"),
    ],
)
def test_permanent_source_extraction_errors_are_excluded(tmp_path, error, reason):
    class Source:
        def request(self, *args, **kwargs):
            raise error

        def pages(self, path):
            raise error

    results, state = _run_chats(tmp_path, Source(), [{"name": "c", "source_chat_id": "19:abc@thread.v2"}], [])
    assert results[0]["status"] == "excluded"
    assert results[0]["reason"] == reason
    assert state.status("batch-chat", "19:abc@thread.v2") == "skipped"


def test_completed_chat_is_never_downgraded_by_a_later_exclusion(tmp_path):
    """sync_new_messages re-checks completed chats; if the source chat has since
    been deleted (404), the completed checkpoint must stay completed."""
    class Source:
        def request(self, *args, **kwargs):
            raise GraphError("GET /chats/x failed (404): NotFound", status_code=404)

        def pages(self, path):
            return []

    states = _states(tmp_path)
    states["teams"].mark("batch-chat", "19:abc@thread.v2", "completed", "target-x")
    plan_dir = tmp_path / "config"
    plan_dir.mkdir()
    plan = {"chats": [{"name": "c", "source_chat_id": "19:abc@thread.v2", "sync_new_messages": True}]}
    report = run_batch(Source(), RecordingGraph(), plan, plan_dir, states, tmp_path / "reports", area="teams", teams_scope="chats")
    assert report["areas"]["teams"]["results"][0]["status"] == "skipped"
    assert states["teams"].status("batch-chat", "19:abc@thread.v2") == "completed"


def test_transient_extraction_error_still_fails_for_retry(tmp_path):
    class Source:
        def request(self, *args, **kwargs):
            raise GraphError("GET /chats/x failed (400): BadRequest", status_code=400)

        def pages(self, path):
            return []

    results, state = _run_chats(tmp_path, Source(), [{"name": "c", "source_chat_id": "19:abc@thread.v2"}], [])
    assert results[0]["status"] == "failed"
    assert state.status("batch-chat", "19:abc@thread.v2") == "failed"


def test_member_without_user_id_does_not_crash_batch(tmp_path):
    """TypeError: '<' not supported between 'str' and 'NoneType' (sorted())."""
    class Source:
        def request(self, method, path, **kwargs):
            return {"id": "19:g@thread.v2", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"}

        def pages(self, path):
            if path.endswith("/members"):
                return iter([{"userId": "s1"}, {"userId": "s2"}, {"userId": None}, {"userId": "s3"}])
            return iter([])

    mapping = [{"source_id": "s1", "target_id": "t1"}, {"source_id": "s2", "target_id": "t2"}]
    results, _ = _run_chats(tmp_path, Source(), [{"name": "g", "source_chat_id": "19:g@thread.v2"}], mapping)
    assert results[0]["status"] == "completed", results[0]


# --- remaining buckets: emoji alt, empty img src, empty body, roster, 412 ---------

def test_rejected_emoji_is_retried_flattened_to_its_text(tmp_path):
    content = 'Hi there! <emoji id="1f603_grinningfacewithbigeyes" alt="\U0001F600" title="Hi"></emoji>'

    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages") and "<emoji" in kwargs["json"]["body"]["content"]:
            raise GraphError("POST failed (400): {\"error\":{\"message\":\"Unicode '\U0001F600' in the payload is not supported\"}}", status_code=400)
        return None

    message = _message("m1", "source-1", "x")
    message["body"] = {"contentType": "html", "content": content}
    graph = RecordingGraph(handler)
    _, count = import_chat(graph, _bundle([message]), {"source-1": "t1", "source-2": "t2"}, StateStore(tmp_path / "s.sqlite"))
    assert count == 1
    assert graph.posted()[-1]["body"]["content"] == "Hi there! \U0001F600"


def test_emoji_falls_back_to_name_when_text_also_rejected(tmp_path):
    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages") and "\U0001F600" in kwargs["json"]["body"]["content"]:
            raise GraphError("POST failed (400): Unicode '\U0001F600' in the payload is not supported", status_code=400)
        return None

    message = _message("m1", "source-1", "x")
    message["body"] = {"contentType": "html", "content": '<emoji id="1f603_grinningfacewithbigeyes" alt="\U0001F600"></emoji>'}
    graph = RecordingGraph(handler)
    import_chat(graph, _bundle([message]), {"source-1": "t1", "source-2": "t2"}, StateStore(tmp_path / "s.sqlite"))
    assert graph.posted()[-1]["body"]["content"] == "(grinningfacewithbigeyes)"


def test_img_with_empty_src_is_replaced():
    body = _import_body({"body": {"contentType": "html", "content": '<p><img itemtype="http://schema.skype.com/InlineImage" src=""></p>'}})
    assert "<img" not in body["content"] and "image not available" in body["content"]


def test_message_reference_quote_is_rendered():
    message = {
        "body": {"contentType": "html", "content": '<attachment id="1648"></attachment>'},
        "attachments": [{"id": "1648", "contentType": "messageReference", "content": '{"messageId":"1648","messagePreview":"See note","messageSender":{"user":{"displayName":"Jason"}}}'}],
    }
    assert _import_body(message)["content"] == "<blockquote>Jason: See note</blockquote>"


def test_empty_message_is_skipped_with_reason_not_failed(tmp_path):
    graph = RecordingGraph()
    state = StateStore(tmp_path / "s.sqlite")
    _, count = import_chat(graph, _bundle([_message("m1", "source-1", ""), _message("m2", "source-1", "real", 1)]), {"source-1": "t1", "source-2": "t2"}, state)
    assert count == 1
    assert state.status("teams-chat-message", "m1") == "skipped"
    assert [p["body"]["content"] for p in graph.posted()] == ["real"]


def test_roster_mismatch_fails_with_mapping_diagnosis_not_misattribution(tmp_path):
    def handler(method, path, kwargs, graph):
        if method == "POST" and path.endswith("/messages"):
            raise GraphError("POST failed (403): MessageWritesBlocked-Message sender is not present in the P2P roster.", status_code=403)
        return None

    graph = RecordingGraph(handler)
    state = StateStore(tmp_path / "s.sqlite")
    with pytest.raises(GraphError, match="user mapping changed after this chat was created"):
        import_chat(graph, _bundle([_message("m1", "source-1", "hi")]), {"source-1": "t1", "source-2": "t2"}, state)
    assert len(graph.posted()) == 1  # no retry under someone else's identity


def test_complete_migration_retries_version_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(services.time, "sleep", lambda seconds: None)
    attempts = {"n": 0}

    def handler(method, path, kwargs, graph):
        if path.endswith("/completeMigration"):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise GraphError("POST failed (412): PreconditionFailed-Version mismatch for thread resource.", status_code=412)
        return None

    state = StateStore(tmp_path / "s.sqlite")
    import_chat(RecordingGraph(handler), _bundle([_message("m1", "source-1", "hi")]), {"source-1": "t1", "source-2": "t2"}, state)
    assert attempts["n"] == 3
    assert state.status("teams-chat", "chat-1") == "completed"


def test_emoji_only_message_is_not_treated_as_empty(tmp_path):
    message = _message("m1", "source-1", "x")
    message["body"] = {"contentType": "html", "content": '<emoji id="like" alt="\U0001F44D" title="Like"></emoji>'}
    graph = RecordingGraph()
    _, count = import_chat(graph, _bundle([message]), {"source-1": "t1", "source-2": "t2"}, StateStore(tmp_path / "s.sqlite"))
    assert count == 1 and len(graph.posted()) == 1


def test_dry_run_never_builds_the_target_writing_attachment_resolver(tmp_path, monkeypatch):
    """--dry-run must not write to the target: the attachment resolver migrates
    missing attachment files on demand, so it must not exist in a dry run."""
    import migration.orchestration.batch as batch

    monkeypatch.setattr(batch, "make_attachment_resolver", lambda *a, **k: pytest.fail("attachment resolver built in dry run"))
    monkeypatch.setattr(batch, "make_image_resolver", lambda *a, **k: pytest.fail("image resolver built in dry run"))

    class Source:
        def request(self, method, path, **kwargs):
            return {"id": "19:g@thread.v2", "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"}

        def pages(self, path):
            return iter([{"userId": "s1"}, {"userId": "s2"}] if path.endswith("/members") else [])

    mapping = [{"source_id": "s1", "target_id": "t1", "source_upn": "a@src", "target_upn": "a@tgt"}, {"source_id": "s2", "target_id": "t2"}]
    plan_dir = tmp_path / "config"
    plan_dir.mkdir()
    report = run_batch(Source(), RecordingGraph(), {"chats": [{"name": "g", "source_chat_id": "19:g@thread.v2"}], "user_mapping": mapping}, plan_dir, _states(tmp_path), tmp_path / "r", dry_run=True, area="teams")
    assert report["areas"]["teams"]["results"][0]["status"] == "dry-run"
