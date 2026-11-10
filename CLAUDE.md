# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

AGENTS.md (imported above) is the source of truth for engineering rules, workload priorities, checkpoint/recovery rules, security, and audit requirements. This file adds commands, architecture, and the current gap toward server-hosted execution.

## Commands

Python >=3.11, dependencies pinned in `uv.lock`, package installed editable (console scripts below).

```powershell
uv sync                                              # create .venv and install
uv run pytest                                        # full suite (testpaths = tests/)
uv run pytest tests/test_onedrive_completion.py      # one file
uv run pytest tests/test_checkpoint.py -k <name>     # one test by name

# Migration engine (console script = migration.orchestration.cli:main)
uv run tenant-migrator batch --config config/onedrive-pilot.yaml --area onedrive [--dry-run] [--retry-failed-only]
uv run tenant-migrator validate --config config/onedrive-pilot.yaml --area onedrive [--hash]
uv run tenant-migrator --state-dir state batch ...   # --state-dir / --dry-run are global flags, before the subcommand
uv run migration-panel                               # interactive terminal panel; shells out to tenant-migrator
```

Other subcommands: `onedrive` (single user pair), `teams-channel`, `extract-chat`, `teams-chat`, `complete-chat`, `list-drives`.

Scope discovery / provisioning (read `.env` themselves; set `PYTHONIOENCODING=utf-8` when redirecting output on Windows):

```powershell
uv run python -m migration.teams.discover_chats --existing config/teams-pilot.yaml --output config/discovery/chats.yaml   # all chats of mapped users; keeps existing chat names
uv run python -m migration.teams.discover_channels --existing config/discovery/legacy-teams-channels.yaml --since 2026-01-01T00:00:00Z   # -> config/discovery/channels.yaml (channels + unresolved)
uv run python -m migration.sharepoint.discover_sites --target-host paizesoffice.sharepoint.com --existing config/sharepoint-pilot.yaml   # -> config/discovery/sites.yaml
uv run python -m migration.teams.provision_targets [--team "<name>"] [--apply]   # create missing target teams/channels in migration mode; DRY RUN unless --apply
uv run python -m migration.teams.provision_targets --finalize [--apply]          # after batch completes a team's channels: completeMigration on the team + add members
```

Teams chats that can never migrate (unmapped 1:1 participant, meeting chat, source chat 404 / other tenant, <2 mapped group members) are checkpointed `skipped` with detail `excluded (<reason>): ...` and reported as `excluded` - not `failed`. They are re-evaluated every run, so adding a user to the mapping picks them up automatically.

Credentials are read from process environment variables (`SOURCE_TENANT_ID`, `SOURCE_GRAPH_CLIENT_ID`, `SOURCE_GRAPH_CLIENT_SECRET`, and `TARGET_*` equivalents; see `config/examples/config.example.env`). The CLI does not call `load_dotenv`. Only `migration/accounts/password_reset.py` does.

## Architecture

```
migration-panel (panel.py) --subprocess--> tenant-migrator (cli.py) --> run_batch (batch.py)
                                                                          |  no domain logic here
                                     teams/services.py  onedrive/migration.py  sharepoint/migration.py
                                                                          |
                     common/: graph.py (GraphClient), auth.py (TokenProvider/MSAL), checkpoint.py (StateStore),
                              retry.py (with_retry), concurrency.py (AdaptiveGate, run_workers), reporting.py
```

- **GraphClient** retries 429/5xx and honors `Retry-After`. On 401 it refreshes the token through `TokenProvider`, for both JSON calls and raw byte transfers. Always build clients from a `TokenProvider`, never a static token, because runs outlive a single token. `set_throttle_hook()` connects 429s to the active `AdaptiveGate`.
- **OneDrive/SharePoint pipeline**: a sequential discovery and folder-prep walk (check-before-create, `conflictBehavior: fail`), then concurrent file transfer through `run_workers` + `AdaptiveGate`. SharePoint adds a separate, lower-concurrency permission stage. Concurrency comes from the `performance.concurrency.<workload>.<stage>` setting in the plan YAML.
- **StateStore** uses one SQLite table, `checkpoints`, with primary key `(workload, source_id)`, plus `user_key`, `permission_status`, and `teams_type` columns. A single connection is shared under a lock across worker threads. Optional `batch_size` buffering trades crash granularity for fewer commits.
- **Checkpoint files** live in one SQLite file per area: `<state-dir>/<area>-checkpoint.sqlite`, created by `open_state_store()`. `--state-dir` defaults to `state/`, and the panel reads `MIGRATION_STATE_DIR`. Workload key prefixes inside a file:
  - `onedrive` (per item, `{user}:{item_id}`) / `batch-onedrive` (per user)
  - `sharepoint` (per item, `{drive_id}:{item_id}`) / `batch-sharepoint` (per library)
  - `teams-message`, `teams-chat-message` (per Graph message id) / `teams-chat`, `teams-channel` (migration-mode lifecycle)
- **Parent gating**: a `batch-*` parent row must be marked `completed` only when it has zero child failures. Otherwise it stays `pending` so the next run re-enters it. This was a real false-completion bug in SharePoint, and OneDrive has the same open concern (see AGENTS.md).
- **Resume** works by re-running the same command against the same state dir. `completed` rows are skipped. `failed` and `pending` rows are retried.
- **Reports**: `batch` writes `reports/<area>-migration-result.json` and `validate` writes `reports/<area>-validation-result.json`. `validate` is a separate pass from `batch`. `python -m migration.onedrive.compare_report` is an ad-hoc source/target comparison script that writes a CSV.
- **Config**: active plans are `config/<area>-pilot.yaml` (panel defaults, overridable with `MIGRATION_CONFIG`). Plans can reference mapping fragments such as `config/*.mapping.pilot.yaml`. `*.example.*` files are templates.

`docs/ARCHITECTURE.md` explains the design, but parts of it are out of date. It describes a single root-level `sharepoint-pilot.sqlite` and `sharepoint-pilot.yaml`. The code now uses per-area files under `state/` and `config/`. Trust the code.

## Toward server-hosted execution

Goal: start a migration, shut down the laptop or browser, and have it keep running and resume correctly from durable state. The plan lives in `docs/CLOUD_HOSTED_MIGRATION_ROADMAP.md` (status: proposed, phases A–D). The current code is still laptop-bound in these ways:

- `migration-panel` runs `tenant-migrator` through a blocking `subprocess.run`, so the job dies with the operator's terminal session.
- Checkpoints are local SQLite files under `state/`, which is gitignored and machine-local. Another machine cannot resume the job unless the files are copied while no worker is running.
- Logs are ad-hoc `*.log` files in `logs/`, and reports are overwritten per area with no job ID in the filename. There is no durable per-job audit manifest yet.
- Credentials come only from process environment variables, with no secret-store integration.
- There is no job ID, lease, or heartbeat, and nothing prevents two workers from running against the same state.

When working on hosting:
- Keep domain modules unchanged. Add hosting concerns (job manifest, worker service, state backend, secrets, structured logging) around `run_batch` and `StateStore`.
- Change `StateStore` only behind its existing method surface (`status`, `target`, `mark`, `list_ids`, `count_by_status`, …), because every workload calls it.
- Phase B (one supervised worker, SQLite on persistent local disk) must pass the recovery acceptance tests in roadmap §7 before a shared store or a multi-worker design is introduced.

## Repo-specific skills

`.github/skills/` holds task playbooks: `onedrive-migration`, `teams-migration`, `checkpoint-debugging`, and `project-registry` (a map of which files to read for which concern). Read the matching one before working in that area.

## Do not touch casually

`data/`, `reports/`, `logs/`, and `state/` hold real migration output and live checkpoints. Do not edit or delete them as part of code changes.
