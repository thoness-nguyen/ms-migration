from migration.common.concurrency import AdaptiveGate, run_workers


def test_adaptive_gate_shrinks_on_throttle():
    gate = AdaptiveGate(initial=8, minimum=1, maximum=8)
    gate.record_throttle()
    assert gate.capacity == 4
    assert gate.throttle_events == 1


def test_adaptive_gate_grows_back_after_sustained_success():
    gate = AdaptiveGate(initial=2, minimum=1, maximum=8, grow_after=3)
    for _ in range(3):
        gate.record_success()
    assert gate.capacity == 3


def test_adaptive_gate_never_shrinks_below_minimum():
    gate = AdaptiveGate(initial=1, minimum=1, maximum=4)
    gate.record_throttle()
    assert gate.capacity == 1


def test_adaptive_gate_never_grows_above_maximum():
    gate = AdaptiveGate(initial=4, minimum=1, maximum=4, grow_after=1)
    for _ in range(10):
        gate.record_success()
    assert gate.capacity == 4


def test_adaptive_gate_bounds_concurrent_acquisitions():
    import threading
    import time

    gate = AdaptiveGate(initial=2, minimum=1, maximum=2)
    active = []
    max_active = []
    lock = threading.Lock()

    def work():
        gate.acquire()
        with lock:
            active.append(1)
            max_active.append(len(active))
        time.sleep(0.05)
        with lock:
            active.pop()
        gate.release()

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max(max_active) <= 2


def test_run_workers_processes_all_items():
    results = run_workers([1, 2, 3, 4], lambda x: x * 2, max_workers=2)
    by_item = {item: result for item, result, error in results}
    assert by_item == {1: 2, 2: 4, 3: 6, 4: 8}
    assert all(error is None for _, _, error in results)


def test_run_workers_isolates_failures():
    def worker(item):
        if item == 2:
            raise ValueError("boom")
        return item

    results = run_workers([1, 2, 3], worker, max_workers=2)
    by_item = {item: (result, error) for item, result, error in results}
    assert by_item[1] == (1, None)
    assert by_item[3] == (3, None)
    result, error = by_item[2]
    assert result is None
    assert isinstance(error, ValueError)


def test_run_workers_empty_list():
    assert run_workers([], lambda x: x, max_workers=4) == []
