from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class StateStore:
    """Small durable checkpoint store used to make migration jobs restartable.

    Thread-safe (a single sqlite3 connection guarded by a lock) so concurrent
    workers can all mark progress against the same store. Supports optional
    write batching: pending marks are buffered in memory and flushed as one
    transaction every `batch_size` calls, which cuts down on fsync-per-item
    overhead for high-throughput runs. status()/target() always see pending
    (not-yet-flushed) writes too, so behavior is identical to batch_size=1
    (the default, and the only mode the original callers ever used) from the
    caller's point of view - only crash-recovery granularity changes: at most
    `batch_size - 1` marks could need reprocessing after an unexpected kill.
    """

    def __init__(self, path: str | Path, batch_size: int = 1):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints (workload TEXT NOT NULL, source_id TEXT NOT NULL, target_id TEXT, status TEXT NOT NULL, detail TEXT, PRIMARY KEY (workload, source_id))"
        )
        self.connection.commit()
        self._lock = threading.Lock()
        self._batch_size = max(1, batch_size)
        self._pending: dict[tuple[str, str], tuple[str | None, str, str | None]] = {}

    def status(self, workload: str, source_id: str) -> str | None:
        with self._lock:
            pending = self._pending.get((workload, source_id))
            if pending is not None:
                return pending[1]
            row = self.connection.execute(
                "SELECT status FROM checkpoints WHERE workload = ? AND source_id = ?",
                (workload, source_id),
            ).fetchone()
            return row[0] if row else None

    def target(self, workload: str, source_id: str) -> str | None:
        with self._lock:
            pending = self._pending.get((workload, source_id))
            if pending is not None:
                return pending[0]
            row = self.connection.execute(
                "SELECT target_id FROM checkpoints WHERE workload = ? AND source_id = ?",
                (workload, source_id),
            ).fetchone()
            return row[0] if row else None

    def list_ids(self, workload: str, status: str) -> list[str]:
        """All source_ids currently recorded for workload with the given status
        (persisted rows overlaid with any not-yet-flushed pending marks). Lets a
        caller target a retry at just the handful of failed items instead of
        re-walking a whole tree to rediscover them."""
        with self._lock:
            merged: dict[str, str] = dict(
                self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ?",
                    (workload,),
                ).fetchall()
            )
            for (pending_workload, source_id), (_, pending_status, _) in self._pending.items():
                if pending_workload == workload:
                    merged[source_id] = pending_status
            return [source_id for source_id, row_status in merged.items() if row_status == status]

    def count_by_status(self, workload: str) -> dict[str, int]:
        """Status -> count breakdown for a workload, pending writes included."""
        with self._lock:
            merged: dict[str, str] = dict(
                self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ?",
                    (workload,),
                ).fetchall()
            )
            for (pending_workload, source_id), (_, pending_status, _) in self._pending.items():
                if pending_workload == workload:
                    merged[source_id] = pending_status
            counts: dict[str, int] = {}
            for row_status in merged.values():
                counts[row_status] = counts.get(row_status, 0) + 1
            return counts

    def mark(self, workload: str, source_id: str, status: str, target_id: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            self._pending[(workload, source_id)] = (target_id, status, detail)
            if len(self._pending) >= self._batch_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        rows = [(workload, source_id, target_id, status, detail) for (workload, source_id), (target_id, status, detail) in self._pending.items()]
        self.connection.executemany(
            "INSERT INTO checkpoints(workload, source_id, target_id, status, detail) VALUES (?, ?, ?, ?, ?) ON CONFLICT(workload, source_id) DO UPDATE SET target_id=excluded.target_id, status=excluded.status, detail=excluded.detail",
            rows,
        )
        self.connection.commit()
        self._pending.clear()

    def close(self) -> None:
        self.flush()
        self.connection.close()
