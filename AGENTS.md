# MS Migration Agent Instructions

## Purpose

This repository is an in-house Microsoft 365 migration platform using
Microsoft Graph APIs to migrate data between source and target tenants.

Supported workloads:

- OneDrive
- Teams
- SharePoint

## Current Engineering Focus

### 1. OneDrive — HIGH PRIORITY

OneDrive is currently the main area requiring investigation and maintenance.

Known issue:

> Some users are marked as completed even though their OneDrive content
> is not fully migrated when the migration report is cross-checked against
> the actual source and target data.

This is a correctness issue, not simply a reporting issue.

When working on OneDrive completion, checkpoint, reporting, or validation,
trace the complete flow:

    orchestration
        ↓
    OneDrive user processing
        ↓
    copy_drive()
        ↓
    per-item checkpoint
        ↓
    batch-onedrive user checkpoint
        ↓
    migration report
        ↓
    validation

Do not assume that one successful function return means the user's
OneDrive migration is complete.

### 2. Teams — MAINTENANCE

Teams is currently a maintenance workload.

Typical tasks:

- Bug fixes
- Checkpoint issues
- Retry issues
- Chat/message issues
- Channel/message issues
- Attachment issues
- Small performance improvements
- Regression fixes

Do not redesign the Teams architecture unless explicitly requested.

### 3. SharePoint — STABLE

SharePoint migration is currently considered stable.

Do not modify SharePoint migration logic for unrelated tasks.

Only touch SharePoint when:

- explicitly requested; or
- a shared infrastructure change directly requires it.

Avoid unnecessary regression risk in the stable SharePoint workload.

---

# Core Engineering Principles

## Correctness before speed

Never sacrifice migration correctness for throughput.

The system must remain:

- Resumable
- Idempotent
- Retry-safe
- Observable
- Deduplicated

## Completion must represent real work

Never mark a workload as `completed` simply because:

- a function returned without raising;
- a worker pool finished;
- a folder walk completed;
- a report was generated.

Completion must reflect the actual required migration result.

For per-user workloads such as OneDrive, verify the relationship between:

- per-item results;
- user-level checkpoint;
- migration statistics;
- report status;
- validation status.

## Preserve existing reliability behavior

When changing code:

- Preserve checkpoint semantics.
- Preserve retry behavior.
- Preserve Graph throttling behavior.
- Preserve idempotency.
- Preserve existing public interfaces unless change is required.
- Avoid unrelated refactoring.

---

# Token / Context Efficiency

Use the smallest amount of repository context necessary.

## Investigation order

1. Identify the exact workload.
2. Search for the relevant function, symbol, status, or checkpoint key.
3. Read the defining function.
4. Read its direct callers.
5. Read its direct dependencies.
6. Inspect related tests.
7. Inspect reporting/validation if relevant.
8. Read architecture documentation only when necessary.

## Do not

- Read the entire repository.
- Read every file in a workload.
- Inspect unrelated workloads.
- Read large historical logs unnecessarily.
- Repeatedly rediscover repository structure.
- Perform broad refactoring during a focused bug fix.
- Copy large amounts of source code into the response.

## Prefer

- Symbol search.
- Targeted file reads.
- Existing documentation.
- Existing abstractions.
- Targeted tests.
- Small root-cause fixes.

---

# OneDrive Debugging Rules

When investigating OneDrive completion problems, explicitly inspect:

### Per-item state

`onedrive`

Represents individual files/folders.

### Per-user state

`batch-onedrive`

Represents the user's overall migration status.

Do not assume:

    copy_drive() returned successfully
        =
    user migration completed

A successful `copy_drive()` call may still contain per-file failures.

Always investigate:

- `files_copied`
- failed items
- pending items
- skipped items
- retry results
- `onedrive` checkpoints
- `batch-onedrive` checkpoint
- generated migration report
- validation result

Find the earliest point where incomplete work becomes `completed`.

Do not solve a completion bug by simply:

- clearing checkpoints;
- deleting the database;
- rerunning the entire migration;
- changing the report to hide failures.

Fix the state transition or underlying logic.

---

# Teams Maintenance Rules

When working on Teams:

1. Identify the exact failing operation.
2. Trace the relevant call chain.
3. Inspect checkpoint behavior.
4. Inspect Graph API calls and retry behavior.
5. Inspect relevant tests.
6. Make the smallest safe change.
7. Run targeted tests.

Preserve:

- message ordering;
- message IDs;
- sender mapping;
- checkpoint semantics;
- idempotency;
- retry behavior.

---

# Shared Infrastructure

Important common modules:

- `migration/common/graph.py`
- `migration/common/auth.py`
- `migration/common/checkpoint.py`
- `migration/common/retry.py`
- `migration/common/concurrency.py`
- `migration/common/reporting.py`

Changes to these modules may affect multiple workloads.

Before modifying shared infrastructure:

1. Identify all callers.
2. Determine affected workloads.
3. Run broader tests.
4. Check SharePoint regression risk.

---

# Architecture References

Primary architecture:

`docs/ARCHITECTURE.md`

Performance/concurrency architecture:

`docs/ARCHITECHTURE_UPGRADE.md`

Project registry:

`.github/skills/project-registry/SKILL.md`

OneDrive skill:

`.github/skills/onedrive-migration/SKILL.md`

Teams skill:

`.github/skills/teams-migration/SKILL.md`

Checkpoint debugging:

`.github/skills/checkpoint-debugging/SKILL.md`

---

# Change Discipline

For focused tasks:

- Inspect first.
- Explain the root cause before making a large change.
- Prefer the smallest viable implementation.
- Reuse existing code.
- Avoid unrelated cleanup.
- Add regression tests for bugs.
- Run targeted tests first.
- Expand testing only when necessary.

Do not implement speculative improvements unless requested.

---

# Response Format

For investigation tasks, return:

1. Findings
2. Root cause
3. Relevant files
4. Recommended change
5. Tests required

For implementation tasks, return:

1. Summary
2. Changed files
3. Tests executed
4. Remaining risks / limitations

Do not include large code blocks unless explicitly requested.