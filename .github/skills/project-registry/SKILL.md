---
name: project-registry
description: Locate relevant code, documentation, and architecture for the MS migration repository.
---

# Project Registry

## Purpose

Help the agent locate the smallest relevant set of files before
investigating or modifying code.

## Repository Scope

This repository migrates Microsoft 365 workloads across tenants.

Current focus:
- Teams migration
- Teams messages (group chat, 1:1 chat)
- Teams channels (channel messages)
- Teams files and attachments
- Checkpointing
- Migration performance

SharePoint and OneDrive migration are currently out of scope unless
explicitly requested.

## Repository Map

### Teams

Path:
`migration/teams/`

Responsibilities:
- Chat extraction
- Chat migration
- Channel message migration
- Teams-specific migration logic

### Orchestration

Path:
`migration/orchestration/`

Responsibilities:
- CLI
- Migration plan
- Batch execution
- Workload orchestration

### Common Infrastructure

Path:
`migration/common/`

Important modules:
- `graph.py` — Graph API client
- `checkpoint.py` — StateStore
- `concurrency.py` — concurrency utilities
- `auth.py` — authentication

### Configuration

Path:
`config/`

### Tests

Path:
`tests/`

## Investigation Procedure

1. Identify the requested workload.
2. Search for the relevant function or symbol.
3. Read its implementation.
4. Identify direct callers and dependencies.
5. Read relevant tests.
6. Read the relevant documentation.
7. Stop investigating when sufficient evidence exists.

## Context Rules

- Do not read the entire repository.
- Do not inspect unrelated workloads.
- Do not read large logs unless explicitly requested.
- Do not duplicate architecture documentation in the response.
- Prefer file paths and function names over large code excerpts.
- Do not modify files during investigation unless requested.

## Output

Return:
- Relevant files.
- Relevant functions.
- Execution flow.
- Findings.
- Recommended next step.