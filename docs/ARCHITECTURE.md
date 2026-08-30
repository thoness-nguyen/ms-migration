# Architecture Design — Microsoft 365 Cross-Domain Migration Platform

## Status
Implemented (post [ADR-0001](../ADR-0001_%20Build%20an%20In-House%20Migration%20Platform%20for%20Cross-Domain%20Microsoft%20365%20Migration.md) rebuild, 2026-08-29/30).

## 1. Purpose

An in-house, license-free platform that migrates SharePoint, Teams, and OneDrive content between two Microsoft 365 tenants (source → target) using Microsoft Graph APIs. Built to be resumable, retry-safe, deduplicated, and observable, so a long-running migration can be stopped and restarted without data loss or duplication.

## 2. High-Level Architecture

```
                 ┌─────────────────────┐
                 │  Migration Panel     │   migration/orchestration/panel.py
                 │  (terminal UI)       │   console script: migration-panel
                 └──────────┬──────────┘
                            │ subprocess
                            ▼
                 ┌─────────────────────┐
                 │  CLI / Orchestrator  │   migration/orchestration/cli.py + batch.py
                 │  (run_batch)         │   console script: tenant-migrator
                 └──────────┬──────────┘
                            │ delegates per workload — no domain logic here
             ┌──────────────┼──────────────┐
             ▼              ▼              ▼
       ┌──────────┐   ┌──────────┐   ┌──────────┐
       │  Teams   │   │SharePoint│   │ OneDrive │
       │ services │   │ services/│   │migration │
       │          │   │migration/│   │          │
       │          │   │validation│   │          │
       └────┬─────┘   └────┬─────┘   └────┬─────┘
            │              │              │
            └──────────────┼──────────────┘
                           ▼
                  ┌────────────────────┐
                  │  common/           │
                  │  graph.py    (GraphClient, token-refresh, retry-on-429/5xx)
                  │  auth.py     (TokenProvider, MSAL)
                  │  checkpoint.py (StateStore — SQLite resume ledger)
                  │  retry.py    (with_retry — shared backoff wrapper)
                  │  reporting.py (report schema helpers)
                  └────────────────────┘
```

**Orchestration owns no domain logic.** It resolves plan → graph clients → checkpoint store, then calls into each domain module and assembles the combined report. All copy/import logic lives inside the domain packages.

## 3. Repository Layout

```
migration/
├── common/
│   ├── auth.py         # TokenProvider (MSAL, auto-refreshing), credential() env lookup
│   ├── graph.py         # GraphClient — request()/raw_request()/pages(), 401 + 429/5xx retry
│   ├── checkpoint.py    # StateStore — SQLite-backed (workload, source_id) -> status ledger
│   ├── retry.py         # with_retry() — generic backoff wrapper used by all domains
│   └── reporting.py     # new_report/domain_result/finalize_report — shared report schema
├── sharepoint/
│   ├── services.py      # site/library discovery, permission grants, metadata/version inventory
│   ├── migration.py     # copy_library() — recursive folder/file copy, chunked upload
│   └── validation.py    # validate_library_migration() — post-copy source/target count check
├── teams/
│   └── services.py      # extract_chat/import_chat/import_channel_messages (message-ID dedup)
├── onedrive/
│   └── migration.py     # copy_drive() — per-user recursive folder/file copy
└── orchestration/
    ├── cli.py            # argparse entry point (tenant-migrator)
    ├── batch.py          # run_batch() — coordinates all 3 domains, writes the report
    └── panel.py          # interactive terminal control panel (migration-panel)

config/     # yaml/json plan & mapping templates (examples + pilot fragments)
reports/    # generated migration-result*.json / *-report.json outputs
logs/       # captured *.log output from past runs
docs/       # this file + legacy runbooks + docs/legacy-scripts/ (superseded one-off scripts)
tests/      # pytest suite, imports from migration.*

sharepoint-pilot.yaml     # ACTIVE batch plan (live, at repo root — see §7)
sharepoint-pilot.sqlite   # ACTIVE checkpoint DB (live, at repo root — see §7)
```

## 4. Core Components

### 4.1 `common/graph.py` — `GraphClient`
- Thin wrapper over `requests.Session` for Microsoft Graph v1.0.
- `request(method, path, **kwargs)`: JSON calls. Retries on `429/500/502/503/504` (honors `Retry-After`), and on `401` refreshes the token via the provider and retries (up to 5 of 6 attempts) — this is what keeps hours-long migrations alive past a single token's ~60–90 min lifetime.
- `raw_request(method, url, **kwargs)`: same 401-refresh behavior for raw byte transfers (file download/upload chunks) that bypass the JSON wrapper for performance.
- `pages(path)`: generator that follows `@odata.nextLink` for paginated list endpoints.
- Accepts either a static token string or a callable (`TokenProvider`); refreshing updates the shared session's default headers, so every subsequent call (JSON or raw) benefits immediately.

### 4.2 `common/auth.py` — `TokenProvider`
- Wraps an MSAL `ConfidentialClientApplication` per tenant. Calling it acquires a token via `acquire_token_for_client`; MSAL caches internally and only re-hits the token endpoint when the cached token is near/past expiry, so repeated calls are cheap.
- `source_token()` / `target_token()` build providers from `SOURCE_*` / `TARGET_*` env vars (with legacy `GRAPH_CLIENT_*` fallback via `credential()`).

### 4.3 `common/checkpoint.py` — `StateStore`
- Single SQLite table: `checkpoints(workload, source_id, target_id, status, detail)`, primary key `(workload, source_id)`.
- Every domain's dedup/resume strategy is just a `workload` prefix over this one store:
  - `sharepoint` — per file/folder item, keyed `{drive_id}:{item_id}`
  - `batch-sharepoint` — per **library** (site+library id), gates whether a run re-enters that library at all
  - `onedrive` — per file/folder item, keyed `{user}:{item_id}`
  - `batch-onedrive` — per user
  - `teams-message` / `teams-chat-message` — per Graph message `id` (ADR's recommended Teams dedup key)
  - `teams-chat` / `teams-channel` — per chat/channel migration-mode lifecycle (`started`/`created`/`completed`)
- Resumability = re-run the same command with the same state file; anything already `completed` is skipped, anything `failed`/`pending` is retried.

### 4.4 `common/retry.py` — `with_retry()`
- Generic `(func, *args, max_retries, backoff_base_seconds, on_retry, **kwargs) -> (result, success, last_error, attempts_made)`.
- Retries on connection/timeout errors plus the broader per-item exception set (`GraphError, OSError, TypeError, ValueError, KeyError, AttributeError`) so one bad item can't silently kill a whole run without at least a few attempts.
- Used by `orchestration/batch.py` around both `copy_library` (SharePoint) and `copy_drive` (OneDrive) — previously SharePoint had its own bespoke retry wrapper and OneDrive had none; now both share identical behavior.

### 4.5 `common/reporting.py`
- `new_report()` / `domain_result()` / `finalize_report()` establish the ADR's required observability fields (job id, per-item area/status/counts/retry_count/validation) for any domain that wants a consistent report shape. `orchestration/batch.py`'s hand-rolled report currently coexists with this module for the SharePoint/OneDrive/Teams sections already in place.

## 5. Domain Modules

### 5.1 SharePoint (`migration/sharepoint/`)
- **services.py**: `resolve_site_url`, `discover_sites`, `get_site_libraries`, `inventory_sharepoint`, `grant_app_site_permissions`, `copy_file_metadata` (partial — site columns not supported), `get_file_versions` (inventory-only, Graph has no version-copy API), `get_site_groups`, `copy_item_permissions` (grants "Everyone: edit", falls back to "Organization" identity, never raises for GraphError — permission failures are logged and swallowed so they can't undo a successful file/folder copy).
- **migration.py**: `copy_library()` — runs as a pipeline (see §11): a sequential discovery + folder-preparation pass, then concurrent bounded file-content workers, then concurrent bounded permission-grant workers.
  - **Discovery/folder-prep** (sequential, parent-before-child): idempotency check-before-create (`GET` by path; only `POST` with `conflictBehavior: fail` if not found) — prevents duplicate folders / data loss that `conflictBehavior: replace` used to risk. Files are collected into a flat work list, not transferred yet.
  - **Content stage** (concurrent, bounded by `content_concurrency`, default 4): existing-file check, then simple `PUT` (≤4 MiB) or chunked resumable upload session (>4 MiB, 5 GB skip threshold configurable via `max_file_size_mb`).
  - **Permission stage** (concurrent, bounded by `permission_concurrency`, default 2 — kept lower than content per the upgrade design): grants apply to every folder/file created or copied in the two stages above.
  - Per-item exceptions are caught broadly and recorded in `state`/`stats["errors"]` without aborting the whole tree walk or the other concurrent workers; only genuine connection/timeout errors during discovery propagate up to trigger `with_retry`'s outer retry.
- **validation.py**: `validate_library_migration()` — recursively counts source vs target files/folders after a copy and reports `match: true/false`. Wired into `orchestration/batch.py` after every successful library copy (previously dead code).

### 5.2 Teams (`migration/teams/services.py`)
- `import_channel_messages()` — channel history import behind Graph's `startMigration`/`completeMigration` migration-mode lifecycle; dedup key = message `id`.
- `extract_chat()` / `import_chat()` — 1:1/group chat extraction + import with a source→target user map; dedup key = message `id` (`teams-chat-message` workload). Handles forwarded-message unwrapping, unsupported-feature counting (reactions/mentions/unresolvable attachments), and group-chat member re-add (Graph doesn't support `PATCH` on members, so it deletes+re-adds each with full visible history).

### 5.3 OneDrive (`migration/onedrive/migration.py`)
- `copy_drive()` — same discovery+folder-prep / concurrent-content-worker pipeline as SharePoint (§11), scoped to `/users/{id}/drive/...`. Folder creation uses the same idempotency check-before-create fix originally built for SharePoint (`conflictBehavior: fail` after an existing-item GET, not `replace`). No permission-grant stage (OneDrive migration never granted permissions in the original implementation, so there's nothing to parallelize there).

## 6. Orchestration

### 6.1 `orchestration/batch.py` — `run_batch()`
Coordinates, in order: Teams chats → OneDrive users → SharePoint sites. For SharePoint specifically:
1. Resolve `source_site_id`/`target_site_id` (from URL if IDs aren't given).
2. Discover libraries on both sides; match by name.
3. Per library: skip if `batch-sharepoint` checkpoint says `completed`; otherwise run `copy_library` through `with_retry`.
4. **Only mark the library `batch-sharepoint: completed` if `stats["failed"] == 0`.** If any per-item failures exist, mark it `pending` instead, so the *next* run re-enters that library and retries the specific failed/pending items (already-completed items are still skipped via the per-item `sharepoint` checkpoint). This was a real bug fixed 2026-08-29 — previously any clean walk (even with hundreds of per-item 401 failures inside) marked the whole library permanently `completed`, silently hiding an incomplete target forever.
5. Runs `validate_library_migration` after a successful copy and includes it in the report.

Writes a single JSON report per invocation: `{migration_id, generated_at, dry_run, results[], planned_workloads}`.

### 6.2 `orchestration/cli.py` — `tenant-migrator`
argparse-based entry point. Subcommands: `batch`, `onedrive`, `teams-channel`, `extract-chat`, `teams-chat`, `complete-chat`, `list-drives`. Builds `GraphClient`s from `TokenProvider`s (never a bare string) so every run gets automatic token refresh.

### 6.3 `orchestration/panel.py` — `migration-panel`
Lightweight terminal control panel (per ADR's mockup): select area (SharePoint / Teams / OneDrive / Run All) → start / monitor / status / failed-report / retry / clear-checkpoint. Talks to the same `StateStore` SQLite file directly for read-only status/report views, and shells out to `tenant-migrator batch` to actually run a migration. Forces UTF-8 stdout/stderr at startup (Windows consoles default to cp1252 and crash on emoji output otherwise).

## 7. Configuration & Live State (deliberately outside the folder taxonomy)

- **`sharepoint-pilot.yaml`** (repo root) — the real, currently-active batch plan (`workloads.sharepoint.sites[]` with direct site IDs). This is what both `tenant-migrator batch --config sharepoint-pilot.yaml` and `migration-panel` use by default (`CONFIG_FILE` env var override: `MIGRATION_CONFIG`).
- **`sharepoint-pilot.sqlite`** (repo root) — the real, currently-active checkpoint DB (`STATE_DB`/`MIGRATION_STATE_DB` override).
- **`config/`** holds *templates and example fragments* only (`*.example.*`, `*.mapping.pilot.yaml` fragments meant to be referenced from inside a hierarchical plan's `workloads.<domain>: <path>`, not run directly).
- These two live files were intentionally **not** moved into `config/` during the ADR restructuring — they're operational state/config actively referenced by running migrations, and moving them risked breaking in-flight checkpoints. If you rename or move them, update `panel.py`'s `STATE_DB`/`CONFIG_FILE` defaults (or the `MIGRATION_STATE_DB`/`MIGRATION_CONFIG` env vars) accordingly.

## 8. Reliability Model (ADR requirements → implementation)

| ADR requirement | Implementation |
|---|---|
| Retry (transient vs permanent) | `common/retry.py` `with_retry()`, shared by SharePoint + OneDrive batch orchestration |
| Resume / checkpoint | `common/checkpoint.py` `StateStore`, SQLite, independent of process memory |
| Deduplication | SharePoint/OneDrive: source+target item identity; Teams: Graph message `id` |
| Auth resilience | `TokenProvider` + `GraphClient` 401-refresh (both JSON and raw byte paths) |
| Observability | Per-run JSON report (`results[]` with status/items/stats/retries/validation) |
| Validation | `sharepoint/validation.py` wired into every successful library copy |
| Control panel | `orchestration/panel.py` — area select, start/monitor/status/report/retry |
| Background execution | Panel shells out to the CLI as a separate process; UI and engine are decoupled |

## 9. Performance & Concurrency

Status: implemented (2026-08-30), scoped from [ARCHITECHTURE_UPGRADE.md](../ARCHITECHTURE_UPGRADE.md). Evolves the platform from strictly sequential item-at-a-time processing to bounded, adaptive concurrent execution, without weakening any of the reliability guarantees in §8.

### 9.1 `common/concurrency.py`
- **`AdaptiveGate`** — a resizable semaphore. `record_throttle()` halves its capacity (down to a configured minimum) whenever the Graph API returns 429; `record_success()` grows capacity by 1 after a run of consecutive successes (up to a configured maximum). This implements the upgrade doc's "429 is a control signal, not just a retry" principle (§9 Principle 4).
- **`run_workers(items, worker_fn, max_workers, gate)`** — bounded `ThreadPoolExecutor` helper. Graph calls are I/O-bound, so threads (not processes) are sufficient. One item's exception never cancels or blocks the others (failure isolation, §21) — every result is returned as `(item, result, error)`.

### 9.2 SharePoint/OneDrive pipeline (`sharepoint/migration.py`, `onedrive/migration.py`)
Both now run the same staged pipeline instead of one giant recursive walk:
1. **Discovery + folder preparation** (sequential): still one recursive walk, but it only lists children and creates/reuses folders (parent must exist before its children — §8 Stage 3). Files are collected into a flat work list, not transferred yet.
2. **Concurrent content transfer**: `run_workers()` over the flat file list, bounded by an `AdaptiveGate` sized from `content_concurrency` (default 4). All existing per-item idempotency checks, error handling, and checkpoint marking are preserved exactly — they just now run from worker threads instead of one at a time.
3. **Concurrent permission grants** (SharePoint only): a second, separate `run_workers()` pass over every folder/file created or copied above, bounded by a *lower* `content_concurrency`-independent `AdaptiveGate` (`permission_concurrency`, default 2) — per the design's reasoning that permission APIs can have different service limits (§9 Principle 5, §8 Stage 6).

`GraphClient.set_throttle_hook()` is rewired to point at whichever gate is active for the current stage, so a 429 during content transfer shrinks the content gate and a 429 during permission grants shrinks the permission gate independently.

### 9.3 Thread safety
- `GraphClient`: added a lock around token refresh and the new `metrics` counters (`requests`, `throttled_429`, `server_errors_5xx`, `auth_refreshes`); connection pool size raised to match expected concurrency (`HTTPAdapter(pool_connections=20, pool_maxsize=20)`).
- `StateStore`: opened with `check_same_thread=False` + an internal lock, since multiple worker threads now call `mark()`/`status()`/`target()` concurrently against the same SQLite connection.
- Per-library `stats` dict (counts + `errors` list) is protected by a `threading.Lock` inside `copy_library`/`copy_drive`, since multiple worker threads increment/append to it.

### 9.4 Checkpoint batching (`common/checkpoint.py`)
`StateStore(path, batch_size=N)` buffers up to `N` pending `mark()` calls in memory and flushes them as a single `executemany` + one `commit()`, instead of committing every single item. `status()`/`target()` always check the in-memory buffer first, so behavior is identical to `batch_size=1` (the default, used everywhere today) from a caller's point of view — the only change is crash-recovery granularity: at most `batch_size - 1` marks could need reprocessing after an unexpected kill, which is an explicit, accepted trade-off per the design (§17).

### 9.5 Configuration
Optional `performance.concurrency.<workload>.<stage>` block in the batch plan YAML; absent entirely by default (existing `sharepoint-pilot.yaml` has none), in which case the safe defaults above apply:

```yaml
performance:
  concurrency:
    sharepoint:
      content: 4
      permissions: 2
    onedrive:
      content: 4
```

`orchestration/batch.py`'s `_concurrency()` helper reads this and passes it through to `copy_library`/`copy_drive` via `with_retry`'s `**kwargs`.

### 9.6 Deferred from the upgrade proposal (with reasons)
- **Teams message-level concurrency** — not implemented. `import_channel_messages`/`import_chat` rely on strictly increasing timestamps (`_timestamp()` bumps colliding times to stay ordered); Graph's channel/chat migration-mode import depends on that ordering. Parallelizing risks producing out-of-order or colliding timestamps, which would be a correctness regression the upgrade doc itself says not to accept (§4.3 non-goals). Left sequential per chat/channel.
- **Separate "metadata queue"** — SharePoint metadata copy (`copy_file_metadata`) is already a documented partial/no-op (§10 Known Limitations); there's nothing meaningful to parallelize.
- **Dedicated `fresh`/`resume`/`reconcile`/`validate` CLI modes** — not implemented as distinct modes. The existing per-item + per-library checkpoint states already provide equivalent behavior (`completed` → fast-path skip, `failed`/`pending` → reprocess); a first-class mode switch is a UX layer on top of that, deferred.
- **Live throughput dashboard in `panel.py`** — deferred. `GraphClient.metrics` and `AdaptiveGate.throttle_events` exist and are queryable, but aren't yet surfaced in the report JSON or the panel's status view.
- **Autotuning concurrency (upgrade doc §20)** — requires measuring real, live migration runs at different concurrency levels; left as configuration knobs (§9.5) for the user to tune per the doc's documented procedure rather than an automated experiment harness.

## 10. Known Limitations

- SharePoint site-column metadata, version history, and sharing links are **not** migrated (Graph has no supported API for the first two; the third would need manual reconfiguration post-migration).
- No wall-clock per-attempt timeout enforcement on Windows (the previous `SIGALRM`-based timeout was always a no-op on Windows; retry is bounded by attempt count + backoff only, not elapsed time).
- `common/reporting.py`'s standardized report helpers are not yet used by `orchestration/batch.py`'s hand-rolled report construction — the report shape works today but isn't yet unified through that module.
- Teams and OneDrive don't yet have their own `validation.py` equivalent to SharePoint's post-copy count check.

## 11. Testing

`tests/` (pytest, run via `python -m pytest`): covers batch plan loading/hierarchy validation, Teams chat/message body transformation and dedup logic, `StateStore` round-trips (including batched-write visibility and concurrent-thread safety), and `AdaptiveGate`/`run_workers` concurrency primitives (shrink/grow behavior, bounded concurrent acquisition, failure isolation). No live-Graph integration tests — all Graph calls are exercised only through real dry-runs and live runs against the actual tenants.
