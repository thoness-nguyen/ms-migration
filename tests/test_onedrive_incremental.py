from __future__ import annotations

from migration.common.checkpoint import StateStore
from migration.common.graph import GraphError
from migration.onedrive import incremental, migration as onedrive_migration
from migration.onedrive.incremental import UserJob, copy_changes_since, user_key_for
from migration.orchestration import batch
from migration.sharepoint.incremental import parse_since

SINCE = parse_since("2026-10-20")

DELTAS = {
    "src-drive-alice": [
        {"id": "root", "root": {}, "folder": {}},
        {"id": "docs", "name": "Docs", "folder": {}, "parentReference": {"id": "root"}},
        {"id": "a-new", "name": "new.xlsx", "file": {}, "size": 10, "parentReference": {"id": "docs"}, "lastModifiedDateTime": "2026-11-01T00:00:00Z"},
        {"id": "a-old", "name": "old.pdf", "file": {}, "size": 10, "parentReference": {"id": "root"}, "lastModifiedDateTime": "2025-01-01T00:00:00Z"},
        {"id": "nb", "name": "Notebook", "package": {"type": "oneNote"}, "parentReference": {"id": "root"}},
        {"id": "a-onenote", "name": "Section.one", "file": {}, "size": 5, "parentReference": {"id": "nb"}, "lastModifiedDateTime": "2026-11-02T00:00:00Z"},
    ],
    "src-drive-bob": [
        {"id": "root", "root": {}, "folder": {}},
        {"id": "b-new", "name": "report.docx", "file": {}, "size": 20, "parentReference": {"id": "root"}, "lastModifiedDateTime": "2026-10-25T00:00:00Z"},
        {"id": "b-bad", "name": "locked.docx", "file": {}, "size": 20, "parentReference": {"id": "root"}, "lastModifiedDateTime": "2026-10-26T00:00:00Z"},
    ],
}


class Response:
    content = b"data"

    def raise_for_status(self):
        return None


class Source:
    base_url = "https://graph.microsoft.com/v1.0"

    def request(self, method, path, **kwargs):
        user = path.split("/")[2]
        return {"id": f"src-drive-{user}"}

    def pages(self, path):
        drive = path.split("/")[2]
        return iter(DELTAS[drive])

    def raw_request(self, method, url, **kwargs):
        return Response()

    def set_throttle_hook(self, hook):
        pass


class Target:
    def __init__(self, fail_names=()):
        self.puts: list[str] = []
        self.fail_names = set(fail_names)

    def request(self, method, path, **kwargs):
        if method == "GET" and path.startswith("/users/"):
            return {"id": f"tgt-drive-{path.split('/')[2]}"}
        if method == "GET":
            raise GraphError("404", status_code=404)
        if method == "POST" and path.endswith("/children"):
            return {"id": f"t-{kwargs['json']['name']}"}
        if method == "PUT":
            if any(name in path for name in self.fail_names):
                raise GraphError("423 locked", status_code=423)
            self.puts.append(path)
            return {"id": f"t-file-{len(self.puts)}"}
        return {}

    def set_throttle_hook(self, hook):
        pass


def _jobs():
    return [UserJob("alice", "alice", "alice-t"), UserJob("bob", "bob", "bob-t")]


def test_changed_files_of_all_users_copied_and_failures_kept_pending(tmp_path):
    state = StateStore(tmp_path / "s.sqlite")
    target = Target(fail_names={"locked.docx"})
    jobs = _jobs()
    copy_changes_since(Source(), target, jobs, state, SINCE, content_concurrency=3)
    alice, bob = jobs
    assert alice.stats["files_copied"] == 1 and alice.stats["failed"] == 0
    assert [item["name"] for item in alice.unresolved] == ["Section.one"]  # reported, not silently dropped
    assert bob.stats["files_copied"] == 1 and bob.stats["failed"] == 1
    assert state.status("onedrive-incremental", "alice:a-new@2026-11-01T00:00:00Z") == "completed"
    assert state.target("onedrive", "alice:a-new") is not None
    assert state.status("onedrive-incremental", "bob:b-bad@2026-10-26T00:00:00Z") == "failed"
    assert incremental.incomplete_reason(bob) == "1 file(s) failed"

    # Resume: completed versions are skipped, the failed one is retried.
    target2 = Target()
    jobs2 = _jobs()
    copy_changes_since(Source(), target2, jobs2, state, SINCE)
    assert len(target2.puts) == 1 and target2.puts[0].endswith("locked.docx:/content")
    assert jobs2[0].stats["skipped"] == 1 and jobs2[1].stats["skipped"] == 1


def test_previous_full_copy_failure_is_recopied_even_if_old(tmp_path):
    state = StateStore(tmp_path / "s.sqlite")
    state.mark("onedrive", "alice:a-old", "failed", detail="timeout")
    jobs = _jobs()[:1]
    copy_changes_since(Source(), Target(), jobs, state, SINCE)
    assert sorted(item["name"] for item, _ in jobs[0].changed) == ["new.xlsx", "old.pdf"]


def test_dry_run_scans_without_writing(tmp_path):
    target = Target()
    jobs = _jobs()
    copy_changes_since(Source(), target, jobs, StateStore(tmp_path / "s.sqlite"), SINCE, dry_run=True)
    assert target.puts == [] and sum(len(job.changed) for job in jobs) == 3


def _plan():
    return {
        "users": [
            {"key": "alice", "source_id": "alice", "target_id": "alice-t"},
            {"key": "bob", "source_id": "bob", "target_id": "bob-t"},
            {"key": "travis", "source_id": "travis", "target_id": "travis-t"},
            {"key": "cecilia", "source_id": "cecilia", "target_id": "cecilia-t", "skip": True, "skip_reason": "excluded user"},
        ],
        "workloads": {"onedrive": {
            "modified_since": "2026-10-20",
            "users": [{"user": "alice"}, {"user": "bob"}, {"user": "travis", "skip": True, "skip_reason": "special case"}, {"user": "cecilia"}],
        }},
    }


def test_batch_incremental_gates_user_completion_and_skips_excluded_users(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ensure_user_drive", lambda *a, **k: True)
    state = StateStore(tmp_path / "s.sqlite")
    results = {r["user"]: r for r in batch._run_batch_onedrive(Source(), Target(fail_names={"locked.docx"}), _plan(), state, dry_run=False)}
    assert results["travis"]["status"] == "skipped" and "special case" in results["travis"]["reason"]
    assert results["cecilia"]["status"] == "skipped"
    # alice has an unresolved OneNote file, bob a failed file: neither is completed.
    assert results["alice"]["status"] == "completed_with_failures"
    assert results["bob"]["status"] == "completed_with_failures"
    assert state.status("batch-onedrive-incremental", user_key_for(UserJob("bob", "bob", "bob-t"), SINCE)) == "pending"
    assert state.status("batch-onedrive", "travis") is None  # skip is never checkpointed
    assert state.status("onedrive-incremental", "travis:x") is None


def test_full_copy_streams_large_files_instead_of_loading_them(tmp_path, monkeypatch):
    calls = {}

    def fake_stream(source_graph, target_graph, drive_id, item_id, size, upload_url, chunk_size=None):
        calls.update(drive_id=drive_id, item_id=item_id, size=size, upload_url=upload_url)
        return {"id": "t-big"}

    monkeypatch.setattr(onedrive_migration, "stream_copy_large_file", fake_stream)

    class FullTarget:
        def request(self, method, path, **kwargs):
            if method == "GET":
                raise GraphError("404", status_code=404)
            if path.endswith("createUploadSession"):
                return {"uploadUrl": "https://upload"}
            raise AssertionError(f"unexpected {method} {path}")

    class FullSource:
        base_url = "https://graph"

        def raw_request(self, *a, **k):
            raise AssertionError("large file must not be downloaded in one request")

    import threading
    state = StateStore(tmp_path / "s.sqlite")
    stats = {"files_copied": 0}
    item = {"id": "big", "name": "video.mp4", "size": 50 * 1024 * 1024, "parentReference": {"driveId": "src-drive"}}
    onedrive_migration._transfer_one_file(FullSource(), FullTarget(), "alice", "alice-t", item, "alice:big", "root",
                                          state, "alice", None, False, stats, threading.Lock())
    assert calls == {"drive_id": "src-drive", "item_id": "big", "size": 50 * 1024 * 1024, "upload_url": "https://upload"}
    assert state.status("onedrive", "alice:big") == "completed" and stats["files_copied"] == 1



def test_per_user_modified_since_mixes_with_full_copy_users(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ensure_user_drive", lambda *a, **k: True)
    full_copy_calls = []
    monkeypatch.setattr(batch, "copy_drive", lambda *a, **k: full_copy_calls.append(k.get("user_key")) or {"files_copied": 0, "failed": 0, "discovery_complete": True})
    plan = _plan()
    onedrive = plan["workloads"]["onedrive"]
    del onedrive["modified_since"]
    onedrive["users"] = [
        {"user": "alice", "modified_since": "2026-10-20"},
        {"user": "bob"},  # no date -> full copy
        {"user": "travis", "skip": True, "skip_reason": "special case"},
    ]
    results = {r["user"]: r for r in batch._run_batch_onedrive(Source(), Target(), plan, StateStore(tmp_path / "s.sqlite"), dry_run=False)}
    assert results["alice"]["mode"] == "incremental"
    assert full_copy_calls == ["bob"]
    assert results["travis"]["status"] == "skipped"
