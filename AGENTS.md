# MS Migration Agent Instructions

## Purpose and operating model

This repository is an in-house Microsoft 365 migration engine for OneDrive, Teams, and SharePoint using Microsoft Graph.

**Architectural direction:** evolve from a laptop-bound terminal workflow to a server-hosted, resumable migration service. Migration execution must not depend on an operator keeping a laptop or browser open.

Preferred progression:
1. Prove a single persistent worker on approved, cost-effective Azure compute.
2. Harden durable checkpoints, recovery, observability, configuration, and audit.
3. Add an authenticated web/API control plane and durable queue only when justified.

Do not assume hosting, pricing, service limits, or company Azure access. Verify them before selecting a service. A server-hosted job continues only while its compute resource remains running and healthy.

## Workload priorities

### OneDrive — HIGH PRIORITY
Known correctness concern: a user may be marked completed while some source content is not fully present in the target.

Trace the full flow:
`orchestration -> user processing -> copy_drive() -> per-item checkpoint -> batch-onedrive checkpoint -> report -> validation`.

A successful function return or user-level checkpoint is not sufficient proof of completion.

### Teams — MAINTENANCE
Preserve message ordering, sender mapping, member handling, attachments, retries, checkpoint semantics, and idempotency. Avoid broad redesign unless requested.

### SharePoint — STABLE
Avoid unrelated changes. Modify only when requested or when a shared infrastructure change requires it; include regression tests.

## Non-negotiable engineering principles

- Correctness before throughput.
- Resumable, idempotent, retry-safe, observable execution.
- Completion means required work has succeeded and configured validation has passed.
- Separate execution status from validation/audit status.
- Never hide failures by changing reports, clearing checkpoints, or marking unresolved work complete.
- Preserve Graph throttling behavior and honor `Retry-After`.
- Avoid unrelated refactors and speculative implementation.

## Cloud execution and architecture rules

- Treat the CLI/`migration-panel` as an operator interface, not as the long-term owner of a multi-day process.
- The worker must execute on persistent server-side compute. Closing the browser or shutting down the operator laptop must not stop it.
- Start with one worker on one approved host if sufficient; do not introduce Kubernetes, microservices, or distributed coordination without a demonstrated need.
- Evaluate Azure VM and supported container hosting against current price, runtime limits, persistent storage, restart behavior, network access, and company policy.
- Do not assume short-lived/serverless jobs can run for days without limits.
- Keep the Synology NAS as optional file/archive storage only unless IT explicitly approves and validates another role. Do not use SQLite over SMB as a distributed coordination database.
- Keep workload/domain logic separate from hosting and job orchestration so hosting can change without rewriting migration behavior.
- Before multiple workers share state, design atomic job claiming, leases/heartbeats, conflict prevention, and a transactional shared state store.

## Checkpoint and recovery rules

- Inspect `migration/common/checkpoint.py` before changing state behavior.
- Document the meaning of each state and its legal transitions.
- Record job ID, item identity, attempt count, timestamps, error category, and relevant source/target references.
- Update successful checkpoints only after the destination operation is confirmed.
- Reconcile the ambiguous case where a destination write succeeds but the process crashes before checkpoint commit.
- Ensure parent completion is blocked by required pending, failed, unknown, or unvalidated children.
- Never manually update/delete live checkpoint rows while a worker is active unless a documented maintenance procedure proves it safe.
- Test recovery after process termination, host restart, network loss, authentication renewal, and Graph throttling.

## Configuration and security

- Separate code, non-secret deployment settings, per-job manifest, and credentials.
- Validate source/target tenant IDs, workload scope, mappings, permissions, concurrency, storage, and output paths before starting.
- Store secrets in an approved server-side secret store such as Azure Key Vault where available; never commit tokens, certificates/private keys, client secrets, production mappings, or `.env` files.
- Prefer managed identity for Azure resource access where compatible. Do not assume it replaces the app registrations/credentials required for Microsoft Graph tenant-to-tenant access.
- Apply least privilege, restricted admin access, secret rotation, and redaction of tokens/sensitive data from logs.
- Log who starts, retries, cancels, or approves jobs.

## Post-migration audit

Every run must produce a durable audit manifest and exception report. Report migrated, failed, retry-exhausted, skipped, excluded, unsupported, and unverified items separately.

Validation must compare expected source inventory with target results using workload-appropriate identifiers and checks. Checkpoint `completed` is not proof that target data is correct. A final status must distinguish passed, passed-with-approved-exceptions, failed, and inconclusive.

## Investigation efficiency

Use the smallest context that can establish the root cause:
1. Identify workload and observed symptom.
2. Search for relevant symbol/state/checkpoint key.
3. Read definition, direct callers, dependencies, and targeted tests.
4. Inspect report and validation path when completion is involved.
5. Read architecture documentation when hosting, state, or cross-workload behavior is involved.

Do not scan the whole repository, inspect unrelated workloads, or read large logs unnecessarily.

## Shared infrastructure

- `migration/common/graph.py`
- `migration/common/auth.py`
- `migration/common/checkpoint.py`
- `migration/common/retry.py`
- `migration/common/concurrency.py`
- `migration/common/reporting.py`

Before changing shared code, identify all callers, affected workloads, and regression tests.

## Required implementation workflow

1. Inspect current code and document assumptions.
2. Explain root cause or architecture decision before a large change.
3. Propose the smallest safe change and rollout/rollback approach.
4. Add targeted regression/recovery tests.
5. Run targeted tests first, then broaden as risk requires.
6. Report changed files, tests run, untested scenarios, and remaining risks.

## Response format

For investigations: findings, root cause, relevant files, recommended change, tests.
For implementation: summary, changed files, tests executed, remaining risks/limitations.
Do not claim a deployment or repository change succeeded unless verified.
