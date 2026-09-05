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
