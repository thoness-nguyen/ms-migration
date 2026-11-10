# Technical Design --- High-Throughput Microsoft 365 Cross-Domain Migration Platform

## Status

Proposed --- performance-focused evolution of the current in-house
migration platform.

## 1. Purpose

This design extends the existing Microsoft 365 cross-domain migration
platform with a primary focus on **throughput, parallelism, API
efficiency, throttling resilience, and scalable execution**.

The existing design already provides important reliability capabilities:
resumability, retry-safe execution, deduplication, checkpointing,
authentication refresh, validation, and reporting. This proposal
preserves those capabilities while changing the execution model so that
migration is no longer fundamentally constrained by sequential,
item-by-item processing.

The design applies consistently to all supported workloads:

-   SharePoint
-   OneDrive
-   Teams

The platform remains based on Microsoft Graph and other
Microsoft-supported APIs already used by the implementation. The design
does **not** introduce paid migration products or paid migration
services. The migration engine must remain usable with the existing
in-house implementation and Microsoft APIs available to the
organization.

## 2. Problem Statement

The current platform is reliability-oriented but can become
throughput-limited because migration work is performed at a relatively
fine-grained item level.

For SharePoint, the current implementation recursively walks libraries
and processes folders/files individually. File migration can include
multiple network operations such as:

1.  Source discovery.
2.  Target existence/idempotency check.
3.  Source download.
4.  Target upload.
5.  Checkpoint update.
6.  Optional metadata or permission work.
7.  Validation.

When these operations are executed serially, network latency and API
round trips accumulate.

A migration that processes approximately 10 files in 30 minutes is
therefore not necessarily limited by raw network bandwidth. The more
likely constraints are:

-   insufficient concurrency;
-   excessive Graph round trips;
-   serial dependency between discovery and transfer;
-   metadata/permission operations blocking file transfer;
-   unnecessary target existence checks;
-   aggressive or inefficient retry behavior;
-   validation work occurring too close to the critical transfer path;
-   checkpoint writes occurring too frequently;
-   API throttling.

The new design treats **throughput as a first-class architectural
requirement**, while retaining correctness and recoverability.

## 3. Existing Design vs Proposed Design

### Existing model

``` text
Orchestrator
    |
    +--> Domain migration
            |
            +--> Discover item
            +--> Process item
            +--> Upload/import
            +--> Checkpoint
            +--> Next item
```

The effective execution model is predominantly item-at-a-time.

### Proposed model

``` text
Orchestrator
    |
    +--> Plan / Inventory
             |
             v
        Work Queues
             |
     +-------+-------+-------+
     |       |       |       |
     v       v       v       v
  Worker  Worker  Worker  Worker
     |       |       |       |
     +-------+-------+-------+
             |
             v
       Checkpoint / State
             |
             +--> Metadata Queue
             |
             +--> Permission Queue
             |
             +--> Validation
             |
             v
           Report
```

The core change is from **serial recursive copying** to a **bounded,
observable producer/consumer execution model**.

## 4. Design Goals

### 4.1 Primary goals

1.  Increase migration throughput through controlled concurrency.
2.  Reduce unnecessary Microsoft Graph API calls.
3.  Prevent one slow item from blocking unrelated items.
4.  Preserve resumability and idempotency.
5.  Handle Graph throttling adaptively.
6.  Keep authentication resilient during long-running migrations.
7.  Apply the same performance model to SharePoint, OneDrive, and Teams.
8.  Make throughput measurable instead of relying on elapsed time alone.
9.  Allow different workloads to have different concurrency limits.
10. Keep the implementation in-house and avoid dependency on paid
    migration platforms.

### 4.2 Secondary goals

1.  Support pause/resume.
2.  Support retry of failed work without restarting successful work.
3.  Provide live progress and throughput metrics.
4.  Support configurable migration modes.
5.  Make performance tuning possible without changing business/domain
    logic.

### 4.3 Non-goals

This design does not attempt to:

-   replace Microsoft Graph with an unsupported private API;
-   remove required validation or security controls;
-   guarantee a fixed files-per-minute rate;
-   bypass Microsoft service throttling;
-   introduce paid migration software or paid migration services;
-   sacrifice correctness for raw transfer speed.

## 5. Core Performance Principles

### Principle 1 --- Parallelize independent work

Files, messages, users, folders, and other independent migration units
should be processed concurrently where the destination dependency graph
permits it.

Concurrency must be bounded and configurable.

### Principle 2 --- Separate discovery from execution

Discovery should produce migration work items rather than immediately
performing the entire migration recursively.

This allows the engine to keep workers busy while discovery continues.

### Principle 3 --- Minimize API round trips

Every Graph request has latency and contributes to throttling pressure.

The implementation should avoid unnecessary:

-   existence checks;
-   repeated folder discovery;
-   repeated authentication calls;
-   repeated metadata reads;
-   repeated validation scans.

### Principle 4 --- Throttling is a control signal

HTTP 429 is not simply a failure to retry.

It is a signal that the worker pool is producing more demand than the
service currently wants to accept.

The engine should:

1.  honor `Retry-After`;
2.  temporarily reduce pressure;
3.  retry transient work;
4.  gradually recover concurrency;
5.  record throttle metrics.

### Principle 5 --- Reliability must survive parallelism

Parallel execution must not weaken:

-   checkpointing;
-   deduplication;
-   retry behavior;
-   authentication refresh;
-   error isolation;
-   validation;
-   reporting.

### Principle 6 --- Optimize the critical path

File/content transfer should not unnecessarily wait for:

-   full-library validation;
-   unrelated metadata processing;
-   unrelated permission processing;
-   report generation;
-   other independent files.

## 6. Proposed Platform Architecture

``` text
                         +----------------------+
                         |   Migration Panel    |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | CLI / Orchestrator   |
                         +----------+-----------+
                                    |
                                    v
                         +----------------------+
                         | Migration Planner    |
                         | - plan validation    |
                         | - workload planning  |
                         | - concurrency config |
                         +----------+-----------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
              v                     v                     v
       SharePoint Planner      OneDrive Planner       Teams Planner
              |                     |                     |
              +---------------------+---------------------+
                                    |
                                    v
                         +----------------------+
                         | Work Queue Layer     |
                         | bounded queues       |
                         +----------+-----------+
                                    |
             +----------------------+----------------------+
             |                      |                      |
             v                      v                      v
      Content Workers        Metadata Workers       Permission Workers
             |                      |                      |
             +----------------------+----------------------+
                                    |
                                    v
                         +----------------------+
                         | State / Checkpoint   |
                         | SQLite                |
                         +----------+-----------+
                                    |
                   +----------------+----------------+
                   |                                 |
                   v                                 v
             Validation                         Reporting
```

## 7. Workload Model

Each migration workload should be represented as a unit of work with
enough information for a worker to operate independently.

Example:

``` json
{
  "workload": "sharepoint",
  "source_tenant": "source",
  "target_tenant": "target",
  "source_container_id": "...",
  "source_item_id": "...",
  "source_path": "/Documents/example.docx",
  "target_path": "/Documents/example.docx",
  "item_type": "file",
  "size_bytes": 123456,
  "status": "pending",
  "attempt": 0
}
```

The exact schema may differ by workload.

### SharePoint

Natural work unit:

``` text
file / folder
```

### OneDrive

Natural work unit:

``` text
file / folder
```

### Teams

Natural work unit may be:

``` text
message
chat
channel
attachment
```

The worker granularity should be selected based on the dependency and
API characteristics of the operation.

## 8. Pipeline Stages

### Stage 1 --- Planning

Validate:

-   source configuration;
-   target configuration;
-   workload mappings;
-   authentication;
-   destination availability;
-   concurrency settings.

No migration data is changed.

### Stage 2 --- Inventory / Discovery

Enumerate source content and produce work items.

For SharePoint:

``` text
Site
  -> Library
      -> Folder tree
          -> File work items
```

For OneDrive:

``` text
User
  -> Drive
      -> Folder tree
          -> File work items
```

For Teams:

``` text
Team / Channel / Chat
  -> Message / attachment work items
```

### Stage 3 --- Dependency preparation

Create required destination structures before content workers depend on
them.

For example:

``` text
Create folders
     |
     v
Folder structure ready
     |
     v
File workers
```

This prevents a file worker from being blocked waiting for its parent
folder.

### Stage 4 --- Content migration

Run bounded concurrent workers.

``` text
Queue
 |
 +--> Worker 1
 +--> Worker 2
 +--> Worker 3
 +--> ...
 +--> Worker N
```

Workers should be independent whenever possible.

### Stage 5 --- Metadata migration

Metadata work should be decoupled from raw content transfer where the
target API semantics allow it.

``` text
Content complete
      |
      v
Metadata queue
      |
      +--> metadata workers
```

### Stage 6 --- Permission migration

Permissions should be processed independently from raw file transfer
where possible.

``` text
Content complete
      |
      v
Permission queue
      |
      +--> permission workers
```

Permission concurrency should normally be lower than content concurrency
because security-related operations can have different service limits
and side effects.

### Stage 7 --- Validation

Validation should be configurable:

``` text
none
lightweight
full
```

Full recursive validation should normally be performed after content
transfer rather than unnecessarily blocking the transfer pipeline.

## 9. Concurrency Architecture

### 9.1 Bounded worker pools

The system must never create unbounded tasks.

Example starting configuration:

``` yaml
concurrency:
  sharepoint_content: 8
  sharepoint_metadata: 4
  sharepoint_permissions: 2

  onedrive_content: 8
  onedrive_metadata: 4
  onedrive_permissions: 2

  teams_messages: 8
  teams_attachments: 4
```

These values are starting points, not guaranteed optimal values.

The engine should measure actual performance and throttling before
increasing them.

### 9.2 Adaptive concurrency

A worker pool should react to service behavior.

Conceptually:

``` text
Initial concurrency
       |
       v
   Process work
       |
       +---- normal response ----> gradually increase
       |
       +---- 429 -----------------> honor Retry-After
                                      |
                                      v
                                reduce concurrency
                                      |
                                      v
                                   recover
```

The implementation should avoid repeatedly increasing concurrency when
the service is already throttling.

### 9.3 Workload-specific limits

SharePoint, OneDrive, and Teams should not necessarily share one global
worker limit.

A slow Teams message operation should not prevent SharePoint file
transfers from progressing.

## 10. SharePoint Design

### 10.1 Current behavior

The current SharePoint migration recursively walks a library and
performs per-item processing, including idempotency checks and
simple/chunked upload behavior.

### 10.2 Proposed behavior

Refactor `copy_library()` into logical stages:

``` text
inventory_library()
       |
       v
prepare_folders()
       |
       v
enqueue_files()
       |
       v
run_content_workers()
       |
       v
enqueue_metadata()
       |
       v
run_metadata_workers()
       |
       v
enqueue_permissions()
       |
       v
run_permission_workers()
       |
       v
validate()
```

### 10.3 Fresh migration mode

When the destination is known to be empty or controlled by the migration
plan, avoid unnecessary target existence checks.

``` text
Fresh mode:
checkpoint -> completed ? skip : upload
```

The migration plan must explicitly declare whether the target is
expected to be empty.

### 10.4 Resume mode

Use the existing checkpoint ledger as the primary fast-path:

``` text
completed -> skip
pending/failed -> process
unknown -> process
```

A target existence check should be used when needed to reconcile
uncertain state rather than automatically performing it for every
known-new item.

### 10.5 Reconcile mode

Use stronger target-side checks for:

-   interrupted migrations;
-   externally modified targets;
-   uncertain checkpoint state;
-   post-migration reconciliation.

This mode can trade throughput for stronger correctness verification.

## 11. OneDrive Design

OneDrive should use the same high-throughput model as SharePoint:

``` text
User
  |
  v
Drive inventory
  |
  v
Folder preparation
  |
  v
Content queue
  |
  +--> concurrent workers
  |
  v
Metadata / follow-up queues
  |
  v
Validation
```

The per-user checkpoint model remains intact.

Users should be independently schedulable so that one problematic user's
drive does not block the entire migration.

Potential execution model:

``` text
User A -> workers
User B -> workers
User C -> workers
User D -> workers
```

Global concurrency must still be bounded to avoid excessive service
pressure.

## 12. Teams Design

Teams migration should apply the same principles but use
message/chat/channel appropriate work units.

### Channel messages

``` text
Channel inventory
       |
       v
Message queue
       |
       +--> concurrent message workers
```

### Chat migration

Because chat lifecycle operations may have dependencies, the system
should separate:

``` text
Chat lifecycle
     |
     +--> create/import context
     |
     v
Message queue
     |
     +--> message workers
     |
     v
Completion
```

Message-ID deduplication remains mandatory.

Attachments should be treated as independent work where the Graph/API
dependency permits it.

## 13. Graph Client Requirements

The existing `GraphClient` should remain the common abstraction.

It must provide:

-   token refresh;
-   connection reuse;
-   429 handling;
-   `Retry-After` handling;
-   transient 5xx retry;
-   raw byte transfer support;
-   pagination;
-   request correlation;
-   timeout configuration;
-   telemetry.

The Graph client should use a reusable HTTP session rather than creating
a new network connection for every request.

## 14. Authentication

The existing MSAL `TokenProvider` model should remain.

Workers must never manually manage long-lived access tokens.

The architecture should ensure:

``` text
Worker
  |
  v
GraphClient
  |
  v
TokenProvider
  |
  v
MSAL token cache
```

A 401 must trigger token refresh/reacquisition and a bounded retry.

Token acquisition should not become a per-file operation.

## 15. Retry and Throttling

### Retry categories

Transient:

-   429
-   500
-   502
-   503
-   504
-   connection failures
-   timeout failures

Permanent or item-specific:

-   invalid path;
-   unsupported item;
-   invalid request;
-   missing required mapping;
-   known authorization/configuration error.

Permanent errors should not consume unlimited retries.

### 429 strategy

``` text
Receive 429
     |
     v
Read Retry-After
     |
     v
Pause affected work
     |
     v
Reduce worker pressure
     |
     v
Retry
     |
     v
Gradually restore concurrency
```

The engine must record:

-   number of 429 responses;
-   retry delay;
-   affected workload;
-   worker concurrency at the time;
-   final outcome.

## 16. API Call Reduction

Before optimizing CPU, identify every Graph request made per migration
item.

A performance report should expose something similar to:

``` text
Item
  discovery requests:       1
  target checks:            1
  download requests:        1
  upload requests:          N
  metadata requests:        X
  permission requests:      Y
  checkpoint writes:        1
```

The engineering goal is:

``` text
fewer API calls per migrated item
```

rather than simply:

``` text
more workers
```

Increasing concurrency while retaining unnecessary API calls can
increase throttling without producing meaningful throughput improvement.

## 17. Checkpoint Optimization

The existing SQLite state store remains the source of truth for
resumability.

However, checkpoint writes should be optimized.

Preferred pattern:

``` text
Worker completion
      |
      v
in-memory completion event
      |
      v
checkpoint writer
      |
      v
batched SQLite transaction
```

The implementation must preserve crash safety.

A small amount of safe reprocessing after an unexpected process
termination is preferable to compromising checkpoint correctness.

Checkpoint updates must remain idempotent.

## 18. Validation Strategy

### Lightweight validation

Suitable for frequent progress checks:

``` text
source item count
target item count
completed checkpoint count
failed item count
pending item count
```

### Full validation

Used after a migration stage or library is complete:

``` text
recursive source/target comparison
folder comparison
file comparison
size comparison
required metadata comparison
```

Validation should not repeatedly re-enumerate a large library while the
content workers are still trying to maximize throughput unless
explicitly requested.

## 19. Observability

The migration platform must expose throughput metrics.

### Required metrics

``` text
total_items
completed_items
pending_items
failed_items
active_workers
throughput_items_per_minute
throughput_mb_per_minute
average_item_duration
p95_item_duration
retry_count
throttle_count
authentication_refresh_count
api_error_count
```

### Per-stage timing

``` text
discovery_duration
folder_preparation_duration
content_duration
metadata_duration
permission_duration
validation_duration
```

### Example

``` text
Migration Performance

Items:
  discovered:       12,450
  completed:         8,392
  pending:           4,044
  failed:               14

Workers:
  active:               12
  configured:            16

Throughput:
  items/minute:         5.4
  MB/minute:           82.3

API:
  429:                   17
  5xx:                    2
  401 refreshes:          1

Average:
  item:                 11.2 sec
  upload:                7.4 sec
```

The system should use these metrics to determine the bottleneck before
changing architecture or configuration.

## 20. Performance Tuning Procedure

Do not immediately set maximum concurrency.

Use controlled experiments.

Example:

``` text
Concurrency 4
    |
    v
measure

Concurrency 8
    |
    v
measure

Concurrency 12
    |
    v
measure

Concurrency 16
    |
    v
measure
```

Record:

``` text
throughput
429 rate
average latency
p95 latency
CPU
memory
network utilization
```

Stop increasing concurrency when:

-   throughput stops improving;
-   throttling increases significantly;
-   latency becomes unstable;
-   failure/retry rates increase.

The selected value should be workload-specific.

## 21. Failure Isolation

One failed item must not stop unrelated migration work.

``` text
Worker 1 -> success
Worker 2 -> failure -> checkpoint failed
Worker 3 -> success
Worker 4 -> success
```

The failed item enters the retry/failure queue while other workers
continue.

The whole library/workload should only be considered complete when all
required work units reach the required terminal state.

This preserves the existing fix where a library with per-item failures
must not be marked fully completed.

## 22. Backpressure

Queues must have bounded capacity.

If workers cannot consume work because Graph is throttling:

``` text
Discovery
   |
   v
Bounded queue
   |
   X queue full
   |
Discovery slows
```

This prevents the process from accumulating unlimited work in memory.

Backpressure also allows the system to remain stable during very large
migrations.

## 23. Resource Management

The engine must monitor:

-   CPU;
-   memory;
-   disk usage;
-   network throughput;
-   temporary file usage;
-   open connections;
-   queue depth.

Large files should not cause the system to load many complete files into
memory simultaneously.

Prefer streaming/chunked transfers where supported.

## 24. Migration Modes

The platform should support explicit modes:

### `fresh`

Optimized for a controlled empty target.

``` text
minimal target checks
high content throughput
checkpoint enabled
```

### `resume`

Optimized for interrupted migration.

``` text
checkpoint-first
retry incomplete items
safe idempotency
```

### `reconcile`

Optimized for correctness after uncertain state.

``` text
stronger target checks
more validation
lower throughput accepted
```

### `validate`

No migration changes.

``` text
source vs target analysis
checkpoint analysis
missing/extra item reporting
```

## 25. Configuration

Example:

``` yaml
performance:
  mode: resume

  concurrency:
    sharepoint:
      content: 8
      metadata: 4
      permissions: 2

    onedrive:
      content: 8
      metadata: 4
      permissions: 2

    teams:
      messages: 8
      attachments: 4

  throttling:
    adaptive: true
    honor_retry_after: true
    max_concurrency: 16

  checkpoint:
    batch_size: 50

  validation:
    mode: lightweight
```

The actual values must be tuned using observed migration metrics.

## 26. Compatibility With Existing Components

The proposal should reuse the existing architecture rather than replace
it.

  Existing component           Proposed treatment
  ---------------------------- -----------------------------------------------
  `common/auth.py`             Keep
  `common/graph.py`            Keep and extend telemetry/concurrency support
  `common/checkpoint.py`       Keep; optimize write path
  `common/retry.py`            Keep; extend adaptive throttling behavior
  `common/reporting.py`        Expand to include performance metrics
  `sharepoint/services.py`     Keep domain discovery/services
  `sharepoint/migration.py`    Major performance refactor
  `sharepoint/validation.py`   Keep; make execution mode configurable
  `onedrive/migration.py`      Apply same worker/queue model
  `teams/services.py`          Apply workload-specific parallelism
  `orchestration/batch.py`     Add planner/worker orchestration
  `orchestration/panel.py`     Add live performance visibility

## 27. Implementation Phases

### Phase 1 --- Instrumentation

Before changing behavior:

-   measure per-operation latency;
-   count Graph requests;
-   count 429/5xx/401;
-   measure throughput;
-   measure queue/worker activity;
-   identify current bottleneck.

### Phase 2 --- Concurrent content migration

Implement bounded workers for:

-   SharePoint files;
-   OneDrive files;
-   Teams messages where safe.

Start with conservative concurrency.

### Phase 3 --- API-call reduction

Review every per-item request.

Remove or avoid unnecessary:

-   target existence checks;
-   duplicate discovery;
-   repeated metadata reads;
-   redundant validation calls.

### Phase 4 --- Separate queues

Introduce independent queues for:

-   content;
-   metadata;
-   permissions;
-   validation.

### Phase 5 --- Adaptive throttling

Add:

-   dynamic concurrency reduction;
-   `Retry-After` handling;
-   controlled recovery;
-   throttle metrics.

### Phase 6 --- Checkpoint batching

Optimize SQLite writes without weakening crash recovery.

### Phase 7 --- Validation and reporting optimization

Move expensive validation away from the critical transfer path where
appropriate and standardize performance reporting.

### Phase 8 --- Cross-workload tuning

Tune SharePoint, OneDrive, and Teams independently using measured data.

## 28. Acceptance Criteria

The redesign is successful when:

1.  Multiple independent migration items can be processed concurrently.
2.  A slow item does not block unrelated items.
3.  The system remains resumable after process interruption.
4.  Completed items are not unnecessarily migrated again.
5.  429 responses are honored and handled without runaway retries.
6.  Authentication continues to work during long-running migrations.
7.  Per-stage throughput and latency are observable.
8.  SharePoint, OneDrive, and Teams use the same core execution
    principles.
9.  Full validation can be enabled without being mandatory for every
    transfer operation.
10. Performance can be tuned through configuration rather than
    source-code changes.
11. No paid migration platform or paid migration service is required by
    the design.
12. The system does not attempt to bypass Microsoft service limits or
    supported API behavior.

## 29. Expected Result

The main expected improvement is not a single hardcoded files-per-minute
target.

The intended change is:

``` text
OLD

discover
  -> process
      -> wait
          -> process
              -> wait
                  -> process


NEW

discover
  -> queue
      -> worker 1 ─┐
      -> worker 2  ├-> concurrent migration
      -> worker 3  │
      -> worker 4 ─┘
              |
              v
       checkpoint/state
              |
              +-> metadata
              +-> permissions
              +-> validation
```

This allows the platform to use available network/API capacity more
effectively while remaining within service limits.

## 30. Architectural Decision Summary

The current architecture should **not be replaced**.

The recommended direction is to evolve it from:

> **reliable sequential migration**

to:

> **reliable, concurrent, measurable, adaptive migration**

The key architectural changes are:

1.  **Bounded concurrency**
2.  **Inventory/work queues**
3.  **Separate content, metadata, and permission pipelines**
4.  **API-call minimization**
5.  **Adaptive throttling**
6.  **Batched checkpoint persistence**
7.  **Configurable validation**
8.  **Performance telemetry**
9.  **Workload-specific tuning**
10. **Preservation of the existing free, in-house implementation model**

The reliability foundation remains unchanged:

``` text
MSAL token refresh
        +
Graph retry
        +
checkpoint
        +
deduplication
        +
validation
        +
reporting
```

The execution layer becomes:

``` text
                Planner
                   |
                Queues
                   |
          +--------+--------+
          |        |        |
        Workers  Workers  Workers
          |        |        |
          +--------+--------+
                   |
             State / Report
```

This is the recommended next-generation architecture for increasing
migration throughput across the entire Microsoft 365 migration platform
without sacrificing resumability, correctness, or the requirement to use
an in-house, no-paid-service approach.
