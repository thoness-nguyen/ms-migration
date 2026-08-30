from migration.orchestration.batch import load_plan


def test_hierarchical_plan_validates_workload_user(tmp_path):
    (tmp_path / "users.json").write_text('[{"key":"alice","source_id":"source","target_id":"target"}]', encoding="utf-8")
    (tmp_path / "drive.yaml").write_text("users:\n  - user: alice\n    action: graph-copy\n", encoding="utf-8")
    plan = tmp_path / "plan.yaml"
    plan.write_text("users: users.json\nworkloads:\n  onedrive: drive.yaml\n", encoding="utf-8")
    assert load_plan(plan)["workloads"]["onedrive"]["users"][0]["user"] == "alice"