# Cloud-Hosted Migration Architecture Roadmap

**Status:** Proposed; not yet an implementation commitment  
**Goal:** Run multi-day migrations independently of the operator's laptop, with durable checkpoints, safe recovery, controlled configuration, and defensible post-migration audit.

## 1. Recommendation

Do not build a full web platform first. Evolve in stages:

1. Deploy the existing Python engine as a persistent server-side worker.
2. Harden checkpoint durability, restart recovery, observability, configuration, and audit.
3. Add an authenticated API/UI and durable queue only after the worker is reliable and there is a real need for remote job submission or concurrent jobs.

The browser and laptop are operator clients only. They must not own the migration process. The hosting compute must remain running for the job to continue.

## 2. Hosting options and decision criteria

Compare a small Azure VM with a suitable Azure container-hosting option using current pricing and company-specific constraints. Validate subscription access/credits, region, networking, security policy, continuous runtime requirements, restart behavior, disk persistence, monitoring, and monthly cost before selection.

### Initial baseline: one Azure VM

A small Linux VM may be the simplest baseline for an existing Python CLI because it can run a supervised process for long periods without an interactive terminal. It is not automatically the cheapest option; validate current pricing and operational overhead.

- Pin Python and dependency versions.
- Deploy a tagged/versioned code release.
- Run the worker under a service manager such as systemd.
- Keep state, logs, and reports on persistent disk.
- Configure backups, log rotation, health/disk alerts, restricted admin access, patching, and a recovery runbook.
- Ensure restart behavior cannot accidentally launch a second conflicting job.

### Container hosting alternative

Azure Container Apps or another approved container service may be suitable if the worker is packaged correctly and the selected configuration supports its continuous runtime, scaling, durable storage, and restart needs. Verify current platform limits and pricing rather than assuming.

### Do not assume these are suitable

- Short-lived serverless execution may have execution limits incompatible with multi-day work.
- GitHub Actions is useful for CI/tests, but should not be assumed to be a durable production migration host.
- The Synology NAS can be considered for approved backup/archive use, but is not a substitute for compute. Do not use SQLite over SMB as distributed coordination.

**Decision rule:** select the lowest total-cost option that meets runtime, durability, security, recovery, and support requirements. “Free” is not a win if the job is interrupted or state is lost.

## 3. Target architecture

### Phase 1 — persistent worker, minimal redesign

```text
Operator laptop / CLI
       | submit or inspect job
       v
Always-on Azure compute
  - existing Python migration engine
  - supervised single worker
  - persistent checkpoint database
  - structured logs and reports
       |
       v
Microsoft Graph: source and target tenants
```

Keep the CLI as an operator/admin tool initially. The migration worker runs on the server. A laptop or browser disconnect must not terminate the job.

### Phase 2 — control plane and queued jobs

```text
Browser UI
    |
Authenticated Migration API
    |
Durable job record / queue
    |
Migration worker(s)
    |                 |
Durable state       Microsoft Graph
and audit artifacts
```

The API validates and submits jobs; workers execute them. The UI reads status and requests controlled actions. Closing the browser must not cancel the job. Introduce a queue when it solves a real problem, not as a prerequisite to the first deployment.

## 4. Checkpoint state and storage

The repository currently documents a SQLite `StateStore`. Inspect the implementation before changing its guarantees.

### Single worker on one VM

SQLite may be acceptable as an initial solution if:
- the DB lives on persistent local disk;
- the worker and any readers follow a documented locking/transaction policy;
- only one active writer/job is permitted unless concurrency is proven safe;
- backups and restore are tested;
- integrity checks and disk monitoring are configured;
- a process/VM restart can resume safely.

Do not place SQLite on a NAS/SMB share for multi-worker coordination.

### Multiple workers or hosts

Before scaling out, use an approved transactional shared store (for example, Azure SQL or PostgreSQL) and explicit atomic job claiming, leases, heartbeats, stale-worker protection, and conflict detection. Do not simply let several workers access the same SQLite file.

### Required state model

Define legal transitions for job and item state, including `pending`, `running`, `completed`, `failed`, `retryable`, `skipped`, and applicable cancellation states. Store job ID, attempt count, timestamps, last error category, source/target references, and code/config version.

- Claim work atomically.
- Use leases/heartbeats when work can be reclaimed.
- Apply bounded retries and honor Graph `Retry-After`.
- Record successful work only after destination success is confirmed.
- Reconcile the ambiguous window where target write succeeds but checkpoint commit fails.
- Block parent completion while required child work is pending, failed, unknown, or unvalidated.
- Detect duplicate submissions and overlapping source/target scopes.
- Keep workload-specific state semantics where APIs differ.

## 5. Configuration and security

Separate four concerns:

1. **Deployment settings:** environment, log level, concurrency, retry policy, database/state path, report location, retention, and health-check settings.
2. **Job manifest:** unique job ID, source/target tenant IDs, workload, mappings, allowlisted scope, mode (`dry_run`, `fresh`, `resume`, `reconcile`, `validate`), and requested validation level.
3. **Secrets:** use an approved server-side secret store, such as Azure Key Vault where available. Never commit `.env`, client secrets, certificates/private keys, access tokens, production mappings, or tenant data.
4. **Identity and authorization:** document the Graph application permissions and admin consent required in each tenant. Prefer managed identity for Azure resource access when compatible, but do not assume it replaces tenant app registrations or credentials needed for Graph.

Apply least privilege, restricted server access, secret rotation, redaction, and an audit trail for start/retry/cancel/approval actions.

### Preflight gate

A job must fail before starting if:
- required config/secret references are missing;
- tenant IDs do not match the manifest;
- credentials cannot acquire tokens;
- required Graph permissions are missing;
- mappings are invalid or required source/target objects cannot be resolved;
- the scope is not allowed;
- a conflicting job is active;
- disk space/output storage is inadequate;
- concurrency settings exceed approved limits;
- required dry-run or plan approval has not occurred.

## 6. Monitoring and operations

Expose per job:
- job ID, workload, stage, state, heartbeat, start/end times;
- discovered, completed, failed, retryable, skipped, excluded, and unverified counts;
- worker activity and throughput;
- retry counts and Graph 429/5xx/401 counts;
- validation progress and outstanding discrepancies;
- code/config version and report location.

Use structured logs with job and item correlation, log rotation, access/retention controls, disk/health alerts, and a configurable no-progress alert. Document start, pause, cancel, restart, backup, restore, and incident procedures.

## 7. Recovery acceptance tests

Before production:
1. Shut down the operator laptop/browser: server job continues.
2. Kill the worker mid-operation: restart resumes safely.
3. Restart the VM: service recovers without launching conflicting work.
4. Interrupt network and restore it: bounded retries recover.
5. Renew/expire credentials: recover safely or report actionable failure.
6. Trigger Graph throttling: honor `Retry-After`.
7. Simulate destination success followed by checkpoint-write failure: reconciliation prevents loss or uncontrolled duplicate work.
8. Submit the same job twice: duplicate detection works.
9. Attempt an overlapping job: conflict is rejected or safely coordinated.
10. Back up and restore state: integrity and resume are verified.
11. Simulate corrupt state in test: fail safely and alert.
12. Verify required failed child items prevent parent success.

## 8. Post-migration audit

A generated report and a completed checkpoint are not sufficient audit evidence.

### Job audit manifest

Persist a versioned manifest containing:
- job ID, code commit/version, config/manifest version;
- source/target tenant IDs (never secrets);
- workload and scope;
- start/end times;
- discovered, attempted, copied/created, skipped, excluded, failed, retry-exhausted, and unverified totals;
- retry/throttling/error summary;
- validation mode and results;
- known limitations/unsupported features;
- final audit status and reviewer approval where required.

### Validation layers

**A. State audit**
- Compare expected work inventory with checkpoint state.
- Detect missing, duplicate, stale, pending, failed, or contradictory records.
- Ensure a final job has no unresolved active work.

**B. Source-to-target reconciliation**
- OneDrive/SharePoint: compare supported item identities, hierarchy/paths, file/folder counts, sizes, required metadata, and permissions when in scope and supported.
- Teams: compare selected teams/channels/chats/messages and supported members/attachments; explicitly list API restrictions and unsupported features.
- Reconcile checkpoint/target mismatches instead of trusting either one alone.

**C. Exception report**
Write missing, extra, mismatched, failed, skipped, excluded, unsupported, and unverified items to JSON/CSV with error category, retry history, source/target references, and recommended action. Redact sensitive data.

**D. Final status**
- `passed`: required checks pass with no unexplained required-item failures.
- `passed_with_exceptions`: only documented, approved exclusions/unsupported items remain.
- `failed`: required work is incomplete or unexplained discrepancies remain.
- `inconclusive`: evidence is insufficient to determine the outcome.

Produce machine-readable JSON/CSV and a human-readable summary. Retain logs, checkpoints, and reports under an IT-approved access and retention policy.

### Workload-specific gates

- **OneDrive:** reconcile per-item `onedrive`, per-user `batch-onedrive`, report totals, and validation. Never infer user completion from `copy_drive()` returning.
- **SharePoint:** validate required item results and configured metadata/permission checks; mark unsupported checks explicitly.
- **Teams:** validate selected message/member/attachment scope and ordering; document API or feature limitations rather than reporting false success.

## 9. Phased delivery

### Phase A — decision and cost validation
Confirm Azure subscription/credits, region, policy, network, support owner, budget, backup/retention needs. Compare current VM/container pricing and runtime constraints. Record an architecture decision.

**Exit:** hosting and budget approved.

### Phase B — one persistent worker
Deploy one worker, preserve current domain logic, configure process supervision, durable state, backup, remote logs, health checks, and conflict prevention.

**Exit:** laptop-offline, worker-kill, VM-restart, and resume tests pass on pilot data.

### Phase C — audit and recovery hardening
Formalize state transitions, test ambiguous destination/checkpoint failures, and standardize audit reports.

**Exit:** restore demonstrated; pilot exceptions are explained and accepted; false-completion issues are fixed.

### Phase D — UI/API and queue
Add authenticated job submission/status and a UI. Add a durable queue/shared transactional store when concurrent jobs or multiple workers are required.

**Exit:** browser/laptop disconnect has no effect, and access-control/job-state tests pass.

## 10. Guardrails

- Do not rewrite stable workload logic just to host it remotely.
- Do not add Kubernetes or distributed complexity without a measurable need.
- Do not use NAS/SMB-hosted SQLite for distributed coordination.
- Do not assume “free” services support days-long continuous execution.
- Do not mark a migration successful only because the process exited or checkpoints say `completed`.
- Make small changes with tests, rollout steps, and rollback instructions.
