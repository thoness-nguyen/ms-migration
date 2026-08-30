# SharePoint Large Library Migration Guide

## Overview

The enhanced migration system now supports automatic retry and timeout recovery for large SharePoint libraries that may fail during long copy operations.

## New Features (v2.0+)

### 1. **Automatic Retry on Timeout/Connection Errors**
- **Max retries**: 3 attempts per library
- **Backoff strategy**: 10s, 20s, 40s delays between retries
- **Timeout per attempt**: 15 minutes (900 seconds)
- **Triggers on**: Connection reset, timeouts, network errors

### 2. **Per-Folder Checkpoint Tracking**
- Each folder and file is tracked individually in SQLite state database
- If migration fails partway through, **resume from exactly where it stopped**
- No duplicates - already-copied items are skipped automatically

### 3. **Oversized File Handling**
- Default limit: 2 GB per file
- Oversized files are automatically skipped with a warning
- Counts tracked separately in migration report

### 4. **Better Progress Monitoring**
- Real-time progress % for large files (100+ MB)
- Connection error details with retry count
- Per-library statistics in final report

## Configuration

### Basic (No Changes Required)
```yaml
migration_id: sharepoint-pilot-001
workloads:
  sharepoint:
    sites:
      - name: "My Site"
        source_site_id: "tenant.sharepoint.com,guid1,guid2"
        target_site_id: "tenant.sharepoint.com,guid3,guid4"
```

### Advanced (Optional Parameters)
Parameters are in `tenant_migrator/batch.py` and `tenant_migrator/sharepoint.py`:

**In batch.py - `_copy_library_with_retry()`:**
- `max_retries=3`: Number of retry attempts (default: 3)
- `timeout_seconds=900`: Seconds per attempt (default: 15 minutes)

**In sharepoint.py - `copy_library()`:**
- `max_file_size_mb=2000`: Skip files larger than this (default: 2 GB)

## Usage

### Standard Run (with Auto-Retry)
```powershell
. .\.venv\Scripts\Activate.ps1
tenant-migrator --state migration.sqlite batch --config sharepoint-pilot.yaml --report result.json
```

### Dry Run (validates without copying)
```powershell
tenant-migrator --state migration.sqlite batch --config sharepoint-pilot.yaml --report result.json --dry-run
```

## Expected Behavior

### Successful Run
```
[DEBUG] Processing site: Harbour PD Work Flow
[DEBUG] Discovering libraries in source site...
[DEBUG] Found 2 libraries in source site
[DEBUG] Copying library: Documents
✓ Created folder: ALL APPROVED DWG RH HO CONTRACT
✓ Copied file: file1.dwg (0.35 MB)
✓ Copied file: file2.xlsx (3.03 MB)
  → large-file.dwg: 25% (512.5 MB / 2050 MB)
  → large-file.dwg: 50% (1025.0 MB / 2050 MB)
  → large-file.dwg: 75% (1537.5 MB / 2050 MB)
✓ Copied file: large-file.dwg (2050 MB)
[SUCCESS] Copy completed on attempt 1
```

### Timeout + Auto-Retry
```
[DEBUG] Copying library: Documents
✓ Copied file: file1.dwg
✓ Copied file: file2.xlsx
[TIMEOUT/CONNECTION] Attempt 1 failed: ConnectionResetError: An existing connection was forcibly closed
[RETRY] Retrying after error (attempt 2/3)...
[RETRY] Attempt 2/3 (waiting 10s)...
✓ Created folder: INDOOR PD
✓ Copied file: file3.dwg (skipped - already processed)
... continues from checkpoint ...
[SUCCESS] Copy completed on attempt 2
  "retries": 1
```

### Oversized Files
```
✓ Copied file: file1.dwg
⊘ Skipped oversized file: archive.iso (2500 MB > 2000 MB limit)
  "oversized_skipped": 1
```

## Migration Report Structure

```json
{
  "migration_id": "sharepoint-pilot-001",
  "dry_run": false,
  "results": [
    {
      "workload": "sharepoint",
      "site": "Harbour PD Work Flow",
      "status": "completed",
      "libraries": [
        {
          "name": "Documents",
          "status": "completed",
          "items": 542,
          "retries": 1,
          "stats": {
            "files_copied": 425,
            "folders_created": 87,
            "skipped": 30,
            "failed": 0,
            "oversized_skipped": 2,
            "retry_count": 1,
            "errors": [
              {"item": "archive.iso", "error": "File size exceeds 2 GB limit"}
            ]
          }
        }
      ]
    }
  ]
}
```

## Troubleshooting

### Issue: Migration still times out after retries
**Solution**: Increase timeout in `batch.py` line ~83:
```python
timeout_seconds=1800  # 30 minutes instead of 15
```

### Issue: Oversized files skipped
**Solution**: Increase file size limit in `sharepoint.py` line ~242:
```python
max_file_size_mb=5000  # 5 GB instead of 2 GB
```

### Issue: Partial migration (some folders missing)
**Solution**: Check state database and re-run:
```powershell
tenant-migrator --state migration.sqlite batch --config sharepoint-pilot.yaml --report result-retry.json
```
Migration will resume from exactly where it stopped.

### Issue: Connection reset after 10+ minutes
**Likely causes**:
1. Network timeout on source/target tenant
2. Graph API rate limiting (429 Too Many Requests)
3. SharePoint request processing limit

**Solutions**:
- Increase timeout between retries:
  ```python
  wait_time = min(120, 2 ** (attempt - 2) * 20)  # 20s, 40s, 80s
  ```
- Reduce chunk size for large files in `sharepoint.py`:
  ```python
  CHUNK_SIZE = 5 * 320 * 1024  # 1.6 MB per chunk instead of 3.3 MB
  ```
- Split into smaller sites/libraries manually

## State Database

Location: `sharepoint-pilot.sqlite` (or custom with `--state` flag)

Check progress:
```powershell
. .\.venv\Scripts\Activate.ps1
python3 -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM checkpoints WHERE status=\"completed\"')
print(f'Completed items: {cur.fetchone()[0]}')
conn.close()
"
```

Clear state (start fresh):
```powershell
rm sharepoint-pilot.sqlite
```

## Performance Tips

1. **For very large libraries (10,000+ items)**:
   - Consider splitting into multiple migrations per site
   - Use smaller libraries separately

2. **For slow networks**:
   - Reduce chunk size
   - Increase timeout

3. **Monitor progress**:
   - Keep terminal open to see real-time output
   - Check report.json after each run

## Next Steps After Migration

1. **Verify files in target**: Browse target SharePoint site
2. **Check permissions**: Not yet automated - may need manual setup
3. **Verify metadata**: Site columns need manual mapping
4. **Test access**: Ensure users can access migrated content

## Support

For issues, check:
- Console output (connection errors, timeouts)
- Report JSON (summary statistics)
- State database (item-level tracking)
