---
name: teams-migration
description: Maintain Teams migration behavior, message correctness, resumability, and audit while supporting server-hosted execution.
---

# Teams Migration Skill

## Scope

Teams only: teams, channels, chats, messages, members, files, and attachments. Do not modify OneDrive or SharePoint unless requested or a shared infrastructure change requires it.

## Entry points

Start at `migration/orchestration/cli.py`, trace to `migration/orchestration/batch.py`, then into the relevant service and validation code.

Main implementation: `migration/teams/services.py`.
Relevant functions include `import_channel_messages()`, `extract_chat()`, and `import_chat()`.

## Investigation procedure

1. Identify the exact workload and failure.
2. Trace the orchestration and service call chain.
3. Inspect Graph API response/retry behavior.
4. Inspect checkpoint transitions and parent completion.
5. Inspect the relevant report and validation.
6. Read targeted tests and add regression coverage.
7. Make the smallest safe change.

## Correctness requirements

Preserve:
- message ordering;
- sender/identity mapping;
- chat/channel membership semantics;
- attachment handling;
- idempotency and duplicate prevention;
- retry and checkpoint behavior;
- Graph throttling limits.

Do not claim parity for features that the source/target APIs or current implementation cannot migrate. Document unsupported or unverified features in the audit.

## Server-hosted execution

Teams jobs must run in a server-side worker, not depend on the operator's terminal session. Keep workload logic independent of UI and hosting. Job configuration, checkpoint location, logs, and reports must be explicit and durable.

Do not introduce multiple workers sharing state until atomic job claiming, leases, duplicate-job protection, and the shared state store are designed and tested.

## Verification and audit

Validate the selected teams/channels/chats/messages and supported members/attachments against the expected source scope. Record missing, mismatched, failed, skipped, excluded, unsupported, and unverified outcomes separately.

A job is not audit-passed merely because the worker exited or checkpoints are completed.

## Response

Return changed files, summary, tests executed, and remaining risks/limitations.
