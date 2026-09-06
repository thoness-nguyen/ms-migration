from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

# Each migration area gets its own checkpoint file so a run targeting one
# workload can never accidentally read/write another's checkpoint data.
CHECKPOINT_AREAS = ("sharepoint", "onedrive", "teams")


def open_state_store(state_dir: str | Path, area: str, batch_size: int = 1) -> StateStore:
    """Open (creating the directory/file if needed) the checkpoint database
    for one migration area: `<state_dir>/<area>-checkpoint.sqlite`."""
    if area not in CHECKPOINT_AREAS:
        raise ValueError(f"Unknown checkpoint area: {area!r} (expected one of {CHECKPOINT_AREAS})")
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return StateStore(directory / f"{area}-checkpoint.sqlite", batch_size=batch_size)


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
        # user_key: the mapping-file key (e.g. "thanh.nguyen") this checkpoint row
        # belongs to, for onedrive ("one drive per user") and teams ("chat between
        # these member keys") areas - lets status/validation reporting be grouped
        # per user instead of only per workload. Added after the original schema,
        # so existing databases need an ALTER TABLE to pick it up.
        existing_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(checkpoints)").fetchall()}
        if "user_key" not in existing_columns:
            self.connection.execute("ALTER TABLE checkpoints ADD COLUMN user_key TEXT")
        self.connection.commit()
        self._lock = threading.Lock()
        self._batch_size = max(1, batch_size)
        self._pending: dict[tuple[str, str], tuple[str | None, str, str | None, str | None]] = {}

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

    def list_ids(self, workload: str, status: str, user_key: str | None = None) -> list[str]:
        """All source_ids currently recorded for workload with the given status
        (persisted rows overlaid with any not-yet-flushed pending marks). Lets a
        caller target a retry at just the handful of failed items instead of
        re-walking a whole tree to rediscover them. Pass `user_key` to narrow to
        one user's/chat's items only (onedrive/teams)."""
        with self._lock:
            if user_key is None:
                rows = self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ?",
                    (workload,),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ? AND user_key = ?",
                    (workload, user_key),
                ).fetchall()
            merged: dict[str, str] = dict(rows)
            for (pending_workload, source_id), (_, pending_status, _, pending_user_key) in self._pending.items():
                if pending_workload == workload and (user_key is None or pending_user_key == user_key):
                    merged[source_id] = pending_status
            return [source_id for source_id, row_status in merged.items() if row_status == status]

    def user_keys(self, workload: str) -> list[str]:
        """Distinct user_key values recorded for a workload (persisted + pending),
        so a validation/reporting pass can enumerate "which users/chats were
        migrated" without needing the original mapping file."""
        with self._lock:
            keys = {
                row[0]
                for row in self.connection.execute(
                    "SELECT DISTINCT user_key FROM checkpoints WHERE workload = ? AND user_key IS NOT NULL",
                    (workload,),
                ).fetchall()
            }
            for (pending_workload, _), (_, _, _, pending_user_key) in self._pending.items():
                if pending_workload == workload and pending_user_key is not None:
                    keys.add(pending_user_key)
            return sorted(keys)

    def count_by_status(self, workload: str, user_key: str | None = None) -> dict[str, int]:
        """Status -> count breakdown for a workload, pending writes included.
        Pass `user_key` to narrow to one user's/chat's items only."""
        with self._lock:
            if user_key is None:
                rows = self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ?",
                    (workload,),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT source_id, status FROM checkpoints WHERE workload = ? AND user_key = ?",
                    (workload, user_key),
                ).fetchall()
            merged: dict[str, str] = dict(rows)
            for (pending_workload, source_id), (_, pending_status, _, pending_user_key) in self._pending.items():
                if pending_workload == workload and (user_key is None or pending_user_key == user_key):
                    merged[source_id] = pending_status
            counts: dict[str, int] = {}
            for row_status in merged.values():
                counts[row_status] = counts.get(row_status, 0) + 1
            return counts

    def mark(self, workload: str, source_id: str, status: str, target_id: str | None = None, detail: str | None = None, user_key: str | None = None) -> None:
        with self._lock:
            existing = self._pending.get((workload, source_id))
            # A later mark() for the same item often omits user_key (e.g. a plain
            # retry call) - don't let that erase a user_key set on an earlier mark.
            if user_key is None and existing is not None:
                user_key = existing[3]
            self._pending[(workload, source_id)] = (target_id, status, detail, user_key)
            if len(self._pending) >= self._batch_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._pending:
            return
        rows = [(workload, source_id, target_id, status, detail, user_key) for (workload, source_id), (target_id, status, detail, user_key) in self._pending.items()]
        self.connection.executemany(
            "INSERT INTO checkpoints(workload, source_id, target_id, status, detail, user_key) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workload, source_id) DO UPDATE SET target_id=excluded.target_id, status=excluded.status, detail=excluded.detail, "
            "user_key=COALESCE(excluded.user_key, checkpoints.user_key)",
            rows,
        )
        self.connection.commit()
        self._pending.clear()

    def close(self) -> None:
        self.flush()
        self.connection.close()

