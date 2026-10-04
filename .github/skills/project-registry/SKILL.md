---
name: project-registry
description: Locate the smallest relevant set of code, documentation, checkpoints, reports, and tests for OneDrive and Teams migration tasks.
---

# Project Registry

## Purpose

Use this skill to quickly locate the relevant parts of the repository
without scanning the entire codebase.

The goal is to minimize unnecessary context while still understanding
the execution path required for a task.

---

# Current Priorities

## Priority 1 — OneDrive

Active debugging and maintenance.

Main concern:

Users may be marked as completed while their OneDrive content is
still incomplete.

## Priority 2 — Teams

Maintenance only.

## Priority 3 — SharePoint

Stable.

Do not inspect SharePoint unless explicitly requested or a shared
infrastructure change directly affects it.

---

# Repository Map

## Orchestration

Path:

`migration/orchestration/`

Important files:

- `cli.py`
- `batch.py`
- `validate.py`

Responsibilities:

- Load migration plans.
- Create Graph clients.
- Open checkpoint stores.
- Coordinate workloads.
- Execute migrations.
- Generate reports.
- Execute post-migration validation.

---

# OneDrive

Path:

`migration/onedrive/`

Important files:

- `migration.py`
- `validation.py`

Important functions:

- `copy_drive()`
- `retry_failed_onedrive_files()`
- `validate_user_drive()`

Responsibilities:

- Per-user OneDrive migration.
- Folder discovery.
- Folder preparation.
- Permission migration where enabled.
- User-level migration completion.
- File transfer.
- Concurrent file workers.
- Per-item checkpointing.
- Post-migration validation.

---

# OneDrive Checkpoint Model

## Per-item

Workload:

`onedrive`

Key format:

`{source_user}:{item_id}`

Represents:

- Files
- Folders

## Per-user

Workload:

`batch-onedrive`

Represents:

- Overall user migration state.

Important:

The per-user checkpoint must not be treated as authoritative without
considering the underlying per-item state.

---

# Teams

Path:

`migration/teams/`

Important files:

- `services.py`
- `validation.py`

Important functions:

- `extract_chat()`
- `import_chat()`
- `import_channel_messages()`
- `validate_chat_migration()`

Responsibilities:

- Chat migration.
- Chat message migration.
- Channel message migration.
- Member handling.
- Attachment handling.
- Message validation.

---

# Common Infrastructure

Path:

`migration/common/`

Important modules:

### `checkpoint.py`

SQLite-based `StateStore`.

Used for:

- Resume.
- Deduplication.
- Completion state.
- Failed state.
- Per-item migration state.

### `graph.py`

Graph API client.

Handles:

- Authentication headers.
- Requests.
- Pagination.
- 401 refresh.
- 429 handling.
- 5xx retry behavior.
- Raw file transfers.

### `retry.py`

Shared retry wrapper.

### `concurrency.py`

Bounded workers and adaptive throttling.

### `reporting.py`

Shared reporting helpers.

---

# OneDrive Investigation Path

When investigating:

> User marked completed but OneDrive is incomplete.

Start here:

```text
migration/orchestration/batch.py
        ↓
OneDrive user processing
        ↓
copy_drive()
        ↓
onedrive checkpoint writes
        ↓
batch-onedrive checkpoint
        ↓
report
        ↓
validate_user_drive()
```
