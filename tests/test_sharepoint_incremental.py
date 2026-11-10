from __future__ import annotations

from migration.common.checkpoint import StateStore
from migration.common.graph import GraphError
from migration.sharepoint import incremental
from migration.sharepoint.incremental import copy_library_changes_since, find_changed_files, parse_since

SINCE = parse_since("2026-09-10")

DELTA = [
    {"id": "root", "root": {}, "folder": {}},
    {"id": "fA", "name": "Projects", "folder": {}, "parentReference": {"id": "root"}},
    {"id": "fB", "name": "2026", "folder": {}, "parentReference": {"id": "fA"}},
    {"id": "new", "name": "new.xlsx", "file": {}, "size": 10, "parentReference": {"id": "fB"}, "lastModifiedDateTime": "2026-10-01T00:00:00Z", "createdDateTime": "2026-10-01T00:00:00Z"},
    {"id": "edited", "name": "old-but-edited.docx", "file": {}, "size": 20, "parentReference": {"id": "fA"}, "lastModifiedDateTime": "2026-09-15T00:00:00Z", "createdDateTime": "2024-01-01T00:00:00Z"},
    {"id": "untouched", "name": "untouched.pdf", "file": {}, "size": 30, "parentReference": {"id": "root"}, "lastModifiedDateTime": "2025-01-01T00:00:00Z", "createdDateTime": "2025-01-01T00:00:00Z"},
    {"id": "failedbefore", "name": "retry.pdf", "file": {}, "size": 5, "parentReference": {"id": "root"}, "lastModifiedDateTime": "2025-01-01T00:00:00Z"},
    {"id": "gone", "name": "deleted.txt", "deleted": {}, "parentReference": {"id": "root"}},
]


class Response:
    def __init__(self, content=b"data"):
        self.content = content

    def raise_for_status(self):
        return None


class Source:
    base_url = "https://graph.microsoft.com/v1.0"

    def pages(self, path):
        assert "/root/delta" in path
        return iter(DELTA)

    def raw_request(self, method, url, **kwargs):
        return Response()

    def set_throttle_hook(self, hook):
        pass


class Target:
    def __init__(self):
        self.puts: list[str] = []
        self.created_folders: list[str] = []

    def request(self, method, path, **kwargs):
        if method == "GET" and ":/" in path:
            raise GraphError("404", status_code=404)  # nothing exists yet
        if method == "POST" and path.endswith("/children"):
            self.created_folders.append(kwargs["json"]["name"])
            return {"id": f"t-{kwargs['json']['name']}"}
        if method == "PUT":
            self.puts.append(path)
            return {"id": f"t-file-{len(self.puts)}"}
        return {}

    def set_throttle_hook(self, hook):
        pass


def test_only_files_changed_since_cutoff_and_previous_failures_are_selected(tmp_path):
    state = StateStore(tmp_path / "s.sqlite")
    state.mark("sharepoint", "drive:failedbefore", "failed", detail="401")
    changed, scanned = find_changed_files(Source(), "drive", SINCE, state)
    assert scanned == len(DELTA)
    assert sorted((item["id"], path) for item, path in changed) == [("edited", "Projects"), ("failedbefore", ""), ("new", "Projects/2026")]


def test_changed_files_are_uploaded_overwriting_and_checkpointed(tmp_path, monkeypatch):
    monkeypatch.setattr(incremental, "copy_item_permissions", lambda *a, **k: None)
    state = StateStore(tmp_path / "s.sqlite")
    target = Target()
    stats = copy_library_changes_since(Source(), target, "drive", "tdrive", state, SINCE)
    assert stats["changed"] == 2 and stats["files_copied"] == 2 and stats["failed"] == 0
    assert target.created_folders == ["Projects", "2026"]  # parents created before children, once each
    assert state.status("sharepoint-incremental", "drive:new@2026-10-01T00:00:00Z") == "completed"
    assert state.target("sharepoint", "drive:new") is not None

    # Resume: same versions are not copied again.
    target2 = Target()
    again = copy_library_changes_since(Source(), target2, "drive", "tdrive", state, SINCE)
    assert again["skipped"] == 2 and target2.puts == []


def test_dry_run_reports_size_without_writing(tmp_path):
    target = Target()
    stats = copy_library_changes_since(Source(), target, "drive", "tdrive", StateStore(tmp_path / "s.sqlite"), SINCE, dry_run=True)
    assert stats["changed_bytes"] == 30 and target.puts == [] and target.created_folders == []


def test_parse_since_accepts_date_or_timestamp():
    assert parse_since("2026-09-10").isoformat() == "2026-09-10T00:00:00+00:00"
    assert parse_since("2026-09-09T17:00:00Z").isoformat() == "2026-09-09T17:00:00+00:00"
