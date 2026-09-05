# Tenant Migrator

This repository is a resumable starter for a Microsoft 365 tenant-to-tenant migration. It automates the two Graph-based paths from `free-tenant-migration-guide.md`:

- Teams channel history import from an extracted JSON array, preserving mapped sender data and historical timestamps.
- Teams 1:1 and group chat extraction/import, with an explicit source-to-target user map.
- OneDrive file and folder copy using source downloads and target uploads, including resumable upload sessions for files over 4 MiB.

It does not claim to migrate everything. Exchange mailbox history remains a Purview PST export/import operation, and SharePoint site structure, permissions, links, versions, meetings, reactions, polls, apps, and external-participant content require separate handling.

## Quick start

1. Install Python 3.11+ and dependencies: `py -m pip install -e .`. The CLI automatically loads a local `.env` file from the current directory.
2. Create two Entra app registrations, one in each tenant. Grant only the permissions needed for the workload and obtain admin consent. The source app needs read permissions such as `Files.Read.All`; the target app needs write/import permissions such as `Files.ReadWrite.All` and `Teamwork.Migrate.All`. The CLI supports separate credentials through `SOURCE_GRAPH_CLIENT_*` and `TARGET_GRAPH_CLIENT_*`.
3. Copy `config.example.env` to `.env`, replace the placeholders with rotated secrets, and never commit a client secret. Alternatively, set the variables in your shell or secret manager.
4. Run a dry run first:

   `tenant-migrator --dry-run teams-channel --team-id TARGET_TEAM --channel-id TARGET_CHANNEL --messages messages.json`

5. Run the real import with a durable state directory (one checkpoint database per area is created automatically under `state/`):

   `tenant-migrator --state-dir state teams-channel --team-id TARGET_TEAM --channel-id TARGET_CHANNEL --messages messages.json`

   `tenant-migrator --state-dir state onedrive --source-user SOURCE_USER_ID --target-user TARGET_USER_ID`

For a Teams 1:1 or group chat, extract from the source tenant first. This command only needs the source tenant variables:

   `tenant-migrator extract-chat --chat-id SOURCE_CHAT_ID --output chat-bundle.json`

Create `user-map.json` as a JSON object such as `{"SOURCE_USER_ID": "TARGET_USER_ID"}`, then dry-run and import in the target tenant:

   `tenant-migrator --dry-run teams-chat --bundle chat-bundle.json --user-map user-map.json`

   `tenant-migrator --state-dir state teams-chat --bundle chat-bundle.json --user-map user-map.json`

The target chat import requires `Teamwork.Migrate.All`, `Chat.Create`, and `User.Read.All` as application permissions with admin consent. The source extraction requires `Chat.Read.All` and `User.Read.All`. OneDrive batch entries use the master user's `source_id` and `target_id`, and require `Files.Read.All` in the source tenant plus `Files.ReadWrite.All` in the target tenant. System/event messages without a user sender are skipped because they cannot be imported as authored messages. Normal HTML/text and inline hosted images are preserved. Forwarded-message references are converted to their original visible HTML content. Other file attachments, polls, reactions, mentions, and app messages cannot be represented faithfully by the Teams import API; the report counts unsupported features and preserves a link when an attachment exposes `contentUrl`. Meeting chats are not supported by the Graph migration API.

If a failed run leaves a target chat in migration mode, complete that session before retrying: `tenant-migrator complete-chat --chat-id TARGET_CHAT_ID`. Then use a fresh state directory for the retry.

## Batch migration

Use [migration.example.yaml](config/migration.example.yaml) as a starting point. The master [mapping.example.json](config/mapping.example.json) defines the users in scope and their source/target IDs. Workload mapping files then reference those stable user keys. The batch command processes Teams chats sequentially, writes bundles for audit/retry, validates all mappings before import, continues after an individual failure, and writes one `<area>-migration-result.json` report per area (SharePoint/OneDrive/Teams) into `--report-dir` (default `reports/`). Exchange, OneDrive, and SharePoint mappings are also accepted for planning and validation; only Teams chat execution is currently wired into this batch command.

Dry-run all configured chats:

   `tenant-migrator --dry-run --state-dir state batch --config config/migration.example.yaml --report-dir reports`

Run the batch:

   `tenant-migrator --state-dir state batch --config config/migration.example.yaml --report-dir reports`

Rerunning the same command skips chats recorded as completed. Failed chats can be corrected in the mapping/configuration and rerun; preserve the state directory, bundles, and reports together.

The master mapping controls scope: if it contains 50 users, those are the users intended for the migration. A workload file can include a subset of those keys. Teams and OneDrive entries are executable; Exchange and SharePoint entries are validated and included in the report for their separate operational workflows. Never infer identity from display names; review source and target object IDs or UPNs before execution.

The state directory (one `<area>-checkpoint.sqlite` per workload) is the resume ledger. Keep it backed up with the extracted payloads, and do not reuse it for a different migration.

## Message input

`messages.json` is an array of Graph `chatMessage` objects containing at least `id`, `createdDateTime`, `from`, and `body`. Chat bundles contain `chat`, `members`, and `messages`. Obtain them with the source-tenant extraction command, then review the user map before importing. Only inline hosted images are supported by the Teams import schema.

## Important operational limits

Pilot with two or three users first. Fresh target channels must be placed in migration mode; the importer completes that mode after the batch. A failed import may require deleting and recreating the target channel. Confirm current Microsoft licensing, throttling, API behavior, retention, and legal requirements with your administrators before production use.

See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for the end-to-end runbook and scope matrix.
