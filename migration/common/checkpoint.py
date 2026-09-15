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
        existing_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(checkpoints)").fetchall()}
        for column in ("user_key", "permission_status"):
            if column not in existing_columns:
                self.connection.execute(f"ALTER TABLE checkpoints ADD COLUMN {column} TEXT")
        self.connection.commit()
        self._lock = threading.Lock()
        self._batch_size = max(1, batch_size)
        # (target_id, status, detail, user_key, permission_status)
        self._pending: dict[tuple[str, str], tuple[str | None, str, str | None, str | None, str | None]] = {}

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

    def user_key_for(self, workload: str, source_id: str) -> str | None:
        with self._lock:
            pending = self._pending.get((workload, source_id))
            if pending is not None:
                return pending[3]
            row = self.connection.execute(
                "SELECT user_key FROM checkpoints WHERE workload = ? AND source_id = ?",
                (workload, source_id),
            ).fetchone()
            return row[0] if row else None

    def permission_status_for(self, workload: str, source_id: str) -> str | None:
        with self._lock:
            pending = self._pending.get((workload, source_id))
            if pending is not None:
                return pending[4]
            row = self.connection.execute(
                "SELECT permission_status FROM checkpoints WHERE workload = ? AND source_id = ?",
                (workload, source_id),
            ).fetchone()
            return row[0] if row else None

    def list_ids(self, workload: str, status: str | None = None, user_key: str | None = None) -> list[str]:
        with self._lock:
            self._flush_locked()
            query = "SELECT source_id FROM checkpoints WHERE workload = ?"
            params: list[str] = [workload]
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            if user_key is not None:
                query += " AND user_key = ?"
                params.append(user_key)
            return [row[0] for row in self.connection.execute(query, params).fetchall()]

    def user_keys(self, workload: str) -> list[str]:
        with self._lock:
            self._flush_locked()
            rows = self.connection.execute(
                "SELECT DISTINCT user_key FROM checkpoints WHERE workload = ? AND user_key IS NOT NULL",
                (workload,),
            ).fetchall()
            return [row[0] for row in rows]

    def count_by_status(self, workload: str, user_key: str | None = None) -> dict[str, int]:
        with self._lock:
            self._flush_locked()
            query = "SELECT status, COUNT(*) FROM checkpoints WHERE workload = ?"
            params: list[str] = [workload]
            if user_key is not None:
                query += " AND user_key = ?"
                params.append(user_key)
            query += " GROUP BY status"
            return {row[0]: row[1] for row in self.connection.execute(query, params).fetchall()}

    def mark(
        self,
        workload: str,
        source_id: str,
        status: str,
        target_id: str | None = None,
        detail: str | None = None,
        user_key: str | None = None,
        permission_status: str | None = None,
    ) -> None:
        with self._lock:
            existing = self._pending.get((workload, source_id))
            if user_key is None and existing is not None:
                user_key = existing[3]
            if permission_status is None and existing is not None:
                permission_status = existing[4]
            self._pending[(workload, source_id)] = (target_id, status, detail, user_key, permission_status)
            if len(self._pending) >= self._batch_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        rows = [
            (workload, source_id, target_id, status, detail, user_key, permission_status)
            for (workload, source_id), (target_id, status, detail, user_key, permission_status) in self._pending.items()
        ]
        self.connection.executemany(
            "INSERT INTO checkpoints(workload, source_id, target_id, status, detail, user_key, permission_status) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workload, source_id) DO UPDATE SET target_id=excluded.target_id, status=excluded.status, detail=excluded.detail, "
            "user_key=COALESCE(excluded.user_key, checkpoints.user_key), permission_status=COALESCE(excluded.permission_status, checkpoints.permission_status)",
            rows,
        )
        self.connection.commit()
        self._pending.clear()

    def close(self) -> None:
        self.flush()
        self.connection.close()


def open_state_store(state_dir: str | Path, area: str) -> StateStore:
    """Open (creating the directory if needed) the per-area checkpoint DB at <state_dir>/<area>-checkpoint.sqlite."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return StateStore(directory / f"{area}-checkpoint.sqlite")
