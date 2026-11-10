from __future__ import annotations

import os
import time

from migration.common.checkpoint import StateStore
from migration.orchestration.batch import run_batch

CHAT = "19:g@thread.v2"


class Source:
    def __init__(self):
        self.message_paths: list[str] = []

    def request(self, method, path, **kwargs):
        return {"id": CHAT, "chatType": "group", "createdDateTime": "2025-01-01T00:00:00Z"}

    def pages(self, path):
        if path.endswith("/members"):
            return iter([{"userId": "s1"}, {"userId": "s2"}])
        self.message_paths.append(path)
        return iter([{"id": "new-1", "createdDateTime": "2026-11-10T01:00:00Z", "from": {"user": {"id": "s1"}}, "body": {"contentType": "text", "content": "new"}}])


class Target:
    def __init__(self):
        self.posts = 0

    def request(self, method, path, **kwargs):
        if method == "POST" and path.endswith("/messages"):
            self.posts += 1
        return {}

    def pages(self, path):
        return iter([])


def _run(tmp_path, state, plan_extra=None):
    plan_dir = tmp_path / "config"
    plan_dir.mkdir(exist_ok=True)
    states = {"sharepoint": StateStore(tmp_path / "sp.sqlite"), "onedrive": StateStore(tmp_path / "od.sqlite"), "teams": state}
    plan = {"chats": [{"name": "g", "source_chat_id": CHAT, "sync_new_messages": True}],
            "user_mapping": [{"source_id": "s1", "target_id": "t1"}, {"source_id": "s2", "target_id": "t2"}], **(plan_extra or {})}
    source, target = Source(), Target()
    report = run_batch(source, target, plan, plan_dir, states, tmp_path / "r", area="teams", teams_scope="chats")
    return source, target, report["areas"]["teams"]["results"][0]


def _completed_state(tmp_path):
    state = StateStore(tmp_path / "teams.sqlite")
    state.mark("batch-chat", CHAT, "completed", "tc")
    state.mark("teams-chat", CHAT, "completed", "tc")
    return state


def test_completed_chat_with_watermark_reads_only_newer_messages(tmp_path):
    state = _completed_state(tmp_path)
    state.mark("chat-sync-watermark", CHAT, "completed", "2026-11-09T00:00:00Z")
    source, target, result = _run(tmp_path, state)
    assert source.message_paths == [f"/chats/{CHAT}/messages?$top=50&$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt 2026-11-09T00:00:00Z"]
    assert result["status"] == "completed" and target.posts == 1
    assert (tmp_path / "data" / "team-chats" / "g.chat-delta.json").exists()
    assert not (tmp_path / "data" / "team-chats" / "g.chat-bundle.json").exists()  # full bundle never overwritten by a delta
    assert state.target("chat-sync-watermark", CHAT) > "2026-11-09T00:00:00Z"  # advanced for the next run


def test_first_incremental_run_seeds_watermark_from_full_bundle_time(tmp_path):
    state = _completed_state(tmp_path)
    bundle = tmp_path / "data" / "team-chats" / "g.chat-bundle.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("{}", encoding="utf-8")
    two_days_ago = time.time() - 2 * 86400
    os.utime(bundle, (two_days_ago, two_days_ago))
    source, _, _ = _run(tmp_path, state)
    assert "$filter=lastModifiedDateTime gt" in source.message_paths[0]
    assert bundle.read_text(encoding="utf-8") == "{}"


def test_not_completed_chats_are_always_read_in_full(tmp_path):
    state = StateStore(tmp_path / "teams.sqlite")
    state.mark("batch-chat", CHAT, "failed", detail="earlier error")
    state.mark("chat-sync-watermark", CHAT, "completed", "2026-11-09T00:00:00Z")
    source, _, _ = _run(tmp_path, state)
    assert source.message_paths == [f"/chats/{CHAT}/messages"]


def test_full_sync_forces_complete_read(tmp_path):
    state = _completed_state(tmp_path)
    state.mark("chat-sync-watermark", CHAT, "completed", "2026-11-09T00:00:00Z")
    source, _, _ = _run(tmp_path, state, {"full_sync": True})
    assert source.message_paths == [f"/chats/{CHAT}/messages"]
