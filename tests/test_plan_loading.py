from migration.orchestration.batch import load_plan


def test_hierarchical_plan_validates_workload_user(tmp_path):
    (tmp_path / "users.json").write_text('[{"key":"alice","source_id":"source","target_id":"target"}]', encoding="utf-8")
    (tmp_path / "drive.yaml").write_text("users:\n  - user: alice\n    action: graph-copy\n", encoding="utf-8")
    plan = tmp_path / "plan.yaml"
    plan.write_text("users: users.json\nworkloads:\n  onedrive: drive.yaml\n", encoding="utf-8")
    assert load_plan(plan)["workloads"]["onedrive"]["users"][0]["user"] == "alice"

def test_shipped_example_plan_loads():
    """config/examples must stay a working, self-contained template."""
    from pathlib import Path

    from migration.orchestration.batch import _concurrency, _skipped_user_ids, load_plan

    plan = load_plan(Path("config/examples/migration.example.yaml"))
    assert len(plan["workloads"]["sharepoint"]["sites"]) == 2
    assert plan["workloads"]["exchange"]["mailboxes"][0]["user"] == "alice"
    assert _concurrency(plan, "sharepoint", "content", 4) == 8
    assert _skipped_user_ids(plan, Path("config/examples")) == {"00000000-0000-0000-0000-00000000a003"}
