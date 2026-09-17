---
name: teams-migration
description: Investigate and implement Teams migration changes with minimal repository context.
---

# Teams Migration Skill

## Scope

Teams migration only.

Focus:

- Channels
- Channel messages
- Chats
- Chat messages
- Chat members
- Teams files and attachments

Do not modify SharePoint or OneDrive unless explicitly requested.

## Entry Points

Start investigation from:

`migration/orchestration/cli.py`

Then trace into:

`migration/orchestration/batch.py`

Then the relevant Teams service.

## Main Implementation

`migration/teams/services.py`

Important functions:

- `import_channel_messages()`
- `extract_chat()`
- `import_chat()`

## Investigation Procedure

For a Teams migration task:

1. Identify the requested feature.
2. Find the relevant entry point.
3. Trace the call chain.
4. Inspect checkpoint behavior.
5. Inspect Graph API calls.
6. Inspect relevant tests.
7. Implement the smallest required change.

## Implementation Rules

- Preserve existing public interfaces.
- Preserve message ordering.
- Preserve checkpoint behavior.
- Preserve idempotency.
- Respect Graph API limits.
- Avoid unrelated refactoring.
- Reuse existing GraphClient and StateStore.
- Do not introduce a new migration framework without approval.

## Verification

Run targeted tests first.

For message migration, verify:

- Message ordering.
- Sender mapping.
- Checkpoint behavior.
- Retry behavior.
- Duplicate prevention.
- Migration completion.

## Response

Return:

- Changed files.
- Summary of changes.
- Tests executed.
- Remaining limitations.
