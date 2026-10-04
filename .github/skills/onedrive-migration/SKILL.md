---
name: onedrive-migration
description: Debug and maintain OneDrive migration, especially completion, checkpoints, validation, retries, and user-level status.
---

# OneDrive Migration

## Priority

OneDrive is the current primary migration workload.

Known issue: users may be marked `completed` while their OneDrive is still incomplete.

## Token Efficiency

Use the minimum context required.

Before reading files:

1. Search for the relevant symbol/status.
2. Read its definition.
3. Read direct callers/callees only.
4. Inspect targeted tests.
5. Read docs only if needed.

Do not:

- Scan the whole repository.
- Read unrelated workloads.
- Read large logs unless required.
- Refactor unrelated code.
- Repeat information already established.

Stop investigating once the root cause is supported by evidence.

## Key Files

Start with only the files relevant to the task:

- `migration/onedrive/migration.py`
- `migration/onedrive/validation.py`
- `migration/orchestration/batch.py`
- `migration/orchestration/validate.py`
- `migration/common/checkpoint.py`

Search for the actual function/symbol before opening additional files.

## Completion Model

Important checkpoints:

- `onedrive` → per file/folder.
- `batch-onedrive` → per user.

Never assume:

```text
copy_drive() returned successfully
→ user completed
```
