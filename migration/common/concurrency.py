"""Bounded, adaptive concurrency for migration workers (ARCHITECHTURE_UPGRADE.md ss9).

Two pieces:
- AdaptiveGate: a resizable semaphore. Shrinks when the Graph API throttles
  (429), grows back slowly after a run of consecutive successes. This is the
  "control signal" behavior the design calls for - 429 isn't just a retry,
  it's a signal to reduce pressure.
- run_workers(): a small bounded ThreadPoolExecutor helper. Graph calls are
  I/O-bound (network round trips), so a thread pool is sufficient - no need
  for multiprocessing. One failed item never blocks or cancels the others.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class AdaptiveGate:
    """Resizable concurrency limiter shared by a pool of workers.

    acquire()/release() bound how many workers may be doing Graph work at
    once (like a semaphore, but its capacity can shrink/grow at runtime).
    """

    def __init__(self, initial: int, minimum: int = 1, maximum: int | None = None, grow_after: int = 5):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._capacity = max(minimum, initial)
        self._minimum = minimum
        self._maximum = maximum if maximum is not None else max(self._capacity, initial)
        self._grow_after = grow_after
        self._in_use = 0
        self._consecutive_successes = 0
        self._throttle_events = 0

    def acquire(self) -> None:
        with self._cond:
            while self._in_use >= self._capacity:
                self._cond.wait()
            self._in_use += 1

    def release(self) -> None:
        with self._cond:
            self._in_use -= 1
            self._cond.notify_all()

    def record_success(self) -> None:
        with self._cond:
            self._consecutive_successes += 1
            if self._consecutive_successes >= self._grow_after and self._capacity < self._maximum:
                self._capacity += 1
                self._consecutive_successes = 0
                self._cond.notify_all()

    def record_throttle(self) -> None:
        with self._cond:
            self._throttle_events += 1
            self._consecutive_successes = 0
            self._capacity = max(self._minimum, self._capacity // 2)
            # capacity shrank - no need to notify, waiters just keep waiting

    def __enter__(self) -> "AdaptiveGate":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    @property
    def capacity(self) -> int:
        with self._lock:
            return self._capacity

    @property
    def throttle_events(self) -> int:
        with self._lock:
            return self._throttle_events


def run_workers(
    items: Iterable[T],
    worker_fn: Callable[[T], R],
    max_workers: int = 4,
    gate: AdaptiveGate | None = None,
) -> list[tuple[T, R | None, BaseException | None]]:
    """Run worker_fn(item) concurrently for every item, bounded by max_workers
    (and further throttled by `gate` if provided).

    Returns a list of (item, result, error) tuples - one per item, in
    completion order. A raised exception in one item never prevents the
    others from running (failure isolation).
    """
    items = list(items)
    if not items:
        return []

    def _run(item: T) -> tuple[T, R | None, BaseException | None]:
        if gate is not None:
            gate.acquire()
        try:
            result = worker_fn(item)
            if gate is not None:
                gate.record_success()
            return item, result, None
        except BaseException as error:  # noqa: BLE001 - isolate failures per item
            return item, None, error
        finally:
            if gate is not None:
                gate.release()

    results: list[tuple[T, R | None, BaseException | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run, item): item for item in items}
        for future in as_completed(futures):
            results.append(future.result())
    return results
