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

## Message bundle snapshots

Each batch run extracts the configured source chats from Microsoft Graph and
overwrites their snapshots under `data/team-chats/` as
`<name>.chat-bundle.json`. Channel entries likewise fetch the source channel
and its messages from Graph on each run, then overwrite
`data/teams/<name>.channel-bundle.json` before importing the messages.
Channel plans therefore need source and target team/channel IDs, not a
manually maintained messages JSON file. With `sync_new_messages: true`, the
fresh snapshot is checked against the existing message checkpoints so only
uncheckpointed messages are imported.

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