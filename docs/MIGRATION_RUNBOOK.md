# Migration runbook - Teams + SharePoint (2026 scope)

Operator steps for the current scope, in order. Every step is resumable:
re-running the same command against the same `state/` continues where it stopped.

**Never run two Teams batches at the same time** - they share
`state/teams-checkpoint.sqlite`. SharePoint (`state/sharepoint-checkpoint.sqlite`)
may run in parallel with Teams.

## 0. One-time prerequisites

1. Credentials in `.env` (`SOURCE_*`, `TARGET_*`; see `config/examples/config.example.env`).
   The CLI and panel read them from the **process environment**, so load them
   into the PowerShell session first:
   ```powershell
   Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
     $k, $v = $_ -split '=', 2; [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim().Trim('"'), 'Process') }
   $env:PYTHONIOENCODING = 'utf-8'
   ```
2. Target app permissions: `Teamwork.Migrate.All`, `Chat.ReadWrite.All`,
   `ChannelMessage.ReadWrite.All` (or equivalent), `Team.ReadBasic.All`,
   `Sites.ReadWrite.All`, `Files.ReadWrite.All`. For provisioning (step 5):
   `Team.Create`, `TeamMember.ReadWrite.All`.
3. `uv sync` and `uv run pytest` (all tests must pass).

## 1. Review the prepared scope (already generated)

| File | Content |
|---|---|
| `config/users.mapping.json` | 97 users. `kevinc` -> `kevinc@paizes.com`. Cecilia Lee has `"skip": true` (excluded). |
| `config/teams-pilot.yaml` | 1,591 chats (all chats of mapped users, meeting chats removed) + 2 channels active in 2026 |
| `config/sharepoint-pilot.yaml` | Harbour PD Work Flow, All Company, root Communication site |
| `config/discovery/channels.yaml` | All 16 channels active in 2026; 14 under `unresolved` (no target team yet) |
| `config/discovery/sites.yaml` | All 40 SharePoint sites active in 2026; 37 under `unresolved` (no target site yet) |

To refresh discovery later (read-only):
```powershell
uv run python -m migration.teams.discover_chats --existing config/teams-pilot.yaml --output config/discovery/chats.yaml
uv run python -m migration.teams.discover_channels --existing config/discovery/legacy-teams-channels.yaml --since 2026-01-01T00:00:00Z
uv run python -m migration.sharepoint.discover_sites --target-host paizesoffice.sharepoint.com --existing config/sharepoint-pilot.yaml
```

## 2. Optional: dry run (no target writes)

```powershell
uv run tenant-migrator --state-dir state --dry-run batch --config config/teams-pilot.yaml --area teams --teams-scope channels --report-dir reports/dry-run
```

## 2b. Streaming pilot (once, before the full SharePoint run)

Large files are now streamed in 10 MiB pieces (constant memory, resumable within
a file). Pilot it on one site whose files are almost all > 4 MB:
```powershell
uv run tenant-migrator --state-dir state batch --config config/sharepoint-streaming-pilot.yaml --area sharepoint --report-dir reports/pilot
```
Check `reports/pilot/sharepoint-migration-result.json`: status `completed`, `failed: 0`,
and `validation` counts matching; open 2-3 large files on the target. Same state DB as
the full run, so the full run skips everything already copied here.

Throughput settings live in the plans (`performance.concurrency`): SharePoint 8 files /
4 permission sets, Teams 6 chats or channels in parallel. Completed chats sync
incrementally (only messages changed since the last sync); set `full_sync: true` in
`config/teams-pilot.yaml` to re-read every chat in full (e.g. after adding users).

## 3. SharePoint (can run in parallel with step 4)

Panel: `uv run migration-panel` -> SharePoint -> **1. Start migration**. Equivalent:
```powershell
uv run tenant-migrator --state-dir state batch --config config/sharepoint-pilot.yaml --area sharepoint --report-dir reports
```
Result: `reports/sharepoint-migration-result.json`. A library with failed items stays
`pending` and is retried by simply re-running.

## 4. Teams - channels first (small; backfills replies), then chats

Panel: `uv run migration-panel` -> Teams -> 1. Start migration -> scope **[2] Channels only**,
then again with scope **[1] Chats only**. Equivalent:
```powershell
uv run tenant-migrator --state-dir state batch --config config/teams-pilot.yaml --area teams --teams-scope channels --report-dir reports
uv run tenant-migrator --state-dir state batch --config config/teams-pilot.yaml --area teams --teams-scope chats --report-dir reports
```
The chat run is long (1,591 chats). Closing the terminal stops it; re-run the same
command to resume.

Reading results (`reports/teams-migration-result.json`, panel option 4):
- `completed` - migrated; `skipped` - already done.
- `excluded` + `reason` - can never migrate with the current mapping
  (`unmapped-participant`, `excluded-user`, `source-chat-not-found`,
  `source-chat-external-tenant`, `insufficient-mapped-members`, `unsupported-chat-type`).
  These are re-checked every run - add the user to the mapping and re-run to migrate them.
- `failed` - read `error`; re-run to retry. A "user mapping changed after this chat
  was created" error means the existing target chat was created with a different
  identity (known: 6 kevinc chats created with his former guest account).

## 5. Target teams/sites - DONE 2026-11-10, then finalize

Already done by script: 21 target teams created in migration mode, backdated to the
source creation time (verified), 13 channels created/mapped, 21 team sites mapped.
Results in `config/discovery/provisioned-teams.yaml`; channels added to `config/teams-pilot.yaml`,
sites added to `config/sharepoint-pilot.yaml` (Harbour PD Work Flow excluded).

The new teams are INVISIBLE to users until finalized. After step 4 (channels) has
completed their channels:
```powershell
uv run python -m migration.teams.provision_targets --finalize            # dry run - shows which teams are ready
uv run python -m migration.teams.provision_targets --finalize --apply    # completeMigration + add members
```

Manual items (the migration app cannot create these):
- CMS site: create document libraries `Contracts` and `Templates` before the SharePoint run.
- HARBOUR VN/RH - Merchandising: create libraries `HB - RH Claim tracker` and
  `HB Price list - master file`. This target site has no M365 group, so set its
  owners/members manually (Settings > Site permissions).
- The 7 group sites without a team were created manually on 2026-11-10 and are in the plan.
- HO.HK private channels "Asia Design Discussion Group" / "Contracts to Tricor": skipped (decision 2026-11-10).

## 5b. Owners and members (same as source)

Needs `GroupMember.ReadWrite.All` (Application) with admin consent on the TARGET app.
```powershell
uv run python -m migration.sharepoint.sync_members                         # dry run
uv run python -m migration.sharepoint.sync_members --apply                 # add source owners/members
uv run python -m migration.sharepoint.sync_members --apply --remove-extra-owners   # also drop placeholder owners
```
Teams created by script get their members in `provision_targets --finalize --apply`.

## 5c. Harbour PD Work Flow - incremental

It is in `config/sharepoint-pilot.yaml` with `modified_since: "2026-09-09T16:00:00Z"`
(10 Sep 2026 00:00 UTC+8): only files created/modified since then, plus items that
failed in the earlier full copy, are copied; newer versions overwrite the target copy.
Not propagated: deletions and pure moves/renames after the cutoff.

Re-running provisioning is safe (existing teams/channels are reused):
`uv run python -m migration.teams.provision_targets [--apply]`.

## 5d. OneDrive - re-copy changes since 2026-10-20

Every user entry in `config/onedrive.mapping.pilot.yaml` has `modified_since: "2026-10-20"`
(a top-level `modified_since:` would apply to all users; an entry's own value wins;
a user without one gets a full copy). For those users, only files created or modified on the source since that date (plus files that
failed in the earlier full copy) are copied again, overwriting the target copy.
`travis` has `skip: true` (handled separately); users with `skip: true` in
`users.mapping.json` (e.g. cecilia) are skipped too. Skips are reported, never
checkpointed - remove the flag to include the user again.

```powershell
uv run tenant-migrator batch --config config/onedrive-pilot.yaml --area onedrive --dry-run   # scan only: files / GB per user
uv run tenant-migrator batch --config config/onedrive-pilot.yaml --area onedrive             # copy (or panel -> OneDrive -> Start)
```
How it runs: one delta scan per drive (4 users at a time), then all users' changed
files go through one pool of 8 workers (`performance.concurrency.onedrive`), largest
first; files > 4 MB are streamed in 10 MB pieces. Resume = rerun; a user is
`completed` only when every changed file succeeded (`batch-onedrive-incremental`
row), otherwise it stays pending. Files inside OneNote notebooks are listed as
`unresolved` in the report and keep the user pending. Not propagated: deletions
and pure moves/renames after the cutoff. Remove a user's `modified_since` for a full copy of that user.

## 6. Validate

```powershell
uv run tenant-migrator validate --config config/teams-pilot.yaml --area teams
```
Checkpoint `completed` is not proof the target is correct - spot-check a few chats
and channels (including replies) in the target Teams client.
