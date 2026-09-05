from migration.common.checkpoint import StateStore


def test_state_store_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    assert store.status("file", "source-1") is None
    store.mark("file", "source-1", "completed", "target-1")
    assert store.status("file", "source-1") == "completed"
    store.close()


def test_batched_writes_visible_before_flush(tmp_path):
    store = StateStore(tmp_path / "state.sqlite", batch_size=10)
    store.mark("file", "source-1", "completed", "target-1")
    # not flushed yet (batch_size=10), but status()/target() must still see it
    assert store.status("file", "source-1") == "completed"
    assert store.target("file", "source-1") == "target-1"
    store.close()


def test_batched_writes_persist_after_flush(tmp_path):
    path = tmp_path / "state.sqlite"
    store = StateStore(path, batch_size=10)
    store.mark("file", "source-1", "completed", "target-1")
    store.flush()
    reopened = StateStore(path, batch_size=10)
    assert reopened.status("file", "source-1") == "completed"
    reopened.close()
    store.close()


def test_batch_auto_flushes_at_batch_size(tmp_path):
    path = tmp_path / "state.sqlite"
    store = StateStore(path, batch_size=2)
    store.mark("file", "source-1", "completed")
    store.mark("file", "source-2", "completed")  # triggers auto-flush at batch_size=2
    reopened = StateStore(path, batch_size=1)
    assert reopened.status("file", "source-1") == "completed"
    assert reopened.status("file", "source-2") == "completed"
    reopened.close()
    store.close()


def test_concurrent_marks_from_multiple_threads(tmp_path):
    import threading

    store = StateStore(tmp_path / "state.sqlite")

    def mark_many(offset):
        for i in range(20):
            store.mark("file", f"source-{offset}-{i}", "completed")

    threads = [threading.Thread(target=mark_many, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for offset in range(4):
        for i in range(20):
            assert store.status("file", f"source-{offset}-{i}") == "completed"
    store.close()


def test_list_ids_filters_by_status_and_workload(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.mark("sharepoint", "drive1:a", "completed", "target-a")
    store.mark("sharepoint", "drive1:b", "failed", detail="boom")
    store.mark("sharepoint", "drive1:c", "failed", detail="boom2")
    store.mark("onedrive", "user1:d", "failed", detail="boom3")
    assert sorted(store.list_ids("sharepoint", "failed")) == ["drive1:b", "drive1:c"]
    assert store.list_ids("sharepoint", "completed") == ["drive1:a"]
    assert store.list_ids("onedrive", "failed") == ["user1:d"]
    store.close()


def test_list_ids_sees_pending_unflushed_marks(tmp_path):
    store = StateStore(tmp_path / "state.sqlite", batch_size=10)
    store.mark("sharepoint", "drive1:a", "failed", detail="boom")
    # not flushed yet, but list_ids must still see it (same guarantee as status())
    assert store.list_ids("sharepoint", "failed") == ["drive1:a"]
    store.close()


def test_count_by_status(tmp_path):
    store = StateStore(tmp_path / "state.sqlite")
    store.mark("sharepoint", "drive1:a", "completed")
    store.mark("sharepoint", "drive1:b", "completed")
    store.mark("sharepoint", "drive1:c", "failed")
    counts = store.count_by_status("sharepoint")
    assert counts == {"completed": 2, "failed": 1}
    store.close()
