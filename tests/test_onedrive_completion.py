import requests
import pytest

from migration.common.checkpoint import StateStore
from migration.common.graph import GraphClient, GraphError, is_mysite_not_found
from migration.common.retry import with_retry
from migration.onedrive.migration import copy_drive, retry_failed_onedrive_files
from migration.onedrive.validation import validate_user_drive
from migration.orchestration.batch import _run_batch_onedrive

MYSITE = '{"error":{"code":"ResourceNotFound","message":"User\'s mysite not found."}}'


class Resp:
    def __init__(self, data=b"abc"):
        self.content = data

    def raise_for_status(self):
        pass


class SourceGraph:
    base_url = "https://g"

    def __init__(self, tree, fail_listing=False, mysite=False):
        self.tree = tree  # parent id -> list of items
        self.fail_listing = fail_listing
        self.mysite = mysite

    def set_throttle_hook(self, hook):
        pass

    def pages(self, path):
        if self.fail_listing:
            raise GraphError("boom", status_code=500)
        parent = "root" if path.endswith("/root/children") else path.split("/items/")[1].split("/")[0]
        yield from self.tree.get(parent, [])

    def request(self, method, path, **kw):
        if "$select=id,driveType" in path:
            if self.mysite:
                raise GraphError(f"GET {path} failed (404): {MYSITE}", status_code=404)
            return {"id": "drive"}
        raise GraphError("not found", status_code=404)

    def raw_request(self, method, url, **kw):
        return Resp(b"abc")


class TargetGraph:
    base_url = "https://g"

    def __init__(self, existing=None, mysite=False, put_error=None, fail_folder=None):
        self.existing = existing or {}  # name -> item
        self.mysite = mysite
        self.put_error = put_error
        self.fail_folder = fail_folder
        self.calls = 0
        self.puts = []

    def set_throttle_hook(self, hook):
        pass

    def request(self, method, path, **kw):
        self.calls += 1
        if "$select=id,driveType" in path:
            if self.mysite:
                raise GraphError(f"GET {path} failed (404): {MYSITE}", status_code=404)
            return {"id": "drive"}
        if method == "GET":
            name = path.split(":/")[-1]
            if name in self.existing:
                return self.existing[name]
            raise GraphError("not found", status_code=404)
        if method == "POST":
            name = kw["json"]["name"]
            if name == self.fail_folder:
                raise GraphError("conflict", status_code=409)
            return {"id": f"T-{name}"}
        if method == "PUT":
            if self.put_error:
                raise self.put_error
            self.puts.append(path)
            return {"id": "T-file"}
        raise AssertionError(path)


def f(i, size=3):
    return {"id": i, "name": f"{i}.txt", "size": size, "file": {}}


def d(i):
    return {"id": i, "name": i, "folder": {}}


@pytest.fixture
def state(tmp_path):
    return StateStore(tmp_path / "onedrive-checkpoint.sqlite")


def run(src, tgt, state, **kw):
    return copy_drive(src, tgt, "S", "T", state, user_key="u", **kw)


def test_unexpected_worker_exception_is_not_swallowed(state):
    stats = run(SourceGraph({"root": [f("a")]}), TargetGraph(put_error=RuntimeError("weird")), state)
    assert stats["failed"] == 1
    assert state.status("onedrive", "S:a") == "failed"


def test_same_name_different_size_is_reuploaded(state):
    tgt = TargetGraph(existing={"a.txt": {"id": "OLD", "size": 1}})
    stats = run(SourceGraph({"root": [f("a", size=3)]}), tgt, state)
    assert tgt.puts and stats["failed"] == 0
    assert state.target("onedrive", "S:a") == "T-file"


def test_same_name_same_size_is_skipped(state):
    tgt = TargetGraph(existing={"a.txt": {"id": "OLD", "size": 3}})
    run(SourceGraph({"root": [f("a")]}), tgt, state)
    assert not tgt.puts and state.target("onedrive", "S:a") == "OLD"


def test_folder_failure_does_not_hide_siblings(state):
    src = SourceGraph({"root": [d("bad"), d("good"), f("a")], "good": [f("b")]})
    stats = run(src, TargetGraph(fail_folder="bad"), state)
    assert stats["discovery_complete"] is False and stats["folders_failed"] == 1
    assert state.status("onedrive", "S:bad") == "failed"
    assert state.status("onedrive", "S:a") == "completed"
    assert state.status("onedrive", "S:b") == "completed"


def test_listing_failure_marks_discovery_incomplete(state):
    stats = run(SourceGraph({}, fail_listing=True), TargetGraph(), state)
    assert stats["discovery_complete"] is False


def test_retry_only_never_proves_completeness(state):
    stats = retry_failed_onedrive_files(SourceGraph({}), TargetGraph(), "S", "T", state)
    assert stats["discovery_complete"] is False


def test_retry_unresolvable_parent_stays_failed(state):
    state.mark("onedrive", "S:a", "failed", user_key="u")

    class Src(SourceGraph):
        def request(self, method, path, **kw):
            return {"id": "a", "name": "a.txt", "size": 3, "parentReference": {"id": "X", "path": "/drive/root:/dir"}}

    stats = retry_failed_onedrive_files(Src({}), TargetGraph(), "S", "T", state)
    assert stats["failed"] == 1 and state.status("onedrive", "S:a") == "failed"


def plan(workers=None):
    return {
        "users": [{"key": "u", "source_id": "S", "target_id": "T"}, {"key": "v", "source_id": "S2", "target_id": "T2"}],
        "workloads": {"onedrive": {"users": [{"user": "u"}, {"user": "v"}]}},
    }


def test_leftover_failed_rows_keep_user_pending(state):
    state.mark("onedrive", "S:old", "failed", user_key="u")
    res = _run_batch_onedrive(SourceGraph({"root": [f("a")]}), TargetGraph(), plan(), state, False)
    assert res[0]["status"] == "completed_with_failures"
    assert state.status("batch-onedrive", "S") == "pending"


def test_clean_full_run_completes(state):
    _run_batch_onedrive(SourceGraph({"root": [f("a")]}), TargetGraph(), plan(), state, False)
    assert state.status("batch-onedrive", "S") == "completed"


def test_retry_only_does_not_promote_pending_user(state):
    state.mark("batch-onedrive", "S", "pending", user_key="u")
    _run_batch_onedrive(SourceGraph({}), TargetGraph(), plan(), state, False, retry_failed_only=True)
    assert state.status("batch-onedrive", "S") == "pending"


def test_target_mysite_missing_skips_without_retry_and_continues(state):
    tgt = TargetGraph(mysite=True)
    res = _run_batch_onedrive(SourceGraph({"root": [f("a")]}), tgt, plan(), state, False)
    assert [r["status"] for r in res] == ["skipped", "skipped"]
    assert "mysite not found" in res[0]["reason"].lower()
    assert state.status("batch-onedrive", "S") == "skipped"
    assert tgt.puts == []
    assert tgt.calls == 2  # one provisioning check per user, no retries


def test_mysite_raised_during_copy_is_not_retried(state):
    calls = []

    class Tgt(TargetGraph):
        def request(self, method, path, **kw):
            if method == "POST":
                calls.append(1)
                raise GraphError(f"POST {path} failed (404): {MYSITE}", status_code=404)
            return super().request(method, path, **kw)

    res = _run_batch_onedrive(SourceGraph({"root": [d("x")]}), Tgt(), plan(), state, False)
    assert res[0]["status"] == "skipped" and len(calls) == 2  # u and v, once each
    assert state.status("batch-onedrive", "S") == "skipped"


def test_with_retry_permanent_vs_transient():
    n = {"c": 0}

    def permanent():
        n["c"] += 1
        raise GraphError(MYSITE, status_code=404)

    _, ok, _, attempts = with_retry(permanent, backoff_base_seconds=0, is_permanent=is_mysite_not_found)
    assert not ok and attempts == 1

    n["c"] = 0

    def transient():
        n["c"] += 1
        raise GraphError("throttled", status_code=429)

    _, ok, _, attempts = with_retry(transient, backoff_base_seconds=0, is_permanent=is_mysite_not_found)
    assert not ok and attempts == 3 and n["c"] == 3


def test_interrupt_leaves_user_pending_and_resume_skips_done(state):
    class Tgt(TargetGraph):
        def request(self, method, path, **kw):
            if method == "POST" and kw["json"]["name"] == "second":
                raise KeyboardInterrupt
            return super().request(method, path, **kw)

    src = SourceGraph({"root": [d("first"), d("second")]})
    with pytest.raises(KeyboardInterrupt):
        _run_batch_onedrive(src, Tgt(), plan(), state, False)
    assert state.status("batch-onedrive", "S") == "pending"
    assert state.status("onedrive", "S:first") == "completed"

    tgt = TargetGraph()
    _run_batch_onedrive(src, tgt, plan(), state, False)
    assert state.status("batch-onedrive", "S") == "completed"
    assert state.target("onedrive", "S:first") == "T-first"  # reused, not recreated


def test_validation_reports_unfinished_items(state):
    state.mark("onedrive", "S:a", "failed", user_key="u")
    report = validate_user_drive(SourceGraph({}), TargetGraph(), "S", "T", state)
    assert report["status"] == "mismatch" and report["unfinished"]


# --- verify_completed: opt-in repair of pre-existing "completed" checkpoint rows ---


def test_verify_completed_reuploads_truncated_file(state):
    # Checkpoint says this file is done, but the target copy is smaller than the
    # source (e.g. a historical truncated upload) - only caught when asked to verify.
    state.mark("onedrive", "S:a", "completed", "OLD", user_key="u")
    tgt = TargetGraph(existing={"a.txt": {"id": "OLD", "size": 1}})
    stats = run(SourceGraph({"root": [f("a", size=3)]}), tgt, state, verify_completed=True)
    assert tgt.puts and stats["failed"] == 0
    assert state.target("onedrive", "S:a") == "T-file"


def test_verify_completed_skips_intact_file_without_reupload(state):
    state.mark("onedrive", "S:a", "completed", "OLD", user_key="u")
    tgt = TargetGraph(existing={"a.txt": {"id": "OLD", "size": 3}})
    stats = run(SourceGraph({"root": [f("a", size=3)]}), tgt, state, verify_completed=True)
    assert not tgt.puts  # already intact - re-confirmed for free, not re-uploaded
    assert state.target("onedrive", "S:a") == "OLD"


def test_verify_completed_off_by_default_trusts_stale_checkpoint(state):
    # Same truncated scenario as above, but without opting in: the existing
    # "completed" row is trusted as-is (current default/steady-state behavior).
    state.mark("onedrive", "S:a", "completed", "OLD", user_key="u")
    tgt = TargetGraph(existing={"a.txt": {"id": "OLD", "size": 1}})
    stats = run(SourceGraph({"root": [f("a", size=3)]}), tgt, state)
    assert not tgt.puts and tgt.calls == 0  # checkpoint trusted - no target call at all
    assert state.target("onedrive", "S:a") == "OLD"


# --- raw_request: 429/5xx/network retry with backoff; permanent 4xx is not retried ---


class _FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeSession:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = 0
        self.headers = {}

    def mount(self, *a, **kw):
        pass

    def request(self, method, url, **kwargs):
        self.calls += 1
        step = self.scripted.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _client(scripted):
    return GraphClient("tok", session=_FakeSession(scripted)), scripted


def test_raw_request_retries_429_with_backoff(monkeypatch):
    monkeypatch.setattr("migration.common.graph.time.sleep", lambda s: None)
    client, _ = _client([
        _FakeResponse(429, headers={"Retry-After": "0"}),
        _FakeResponse(200),
    ])
    response = client.raw_request("GET", "https://x")
    assert response.status_code == 200
    assert client.session.calls == 2
    assert client.metrics["throttled_429"] == 1


def test_raw_request_retries_5xx_with_backoff(monkeypatch):
    monkeypatch.setattr("migration.common.graph.time.sleep", lambda s: None)
    client, _ = _client([_FakeResponse(503), _FakeResponse(503), _FakeResponse(200)])
    response = client.raw_request("PUT", "https://x")
    assert response.status_code == 200
    assert client.session.calls == 3
    assert client.metrics["server_errors_5xx"] == 2


def test_raw_request_retries_network_errors_with_backoff(monkeypatch):
    monkeypatch.setattr("migration.common.graph.time.sleep", lambda s: None)
    client, _ = _client([
        requests.exceptions.ConnectionError("reset"),
        _FakeResponse(200),
    ])
    response = client.raw_request("GET", "https://x")
    assert response.status_code == 200
    assert client.session.calls == 2


def test_raw_request_does_not_retry_permanent_4xx(monkeypatch):
    monkeypatch.setattr("migration.common.graph.time.sleep", lambda s: None)
    client, _ = _client([_FakeResponse(404)])
    response = client.raw_request("GET", "https://x")
    assert response.status_code == 404
    assert client.session.calls == 1  # no retry - caller's raise_for_status() handles it
