# Teams Migration

## Scope

This document describes the current Teams migration implementation.

Supported workloads:
- Teams
- Channels
- Channel messages
- Chats
- Chat messages
- Chat members

## Source Code

Main implementation:

- `migration/teams/services.py`
- `migration/orchestration/cli.py`
- `migration/orchestration/batch.py`
- `migration/common/graph.py`
- `migration/common/checkpoint.py`

## Execution Flow

### Channel Migration

Source channel
    ↓
Create target channel
    ↓
Start migration mode
    ↓
Import channel messages
    ↓
Complete migration
    ↓
Save checkpoint

### Chat Migration

Extract source chat
    ↓
Resolve target users
    ↓
Create target chat
    ↓
Start migration
    ↓
Import messages
    ↓
Complete migration
    ↓
Restore group members
    ↓
Save checkpoint

## Important Functions

### Channel Messages

`migration/teams/services.py`

`import_channel_messages()`

Responsibilities:
- Sort messages chronologically.
- Import channel messages.
- Preserve message timestamps.
- Resolve attachment references.
- Save message checkpoints.
- Complete channel migration.

### Chat Messages

`import_chat()`

Responsibilities:
- Create target chat.
- Import messages.
- Resolve sender identities.
- Handle deleted/system messages.
- Track unsupported features.
- Restore group-chat members.

## Checkpointing

Channel messages use:

`teams-message`

Chats use:

`teams-chat`

Chat messages use:

`teams-chat-message`

Channel migration uses:

`teams-channel`

Before changing checkpoint behavior, inspect:

`migration/common/checkpoint.py`

## Current Limitations

- Channel messages are processed sequentially.
- Chat messages are processed sequentially.
- Message-level concurrency is not yet implemented.
- Channel and chat migrations have different checkpoint models.
- Some Teams message features may be unsupported.
- Graph throttling must be respected.

## Change Rules

- Preserve chronological message ordering.
- Preserve checkpoint semantics.
- Do not duplicate messages.
- Do not mark incomplete work as completed.
- Do not change SharePoint or OneDrive code unless explicitly requested.
- Prefer small changes to existing functions.