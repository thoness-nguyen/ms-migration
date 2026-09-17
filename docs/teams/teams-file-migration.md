# Teams File Migration

## Scope

This document describes file and attachment migration associated with Teams.

## Important Distinction

Teams files are stored in SharePoint or OneDrive.

Teams message migration and file migration are separate operations.

Do not assume that importing a Teams message automatically migrates
its associated file.

## Current Implementation

Before making changes, inspect the repository for:

- Teams attachment handling.
- SharePoint file migration.
- OneDrive file migration.
- Graph file download/upload operations.
- Message attachment references.
- File mapping and checkpoint logic.

## Migration Flow (stale)

Source Teams message
    ↓
Identify attachment
    ↓
Determine file location
    ↓
Check whether file migration is required
    ↓
Migrate file
    ↓
Resolve target file reference
    ↓
Import message with target reference

# New Migration Flow

Source Teams message
    ↓
Identify attachment
    ↓
Determine file location (since files all migrated to target tenant SharePoint or OneDrive)
    ↓
find file in target SharePoint or OneDrive by name if not exist then migrate
    ↓
Import message with target reference

## Important Requirements

- Preserve file names and paths.
- Avoid duplicate file migration.
- Reuse existing file migration logic.
- Preserve checkpoint and retry behavior.
- Do not migrate the same file unnecessarily.
- Do not modify SharePoint or OneDrive migration logic
  without explicit approval.

## Investigation Rules

Before implementing file migration:

1. Identify the current attachment handling.
2. Identify the actual source file location.
3. Identify the target file location.
4. Trace existing file migration functions.
5. Check whether file migration already exists.
6. Identify missing functionality.
7. Implement only the missing part.

Do not assume the architecture document represents the current implementation.