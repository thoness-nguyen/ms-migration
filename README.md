# MS Migration

In-house Microsoft 365 tenant-to-tenant migration engine for **Teams** (chats,
channels, replies), **SharePoint** sites and **OneDrive**, built on Microsoft Graph.
Every run is checkpointed and resumable: re-run the same command and it continues
where it stopped.

- **Run a migration:** [docs/MIGRATION_RUNBOOK.md](docs/MIGRATION_RUNBOOK.md)
- **Engineering rules** (correctness, checkpoints, security): [AGENTS.md](AGENTS.md)
- **Commands and architecture:** [CLAUDE.md](CLAUDE.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Quick start

```powershell
uv sync                 # install
uv run pytest           # all tests must pass
uv run migration-panel  # interactive operator panel (start / status / retry per area)
```

Credentials are read from the process environment (`SOURCE_*` / `TARGET_*`, see
[config/examples/config.example.env](config/examples/config.example.env)); load `.env`
into the shell first as shown in the runbook.

## Repository layout

```
migration/
  orchestration/   cli.py (tenant-migrator), panel.py (migration-panel), batch.py (run_batch), validate.py
  teams/           services.py (chat/channel/reply import), discover_chats.py, discover_channels.py,
                   provision_targets.py (create target teams/channels in migration mode), validation.py
  sharepoint/      migration.py (library copy, streamed large files), incremental.py (changes since a date),
                   discover_sites.py, sync_members.py (owners/members), services.py, validation.py
  onedrive/        migration.py, validation.py, compare_report.py, *_users_usage.py (reports)
  accounts/        password_reset.py (target account password reset tool)
  common/          graph.py, auth.py, checkpoint.py (StateStore), concurrency.py, retry.py, env.py, reporting.py
config/
  teams-pilot.yaml, sharepoint-pilot.yaml, onedrive-pilot.yaml   live migration plans (panel defaults)
  sharepoint-streaming-pilot.yaml                                 one-site pilot plan
  users.mapping.json                                              source -> target user mapping (real data)
  onedrive.mapping.pilot.yaml                                     OneDrive user pairs
  discovery/      generated scope: chats, channels, sites, provisioned teams, legacy channel mapping
  examples/       templates with fake data (start here for a new plan)
docs/             runbook, architecture, cloud-hosting roadmap, Teams notes
tests/            test_<area>_*.py per workload; test_checkpoint/test_concurrency/test_plan_loading for shared code
data/ reports/ logs/ state/   real migration output and LIVE checkpoints - do not edit or delete
```

Plans must stay directly in `config/`: their mapping paths are relative to the plan,
and Teams bundles are written to `<plan folder>/../data/`.
