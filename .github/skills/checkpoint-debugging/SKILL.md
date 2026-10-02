---
name: checkpoint-debugging
description: Investigate checkpoint, resume, retry, and duplicate migration issues.
---

# Checkpoint Debugging

## Scope

Investigate migration state and checkpoint behavior.

## Main Implementation

`migration/common/checkpoint.py`

Teams usage:

`migration/teams/services.py`

## Investigation Procedure

1. Identify the migration workload.
2. Identify the source record ID.
3. Find the checkpoint key.
4. Trace checkpoint reads.
5. Trace checkpoint writes.
6. Identify status transitions.
7. Check retry and resume behavior.
8. Inspect relevant tests.

## Important Statuses

Inspect the actual StateStore implementation before assuming
which statuses are supported.

Relevant Teams keys:

- `teams-channel`
- `teams-message`
- `teams-chat`
- `teams-chat-message`

## Debugging Rules

- Do not change checkpoint behavior without understanding the full flow.
- Do not mark work completed before successful processing.
- Preserve resumability.
- Prevent duplicate migration.
- Handle partial failures correctly.
- Do not reset existing migration state without explicit approval.

## Output

Return:

- Current checkpoint flow.
- Root cause.
- Affected files.
- Minimal fix.
- Tests required.
