---
name: onedrive-migration
description: Maintain OneDrive migration correctness, per-item and per-user checkpoint consistency, safe recovery, and source-to-target validation.
---

# OneDrive Migration

## Priority

OneDrive is the primary current workload. Known concern: users may be marked `completed` even when content is missing or incomplete.

## Efficient investigation

Start with only the relevant files:
- `migration/onedrive/migration.py`
- `migration/onedrive/validation.py`
- `migration/orchestration/batch.py`
- `migration/orchestration/validate.py`
- `migration/common/checkpoint.py`

Search for the symbol first, then read its definition, direct callers, dependencies, and targeted tests. Do not scan unrelated workloads.

## Completion model

Important state scopes:
- `onedrive`: per-file/folder work.
- `batch-onedrive`: per-user summary.

Never assume `copy_drive()` returning successfully means the user is complete. Inspect copied counts, failed/pending/skipped items, retries, child checkpoints, parent checkpoint, report counters, and validation output.

Completion must be derived from required child results and configured validation. Preserve a clear distinction between execution-complete and audit-passed.

## Resume and idempotency

- Checkpoint only after destination success is confirmed.
- Handle timeouts and crashes where the destination may have accepted a write but checkpoint commit did not occur.
- Before retrying ambiguous creates, reconcile target state using stable identifiers and path/metadata evidence.
- Preserve bounded concurrency, retry behavior, and Graph `Retry-After`.
- Do not delete/reset state to mask false completion.
- Avoid manual checkpoint DB writes during an active migration.

## Cloud-worker compatibility

Migration logic should not depend on an interactive terminal, current working directory, laptop-local files, or browser lifetime. Inputs and outputs should be explicit:
- versioned job manifest and mappings;
- configured persistent state location;
- configured log/report output;
- server-side secret references;
- stable job ID and code/config version.

Do not embed deployment credentials or production mappings in source control. Do not make cloud-specific changes to OneDrive domain logic unless needed; prefer a hosting/orchestration adapter.

## Validation and audit

For each user, reconcile source inventory against target outcomes using the checks supported by the implementation, such as item identity, hierarchy/path, file/folder counts, sizes, required metadata, and permissions where in scope.

Report separately:
- migrated and validated;
- failed or retry-exhausted;
- skipped/excluded;
- missing or mismatched;
- unsupported;
- unverified.

A user-level checkpoint cannot override failed child items or failed validation. Keep discrepancy evidence and do not silently convert exceptions to success.

## Tests

Add regression coverage for:
- partial per-file failure with user-level completion blocked;
- crash/restart and resume;
- destination write succeeded but checkpoint commit failed;
- duplicate retry;
- report/checkpoint/validation disagreement;
- Graph throttling and retry exhaustion.
