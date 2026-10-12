import json

from migration.orchestration.batch import load_plan, run_batch
from migration.common.checkpoint import StateStore


class FakeGraph:
    def __init__(self):
        self.calls = []


def test_load_json_mapping_file(tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"source": "target"}), encoding="utf-8")
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text("migration_id: test\nchats:\n  - source_chat_id: chat-1\n    user_map: map.json\n", encoding="utf-8")
    assert load_plan(plan_file)["chats"][0]["source_chat_id"] == "chat-1"


def test_batch_rejects_unmapped_member(tmp_path):
    class Source:
        def request(self, method, path):
            return {"id": "chat-1", "chatType": "oneOnOne"}

        def pages(self, path):
            return iter([{"userId": "source-2"}] if path.endswith("members") else [])

    states = {
        "sharepoint": StateStore(tmp_path / "sharepoint-checkpoint.sqlite"),
        "onedrive": StateStore(tmp_path / "onedrive-checkpoint.sqlite"),
        "teams": StateStore(tmp_path / "teams-checkpoint.sqlite"),
    }
    report = run_batch(Source(), FakeGraph(), {"chats": [{"source_chat_id": "chat-1", "user_map": {"source-1": "target-1"}}]}, tmp_path, states, tmp_path, True)
    teams_report = report["areas"]["teams"]
    assert teams_report["results"][0]["status"] == "failed"
    assert "source-2" in teams_report["results"][0]["error"]


def test_batch_chat_writes_fresh_bundle_under_data_team_chats(tmp_path):
    class Source:
        def request(self, method, path):
            assert method == "GET"
            assert path == "/chats/chat-1"
            return {
                "id": "chat-1",
                "chatType": "group",
                "createdDateTime": "2025-01-01T00:00:00Z",
            }

        def pages(self, path):
            if path.endswith("/members"):
                return iter([{"userId": "source-1"}, {"userId": "source-2"}])
            return iter([{
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-1"}},
                "body": {"contentType": "text", "content": "fresh chat message"},
            }])

    plan_dir = tmp_path / "config"
    plan_dir.mkdir()
    bundle_dir = tmp_path / "data" / "team-chats"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "chat-001.chat-bundle.json"
    bundle_path.write_text("stale", encoding="utf-8")
    state = StateStore(tmp_path / "teams-checkpoint.sqlite")
    states = {
        "sharepoint": StateStore(tmp_path / "sharepoint-checkpoint.sqlite"),
        "onedrive": StateStore(tmp_path / "onedrive-checkpoint.sqlite"),
        "teams": state,
    }
    plan = {
        "chats": [{
            "name": "chat-001",
            "source_chat_id": "chat-1",
            "sync_new_messages": True,
        }],
        "user_mapping": [
            {"source_id": "source-1", "target_id": "target-1"},
            {"source_id": "source-2", "target_id": "target-2"},
        ],
    }

    run_batch(Source(), FakeGraph(), plan, plan_dir, states, tmp_path, dry_run=True, area="teams", teams_scope="chats")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["chat"]["id"] == "chat-1"
    assert bundle["messages"][0]["id"] == "message-1"
    assert bundle["messages"][0]["body"]["content"] == "fresh chat message"


def test_batch_channel_fetches_and_overwrites_bundle_without_messages_file(tmp_path):
    class Source:
        def request(self, method, path):
            assert method == "GET"
            assert path == "/teams/source-team/channels/source-channel"
            return {"id": "source-channel", "displayName": "General"}

        def pages(self, path):
            assert path == "/teams/source-team/channels/source-channel/messages"
            return iter([{
                "id": "message-1",
                "createdDateTime": "2025-01-02T00:00:00Z",
                "from": {"user": {"id": "source-1"}},
                "body": {"contentType": "text", "content": "fresh channel message"},
            }])

    plan_dir = tmp_path / "config"
    plan_dir.mkdir()
    bundle_dir = tmp_path / "data" / "teams"
    bundle_dir.mkdir(parents=True)
    bundle_path = bundle_dir / "General.channel-bundle.json"
    bundle_path.write_text("stale", encoding="utf-8")
    states = {
        "sharepoint": StateStore(tmp_path / "sharepoint-checkpoint.sqlite"),
        "onedrive": StateStore(tmp_path / "onedrive-checkpoint.sqlite"),
        "teams": StateStore(tmp_path / "teams-checkpoint.sqlite"),
    }
    plan = {
        "chats": [],
        "user_mapping": [],
        "workloads": {
            "teams": {
                "channels": [{
                    "name": "General",
                    "source_team_id": "source-team",
                    "source_channel_id": "source-channel",
                    "target_team_id": "target-team",
                    "target_channel_id": "target-channel",
                    "sync_new_messages": True,
                }],
            },
        },
    }

    run_batch(Source(), FakeGraph(), plan, plan_dir, states, tmp_path, dry_run=True, area="teams", teams_scope="channels")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["channel"]["displayName"] == "General"
    assert bundle["messages"][0]["id"] == "message-1"
    assert bundle["messages"][0]["body"]["content"] == "fresh channel message"
