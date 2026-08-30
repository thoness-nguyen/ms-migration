# SharePoint Migration - Local Debug Run Guide

## 🚀 QUICK START - Run Migration Locally

### Step 1: Activate Virtual Environment
```powershell
cd c:\Users\Admin\Downloads\ms-migration
. .\.venv\Scripts\Activate.ps1
```

### Step 2: Run Migration with Debug Output
```powershell
# Option A: Using the helper script (Recommended)
python run_migration_debug.py

# Option B: Direct command with debug flags
$env:PYTHONUNBUFFERED=1
tenant-migrator --state sharepoint-pilot.sqlite batch --config .\sharepoint-pilot.yaml --report sharepoint-result.json --verbose
```

### Step 3: Monitor Progress (In Another Terminal)
```powershell
cd c:\Users\Admin\Downloads\ms-migration
. .\.venv\Scripts\Activate.ps1
python monitor_progress.py
```

---

## 🔍 Understanding Debug Output

### Live Progress Display
```
>> HARBOUR 10-2420114.psd: 50% (15.6 MB / 21.0 MB)
>> HARBOUR 10-2420116.psd: 100% (30.9 MB / 30.9 MB)
[+] Copied file: HARBOUR 10-2420116.psd (30.93 MB)
```

**What it means:**
- `>>` = Currently uploading chunks
- `[+]` = Successfully completed
- `[!]` = Warning
- `[-]` = Error/failure

### Configuration Details (stored in sharepoint-pilot.yaml)
```yaml
source_site: "https://harbouroutdoorasialimited.sharepoint.com/sites/HarbourPDWorkFlow"
target_site: "https://paizesoffice.sharepoint.com/sites/HarborPD"
libraries:
  - source_library: "Documents"
    target_library: "Documents"
```

### Key Limits
```
Max File Size:     5 GB
Timeout per item:  1800 seconds (30 minutes)
Max Retries:       3 attempts with exponential backoff
Chunk Size:        10 MB
Permission Mode:   Everyone can edit
```

---

## 📊 After Migration - Generate Failed Items Report

### View Failed Items Summary
```powershell
python generate_failed_report.py
```

**Output includes:**
- Total failed count
- Error categories (401, 403, timeout, encoding, etc.)
- Item IDs and specific error messages
- Exported JSON report for tracking

### Export Detailed Report
The script automatically creates a JSON file: `failed-items-report-YYYYMMDD-HHMMSS.json`

Example report structure:
```json
{
  "generated_at": "2026-08-29T04:44:45",
  "total_failed": 42,
  "error_categories": {
    "401 Unauthorized": 15,
    "Timeout - Connection too slow": 20,
    "Encoding - Invalid filename": 7
  },
  "failed_items": [
    {
      "source_id": "01P23IA5Q2MILSSNKFGFB25PO5G5URDTUD",
      "error_type": "Timeout",
      "error_detail": "Connection timeout after 1800 seconds"
    }
  ]
}
```

---

## 🔄 Retry Failed Items

### Check Current Status
```powershell
python show_comprehensive_final_report.py
```

### Mark Failed Items for Retry
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute(\"UPDATE checkpoints SET status='pending' WHERE status='failed'\")
cur.execute(\"DELETE FROM checkpoints WHERE workload='batch-sharepoint'\")
conn.commit()
print('Items marked for retry')
conn.close()
"
```

### Run Migration Again
```powershell
python run_migration_debug.py
```

---

## 🛠️ Troubleshooting

### 1. See Real-Time Error Messages
```powershell
# Run with increased verbosity
$env:PYTHONUNBUFFERED=1
tenant-migrator --state sharepoint-pilot.sqlite batch --config .\sharepoint-pilot.yaml --report result.json
```

### 2. Database State Issues
```powershell
# Check current database status
python -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute(\"SELECT status, COUNT(*) FROM checkpoints WHERE workload='sharepoint' GROUP BY status\")
for row in cur.fetchall():
    print(f'{row[0]}: {row[1]}')
conn.close()
"
```

### 3. Reset Batch State (if migration hangs)
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute(\"DELETE FROM checkpoints WHERE workload='batch-sharepoint'\")
conn.commit()
print('Batch checkpoint cleared')
conn.close()
"
```

---

## 📁 Script Files Reference

| Script | Purpose | Usage |
|--------|---------|-------|
| `run_migration_debug.py` | Run migration with debug output | `python run_migration_debug.py` |
| `monitor_progress.py` | Real-time progress monitoring | `python monitor_progress.py` (in another terminal) |
| `generate_failed_report.py` | Create detailed failed items report | `python generate_failed_report.py` |
| `show_comprehensive_final_report.py` | Summary statistics | `python show_comprehensive_final_report.py` |

---

## 📈 Expected Flow

### Full Migration Run:
```
1. python run_migration_debug.py
   ↓
2. [Monitor in another terminal]
   python monitor_progress.py
   ↓
3. [After completion]
   python generate_failed_report.py
   ↓
4. [Review failed items and error types]
   ↓
5. [If needed, retry]
   python -c "..."  (mark pending)
   python run_migration_debug.py
```

---

## ✅ Success Indicators

- **Completed items increment**: `[+] Copied file: ...`
- **Progress % increasing**: `50% → 75% → 100%`
- **No hanging**: Progress updates every few seconds
- **Final report shows 0 pending items** when complete

---

## 💾 Output Files Generated

Each migration run creates:
```
sharepoint-result-YYYYMMDD-HHMMSS.json      ← Migration stats
migration-YYYYMMDD.log                      ← Full output log
failed-items-report-YYYYMMDD-HHMMSS.json   ← Detailed failures
```

---

## 🔐 Permission Migration Notes

- Automatically grants **edit access** to **everyone** on migrated items
- Runs as part of each file/folder copy
- If fails, item is retried (not marked permanent failure)
- Verify by checking target SharePoint: Right-click → Share → Everyone (edit)

---

## ⚡ Performance Tips

1. **Run on machine with fast internet** (migration bandwidth intensive)
2. **Keep both terminals open** during long migrations
3. **Don't close PowerShell** - use Ctrl+C to stop gracefully
4. **Check progress every 5 minutes** for long batches
5. **Timeout per item: 30 min** - some large files need full time

---

Last Updated: 2026-08-29
