# SharePoint Migration Guide - Phased Approach

This guide covers the **5-phase SharePoint migration** separate from OneDrive, with focus on sites, libraries, files, folders, metadata, users, groups, and permissions.

## Overview

```
Phase 1: Sites & Libraries
        ↓
Phase 2: Files + Folders
        ↓
Phase 3: Metadata + Versions
        ↓
Phase 4: Users & Groups
        ↓
Phase 5: Permissions
        ↓
Validate Everything
```

## Prerequisites

1. **Separate App Registrations**: One for source tenant, one for target tenant
2. **Required Permissions**:
   - **Source**: `Sites.Read.All`, `Files.Read.All`
   - **Target**: `Sites.ReadWrite.All`, `Files.ReadWrite.All`, `Teamwork.Migrate.All`
3. **Admin Consent**: Obtained in both tenants
4. **Target Sites Created**: Create target SharePoint sites and document libraries BEFORE migration
5. **Environment Variables**: Set source and target credentials in `.env`

## Phase 1: Sites & Libraries Discovery

### Step 1a: Inventory Source Sites

```powershell
# Activate environment
cd c:\Users\Admin\Downloads\ms-migration
.\.venv\Scripts\Activate.ps1

# Load credentials
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
}

# List all sites and libraries
tenant-migrator list-drives --tenant source > source-sharepoint-inventory.json
```

**Output**: JSON with sites, libraries, and drive IDs needed for mapping.

Example:
```json
[
  {
    "site": "My plan",
    "site_id": "harbouroutdoorasialimited.sharepoint.com,xxx,yyy",
    "drive": "Documents",
    "drive_id": "b!xxx..."
  },
  {
    "site": "Design Task",
    "site_id": "harbouroutdoorasialimited.sharepoint.com,aaa,bbb",
    "drive": "Documents",
    "drive_id": "b!yyy..."
  }
]
```

### Step 1b: Prepare Target Sites

Create target sites and libraries manually:

1. Create target SharePoint sites (same names as source)
2. Create document libraries with matching names
3. Note the drive IDs using:

```powershell
tenant-migrator list-drives --tenant target > target-sharepoint-inventory.json
```

### Step 1c: Create Migration Mapping

Create `sharepoint-migration.yaml` with site/library pairs:

```yaml
migration_id: "sharepoint-prod-001"

users:
  - key: "admin"
    source_id: "admin@source.onmicrosoft.com"
    target_id: "admin@target.onmicrosoft.com"

workloads:
  sharepoint:
    sites:
      - name: "My plan"
        site_id: "source-site-id"
        libraries:
          - name: "Documents"
            source_drive_id: "b!source-drive-id..."
            target_drive_id: "b!target-drive-id..."
            action: "graph-copy"
            metadata:
              copy_versions: false
              copy_custom_columns: false
```

## Phase 2: Files & Folders Copy

### Dry-Run Test

```powershell
tenant-migrator --dry-run --state sharepoint-pilot.sqlite `
  batch --config sharepoint-migration.yaml `
  --report sharepoint-pilot-report.json
```

**Check Report**:
- ✓ File counts match expectations
- ✓ Folder structure correct
- ✓ No permission errors
- ✓ All mappings valid

### Production Migration

```powershell
tenant-migrator --state sharepoint-migration.sqlite `
  batch --config sharepoint-migration.yaml `
  --report sharepoint-migration-report.json
```

**What Happens**:
- Recursively walks source library
- Creates folder structure in target
- Downloads files from source (streaming for large files)
- Uploads files to target using resumable sessions for >4MB
- Tracks completion in SQLite state database
- Skips already-migrated items on rerun

### Resume Interrupted Migration

```powershell
# Use same state database to skip completed items
tenant-migrator --state sharepoint-migration.sqlite `
  batch --config sharepoint-migration.yaml `
  --report sharepoint-migration-report.json
```

**Statistics in Report**:
```json
{
  "files_copied": 1250,
  "folders_created": 85,
  "skipped": 42,
  "failed": 3,
  "errors": [
    {
      "item": "large-video.mp4",
      "type": "file",
      "error": "Upload failed: timeout"
    }
  ]
}
```

## Phase 3: Metadata & Versions

### Metadata Tracking

The migration tool logs which files have metadata and custom columns:

```powershell
# After Phase 2 completes, check report for metadata inventory
Get-Content sharepoint-migration-report.json | 
  ConvertFrom-Json | 
  Select-Object -ExpandProperty results | 
  Where-Object { $_.stats.metadata_fields -gt 0 }
```

### Current Limitations

✅ **Supported**:
- File names and folder structure
- File content and size
- Timestamps (lastModifiedDateTime)

❌ **Not Supported** (manual steps required):
- **Versions/History**: Not exposed by Graph migration APIs
- **Custom Columns/Site Columns**: Requires matching column definitions in target
- **Custom Properties**: Metadata-driven navigation, retention policies

### Manual Steps for Metadata

1. **For Custom Columns**:
   ```
   Source Site → Column Definitions → Export
   Target Site → Create Matching Columns
   Then copy metadata values per file
   ```

2. **For Version History**:
   ```
   Document Versioning is NOT copied
   Use source as archive; retention policy handles cleanup
   ```

3. **For Retention/Compliance**:
   ```
   Recreate retention policies in target site after migration
   Reapply sensitivity labels if using DLP
   ```

## Phase 4: Users & Groups

### User Mapping Discovery

```powershell
# Get all users/groups in source site
# (Currently informational; manual review required)

# Example: List groups in a site
$sourceToken = "..."  # From environment
$siteId = "..."  # From Phase 1 inventory

# User mapping is managed in the migration config:
# - master "users" list defines principals
# - Each workload can reference user keys
# - User IDs must be resolved via MS Graph before migration
```

### Group Reconciliation

**Before Migration**:
1. Document all source site groups
2. Create matching target site groups
3. Populate target groups with target tenant users
4. Note group object IDs for Phase 5

**Tool Support**: Group mapping is currently manual. Future enhancements will support:
- Auto-mapping groups with same display names
- Merging groups if consolidating sites
- Handling external participants

## Phase 5: Permissions & Sharing

### Current Status

⚠️ **Permissions migration is currently in review mode only** (not applied automatically).

### Manual Permission Steps

1. **Inventory Source Permissions**:
   ```powershell
   # After Phase 2, review which users/groups accessed each library
   # Check SharePoint UI for:
   # - Site owners and members
   # - Library-level permissions
   # - Item-level sharing
   ```

2. **Create Target Permissions**:
   ```
   Target Site → Settings → Site permissions
   Add target users with appropriate roles:
   - Site Owner
   - Site Member
   - Site Visitor
   ```

3. **Recreate Sharing Links** (if needed):
   ```
   Manual: Not automated due to complex sharing rules
   Consider: View-only vs Edit links, expiration, password
   ```

4. **Apply Item-Level Permissions**:
   ```
   Not migrated: Requires per-file policy review
   Recommendation: Use site-level access instead
   ```

### Permission Best Practices

- **Minimize item-level permissions** (use library roles instead)
- **Use security groups** for team permissions
- **Review external sharing** policies
- **Test** one library with sensitive content before bulk migration

## Validation & Reconciliation

### Validation Script

```powershell
# Compare source and target
$sourceReport = Get-Content source-sharepoint-inventory.json | ConvertFrom-Json
$targetReport = Get-Content target-sharepoint-inventory.json | ConvertFrom-Json

# Check file counts
foreach ($lib in $sourceReport) {
    Write-Host "Validating: $($lib.site)/$($lib.drive)"
    # (Your comparison logic here)
}
```

### Post-Migration Checks

```powershell
✓ File counts match
✓ Folder structure correct
✓ Timestamps preserved (lastModified)
✓ Large files verified (>100MB samples)
✓ Shared links redirected or recreated
✓ User access tested in target
✓ Retention policies reapplied
✓ Search crawled target library
```

## Troubleshooting

### Issue: "Access denied" on specific sites

**Solution**:
```powershell
# Verify the app has Sites.ReadWrite.All in target tenant
# Check: Azure Portal → App Registration → API permissions

# Test access to specific site:
tenant-migrator list-drives --tenant target --test-site "site-id-here"
```

### Issue: Large file uploads timeout

**Solution**:
- Increase timeout in `graph.py` (default 120s)
- Split very large files before migration
- Run during off-peak hours

### Issue: Duplicate files in target

**Solution**:
```powershell
# If rerunning migration, use same --state database
# State tracks completed items and skips them

# If state database is lost:
# Option 1: Recreate target library and rerun
# Option 2: Manually delete duplicates from target
```

### Issue: Metadata/columns lost

**This is expected**. Phase 3 requires manual steps:
1. Export column definitions from source site
2. Create matching columns in target site  
3. Apply metadata values manually or via PowerShell

## Example: End-to-End Migration

```powershell
# 1. SETUP
cd c:\Users\Admin\Downloads\ms-migration
.\.venv\Scripts\Activate.ps1

# 2. PHASE 1: DISCOVER
tenant-migrator list-drives --tenant source > source-inventory.json
tenant-migrator list-drives --tenant target > target-inventory.json
# → Create mapping file: sharepoint-migration.yaml

# 3. PHASE 2: TEST
tenant-migrator --dry-run --state pilot.sqlite `
  batch --config sharepoint-migration.yaml `
  --report pilot-report.json
# → Review report_report.json

# 4. PHASE 2: MIGRATE
tenant-migrator --state migration.sqlite `
  batch --config sharepoint-migration.yaml `
  --report migration-report.json
# → Monitor progress

# 5. PHASE 3: REVIEW METADATA
Get-Content migration-report.json | ConvertFrom-Json |
  Select-Object -ExpandProperty results |
  ForEach-Object { Write-Host "$($_.site): $($_.stats.metadata_fields) fields" }

# 6. PHASE 4-5: MANUAL STEPS
# → Verify user mappings in target
# → Apply permissions via SharePoint UI
# → Test user access

# 7. VALIDATE
# → Run validation checks
# → Spot-check files in target
# → Verify sharing links
```

## Next Steps

- Phase 3+ will receive Graph API enhancements for metadata/versions
- Phase 4 will support auto-group mapping when groups have matching names
- Phase 5 will support selective permission copying for common scenarios

For now, focus on **Phase 1-2** (sites/libraries, files/folders) which are fully supported.
