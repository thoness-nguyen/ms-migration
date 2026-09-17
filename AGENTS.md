# MS Migration Agent Instructions

## Purpose

This repository migrates Microsoft 365 workloads across tenants,
including Teams, SharePoint, and OneDrive.

## Core principles

- Preserve resumability.
- Preserve idempotency.
- Never sacrifice migration correctness for throughput.
- Use bounded concurrency.
- Respect Microsoft Graph throttling.
- Checkpoint only after successful work.
- Avoid unrelated refactoring.

## Important architecture

See:
- docs/ARCHITECHTURE_UPGRADE.md
- docs/teams/
- docs/sharepoint/
- docs/onedrive/

## Teams

Main implementation areas:

- teams/
- batch runner
- state/checkpoint store
- Graph client

Before changing Teams migration logic:
1. Trace the current call chain.
2. Identify checkpoint behavior.
3. Identify concurrency boundaries.
4. Preserve existing retry semantics.

## Change discipline

For a focused task:

- Inspect only relevant files first.
- Prefer the smallest viable change.
- Do not rewrite unrelated code.
- Run targeted tests.
- Report changed files and tests.

# Token Efficient Coding Agent

## Context policy

Use the smallest context necessary.

### Before reading files

Start with symbol search.

Do NOT:
- read the entire repository;
- read unrelated services;
- inspect generated files;
- read all documentation;
- recursively inspect every directory.

### Investigation order

1. Search for the requested symbol.
2. Read its implementation.
3. Find direct callers.
4. Find direct dependencies.
5. Read related tests.
6. Read architecture documentation only if needed.

### Implementation

After sufficient evidence:
- implement the smallest change;
- avoid unrelated refactoring;
- reuse existing abstractions;
- do not duplicate functionality.

### Verification

Run targeted tests first.

Only expand test scope if:
- targeted tests fail;
- the change affects shared infrastructure;
- integration behavior requires broader validation.

### Response policy

Return:
- summary;
- changed files;
- tests;
- unresolved issues.

Do not dump large code blocks unless requested.