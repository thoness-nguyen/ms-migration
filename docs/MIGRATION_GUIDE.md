# Migration guide

## What was built

The CLI uses a small Graph HTTP client with retry handling for throttling and transient server errors. `StateStore` records source IDs and target IDs in SQLite, so reruns skip completed messages and files. The Teams importer starts a fresh channel or target chat's migration mode, orders messages, makes timestamps unique to the millisecond, imports them, and completes migration. The OneDrive copier recursively recreates folders, downloads source bytes, uploads small files directly, and uploads larger files in sequential 10 MiB fragments.

Source and target tenants are deliberately represented by different Graph clients. A single app-only token is not a cross-tenant read/write credential.

## Recommended process

1. Inventory users, Teams, channels, chats, OneDrive sizes, mailbox sizes, shared mailboxes, delegates, retention policies, and external participants.
2. Create a source identity map and target identity map. Do not guess object IDs from UPNs; resolve and review them before importing messages.
3. Register and consent apps separately in both tenants. Store secrets outside the repository. Test Graph access with one source user and one target user.
4. Prepare target users, Teams, channels, owners, members, and OneDrive drives before moving content. The CLI intentionally does not silently create this security-sensitive structure.
5. Extract Teams channel or chat messages to durable JSON and validate that every sender and chat member maps to a target user. Preserve the extract as a backup and review for unsupported attachments.
6. Run the Teams command with `--dry-run`, then pilot one channel and one 1:1/group chat. Verify sender attribution, membership, order, timestamps, and inline images in Teams before expanding the pilot.
7. Run OneDrive for the pilot users. Compare file counts, paths, sizes, and timestamps. Sharing links and version history are not copied by this starter.
8. Complete Exchange using Purview eDiscovery export to PST in the source tenant and Purview network upload/import in the target tenant. Recreate delegates, rules, holds, permissions, and mail flow separately.
9. Recreate or document excluded workloads: SharePoint sites/pages/workflows, meetings and join URLs, Teams apps/tabs, polls, reactions, external content, and permissions.
10. Cut over DNS/mail flow and user sign-in only after reconciliation, user communication, and a rollback window are agreed. Keep the source tenant read-only for the agreed retention period.

## Automated chat batches

Use `migration.example.yaml` as the batch plan. A plan contains one `chats` entry per source 1:1 or group chat. Each entry has a source chat ID and either an inline source-to-target user map or a relative JSON mapping-file path.

Run the process in two passes:

1. Set the source and target tenant variables in the current PowerShell session.
2. Run the complete plan in dry-run mode:

	`tenant-migrator --dry-run --state pilot.sqlite batch --config migration.yaml --report pilot-report.json`

3. Review the report for missing mappings, unsupported chat types, and expected message counts.
4. Review the generated `*.chat-bundle.json` files. They contain sensitive message data and should be protected.
5. Run the same plan without `--dry-run`:

	`tenant-migrator --state pilot.sqlite batch --config migration.yaml --report migration-report.json`

The recommended structure is a master user mapping plus one mapping file per workload. The master file is the scope list and contains a stable key, source ID/UPN, and target ID/UPN for each user. Teams mapping files reference those keys. OneDrive can use the same user keys because it is user-owned. Exchange mailbox mappings also reference users but execute through Purview PST export/import. SharePoint must be mapped by site, library, drive, permissions, and owners because it is not owned by one user.

The runner processes one Teams chat or OneDrive user at a time. For each Teams chat it extracts the source data, validates every member mapping, creates the target chat, starts migration mode, imports messages, completes migration, and records checkpoints. For each OneDrive user it recursively copies that user's drive. A failed item is recorded in the report and does not stop later entries. Re-running with the same state database skips completed items and retries failed ones. Exchange and SharePoint workload mappings are included in the report as planned work and are not silently executed.

Do not change a chat's mapping after it has partially migrated without reviewing the state database. If a target chat import must be abandoned, record the target chat ID and follow Microsoft's cleanup/retry procedure before starting a replacement.

If Graph reports `already in migration mode`, the prior session is still open. Complete it with `tenant-migrator complete-chat --chat-id TARGET_CHAT_ID`, confirm the command succeeds, and retry using a fresh state database. The next attempt will start migration with the source chat's original `createdDateTime`.

## Scope and fidelity

| Workload | This repository | Expected result |
| --- | --- | --- |
| Teams channel messages | Yes | Historical body, sender mapping, and timestamp; inline images only |
| OneDrive current files/folders | Yes | Content and folder tree; source download/target upload |
| Exchange mail/calendar/contacts | No | Purview PST operator workflow |
| SharePoint site structure | No | Separate assessment or paid migration path |
| Teams 1:1/group chats | Yes | Bundle extraction plus mapped target chat creation/import; meeting chats are excluded |
| Permissions, links, versions, meetings, apps | No | Recreate or document manually |

## Safety checklist

- Use a test tenant or pilot users first.
- Keep secrets out of `.env` files committed to Git.
- Treat extracted messages and PST files as sensitive personal data.
- Capture counts and exceptions after each workload.
- Do not delete source data until business owners approve reconciliation.

## Microsoft references

- [Import messages into Teams](https://learn.microsoft.com/en-us/graph/teams-import-messages)
- [Send/import channel messages](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0)
- [Create OneDrive upload sessions](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0)
- [Microsoft 365 cross-tenant migration overview](https://learn.microsoft.com/en-us/microsoft-365/enterprise/cross-tenant-migration)
