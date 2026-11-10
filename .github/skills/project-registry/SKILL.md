---
name: project-registry
description: Locate the smallest relevant code and documentation for migration correctness, cloud hosting, checkpoints, configuration, and audit.
---

# Project Registry

Use this skill to locate the relevant implementation and documentation without scanning the entire repository.

## Current priorities

1. **OneDrive:** completion correctness, per-item/user checkpoint consistency, reconciliation.
2. **Cloud execution:** move long-running execution off the operator laptop while keeping the initial deployment simple.
3. **Checkpoint/recovery and audit:** durable state, safe resume, source/target validation.
4. **Teams:** maintenance and regression fixes.
5. **SharePoint:** stable; inspect only when requested or a shared change affects it.

## Architecture and deployment documents

- `docs/ARCHITECTURE.md` — current application architecture; verify its contents before relying on it.
- `docs/ARCHITECTURE_UPGRADE.md` — existing performance/concurrency architecture; note the filename spelling.
- `docs/CLOUD_HOSTED_MIGRATION_ROADMAP.md` — proposed hosting, checkpoint, configuration, recovery, and audit roadmap.
- `AGENTS.md` — repository-wide engineering and safety rules.

## Repository map

### Orchestration
`migration/orchestration/`
- `cli.py`
- `batch.py`
- `validate.py`

Responsibilities: load migration plans, create Graph clients, open checkpoint stores, coordinate workloads, execute jobs, generate reports, and run validation.

### OneDrive
`migration/onedrive/`
- `migration.py`
- `validation.py`

Important functions: `copy_drive()`, `retry_failed_onedrive_files()`, `validate_user_drive()`.

State:
- `onedrive`: per-file/folder state; key format documented in current code/config.
- `batch-onedrive`: per-user summary state.

Do not assume a user-level checkpoint is authoritative without examining child item states and validation.

### Teams
`migration/teams/`
- `services.py`
- `validation.py`

Important functions include `extract_chat()`, `import_chat()`, `import_channel_messages()`, and `validate_chat_migration()`.

Preserve workload-specific API limitations, ordering, identity mapping, and validation semantics.

### Shared infrastructure
`migration/common/`
- `checkpoint.py`: SQLite `StateStore`, resume and deduplication.
- `graph.py`: authentication headers, requests, pagination, token refresh, throttling/retries, transfers.
- `retry.py`: shared retry wrapper.
- `concurrency.py`: bounded workers/adaptive throttling.
- `reporting.py`: shared reporting helpers.
- `auth.py`: authentication.

Before modifying a shared module, identify callers and test affected workloads.

## Cloud execution boundary

The target direction separates the operator interface from the long-running worker:
- Operator interface submits/inspects a job.
- Server-side worker owns execution.
- Durable state and reports survive laptop/browser shutdown.
- Hosting, credentials, and persistent storage are configured per environment.

Do not introduce a web UI, queue, or multi-worker design before the single-worker deployment and recovery model is proven. SQLite is currently a single-host assumption until code inspection and tests prove otherwise.

## Investigation path for false OneDrive completion

`migration/orchestration/batch.py`
→ OneDrive user processing
→ `copy_drive()`
→ per-item checkpoint writes
→ `batch-onedrive` checkpoint
→ report
→ `validate_user_drive()`

Find the earliest transition where incomplete work becomes completed. Do not fix by clearing state or hiding failures.
