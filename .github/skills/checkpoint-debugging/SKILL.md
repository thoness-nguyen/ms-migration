---
name: checkpoint-debugging
description: Diagnose and fix migration checkpoint, resume, retry, duplicate-work, and false-completion issues across OneDrive and Teams.
---

# Checkpoint Debugging

## Purpose

Use this skill whenever persisted migration state disagrees with execution logs, reports, or actual destination data.

A checkpoint records what the worker believes happened; it is not independent proof that destination data is correct.

## Investigation model

Trace:
1. What operation was attempted?
2. What did the destination API return?
3. Was the destination result confirmed?
4. What checkpoint transition was committed?
5. What did the parent/user/job status infer?
6. What did the report say?
7. What did post-migration validation find?

Inspect `migration/common/checkpoint.py` and its callers before proposing state changes.

## Required safety rules

- Do not clear/delete checkpoints or directly change live rows as a first-line fix.
- Do not manually update the state DB while a worker is using it unless a documented safe maintenance procedure is followed.
- Preserve idempotency, retry limits, and Graph throttling.
- Separate `attempted`, `copied/created`, `skipped`, `failed`, `retryable`, and `validated` outcomes.
- A parent cannot be complete if required child work is pending, failed, unknown, or unvalidated.
- A report-generation success is not migration success.

## Crash window and reconciliation

Test the ambiguous sequence where the destination write succeeds but the worker crashes before the checkpoint commit. On resume, reconcile the destination using stable identifiers and workload-specific evidence before retrying; do not blindly duplicate a create operation.

Also test a crash before the destination write, and a timeout where the destination may have accepted the request despite the client receiving no response.

## Concurrency and leases

For one worker on one host, inspect SQLite transactions and write access. Enforce a single-writer/single-active-job policy unless concurrency is explicitly supported.

Before multiple processes or hosts share jobs, require:
- transactional shared state;
- atomic work claiming;
- lease expiry and heartbeat;
- protection against stale workers updating reclaimed work;
- duplicate-job and overlapping-scope detection.

Do not use a SQLite file on SMB/NAS as distributed coordination.

## Recovery test matrix

- Kill worker before operation.
- Kill worker after destination success but before checkpoint commit.
- Restart process and host.
- Interrupt and restore network.
- Expire/reacquire credentials.
- Trigger 429 and transient 5xx responses.
- Re-run a completed job or submit the same job twice.
- Restore checkpoint backup.
- Simulate malformed/corrupt state in a test environment.
- Confirm terminal job status matches all child results.

## Reporting and evidence

For each defect, capture job ID, item identity, prior state, attempted operation, API outcome, retry history, new state, report result, and validation result. Redact tokens and sensitive content.

A final audit must distinguish:
- `passed`
- `passed_with_exceptions`
- `failed`
- `inconclusive`

Never label a run audited/passed solely because all checkpoints say `completed`.
