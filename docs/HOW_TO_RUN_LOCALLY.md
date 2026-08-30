# 📖 HOW TO RUN SHAREPOINT MIGRATION LOCALLY WITH DEBUG MODE

## ✅ Current Status
- **3,451 items completed** ✓
- **86 items pending** (last batch, likely resolvable)
- **0 items failed permanently**
- **Success rate: 97.6%**

---

## 🚀 QUICK START (Recommended)

### **Option 1: Interactive Menu (Easiest)**

```powershell
cd c:\Users\Admin\Downloads\ms-migration
. .\.venv\Scripts\Activate.ps1
python master_control.py
```

Then select options from menu:
```
1. Run Migration (Debug mode)
2. Monitor Progress (Real-time)
3. Generate Failed Items Report
4. Show Migration Status
5. Mark Failed Items for Retry
6. Clear Batch State
7. Open Debug Guide
8. Exit
```

### **Option 2: Direct Commands**

```powershell
# Step 1: Activate environment
cd c:\Users\Admin\Downloads\ms-migration
. .\.venv\Scripts\Activate.ps1

# Step 2: Run migration with debug output
python run_migration_debug.py
```

---

## 🔍 UNDERSTANDING THE DEBUG OUTPUT

### What You'll See:

```
════════════════════════════════════════════════════════════════
SHAREPOINT MIGRATION - LOCAL DEBUG RUN
Started: 2026-08-29 12:25:00
════════════════════════════════════════════════════════════════

📋 COMMAND:
  .venv\Scripts\python.exe -u -m tenant_migrator.cli batch ...

⚙️  CONFIGURATION:
  • File size limit: 5 GB
  • Timeout: 30 minutes per attempt
  • Max retries: 3 with exponential backoff
  • Permission migration: Enabled (everyone can edit)

🔄 STARTING MIGRATION...
─────────────────────────────────────────────────────────────────

[DEBUG] Processing site: Harbour PD Work Flow
[DEBUG] Source site ID: harbouroutdoorasialimited.sharepoint.com...
[DEBUG] Target site ID: paizesoffice.sharepoint.com...

  ✓ Copying folder: HARBOUR
  [+] Created folder: HARBOUR 1
    >> HARBOUR 1-234567.psd: 25% (15.6 MB / 61.7 MB)
    >> HARBOUR 1-234567.psd: 50% (31.2 MB / 61.7 MB)
    >> HARBOUR 1-234567.psd: 75% (46.9 MB / 61.7 MB)
    >> HARBOUR 1-234567.psd: 100% (61.7 MB / 61.7 MB)
  [+] Copied file: HARBOUR 1-234567.psd (61.71 MB)

[+] Created folder: HARBOUR 2
  [+] Copied file: HARBOUR 2-123456.psd (32.59 MB)
  [+] Copied file: HARBOUR 2-123457.psd (45.12 MB)
  [+] Copied file: HARBOUR 2-123458.psd (28.74 MB)

[PROGRESS] 125/3451 items completed (3.6%) - Elapsed: 345s
```

### **Key Indicators:**

| Output | Meaning |
|--------|---------|
| `>>` | Currently uploading chunks |
| `[+]` | Successfully completed |
| `[!]` | Warning (non-fatal) |
| `[-]` | Error (item failed) |
| `25% (15.6 MB / 61.7 MB)` | Upload progress percentage |
| `[DEBUG]` | Configuration/diagnostic info |

---

## 📊 MONITORING PROGRESS (In Separate Terminal)

While migration is running, open **another PowerShell** and:

```powershell
cd c:\Users\Admin\Downloads\ms-migration
. .\.venv\Scripts\Activate.ps1
python monitor_progress.py
```

Output:
```
[12:25:45] Progress: 125/3451 (3.6%) | Completed: 125 | Pending: 3326 | 
Failed: 0 | Elapsed: 345s
```

Press `Ctrl+C` to stop monitoring (migration continues).

---

## 📋 GENERATE FAILED ITEMS REPORT

After migration completes, run:

```powershell
python generate_failed_report.py
```

**Output shows:**
- Total failed count
- Error categories (401, 403, timeout, encoding, etc.)
- Item IDs with specific errors
- **Exported JSON file** with all details

Example JSON report (`failed-items-report-20260829-122500.json`):
```json
{
  "generated_at": "2026-08-29T12:25:28.123456",
  "total_failed": 42,
  "error_categories": {
    "401 Unauthorized - Permission/Auth issue": 15,
    "Timeout - Connection/operation too slow": 20,
    "Encoding - Invalid filename characters": 7
  },
  "failed_items": [
    {
      "source_id": "01P23IA5Q2MILSSNKFGFB25PO5G5URDTUD",
      "error_type": "Timeout - Connection/operation too slow",
      "error_detail": "HTTPConnectionPool(host='graph.microsoft.com', port=443): 
                       Read timed out. (read timeout=1800)"
    },
    {
      "source_id": "01P23IA5Q5ZPJUZO7JHZDJBQWALPLB4DXH",
      "error_type": "Encoding - Invalid filename characters",
      "error_detail": "'charmap' codec can't encode characters in position 22-23: 
                       character maps to <undefined>"
    }
  ]
}
```

---

## 🔄 RETRY FAILED ITEMS

### Step 1: Check Status
```powershell
python show_comprehensive_final_report.py
```

### Step 2: Mark Failed Items
```powershell
python master_control.py
# Select option 5: Mark Failed Items for Retry
```

OR directly:
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute(\"UPDATE checkpoints SET status='pending' WHERE status='failed'\")
cur.execute(\"DELETE FROM checkpoints WHERE workload='batch-sharepoint'\")
conn.commit()
print(f'Items marked for retry')
conn.close()
"
```

### Step 3: Run Migration Again
```powershell
python run_migration_debug.py
```

---

## 🛠️ TROUBLESHOOTING

### Issue: Migration Hangs
```powershell
# 1. Press Ctrl+C to stop
# 2. Clear batch state
python master_control.py
# Select option 6: Clear Batch State (reset if stuck)

# 3. Restart
python run_migration_debug.py
```

### Issue: See More Detailed Errors
```powershell
# Run with maximum verbosity
$env:PYTHONUNBUFFERED=1
tenant-migrator --state sharepoint-pilot.sqlite batch `
  --config .\sharepoint-pilot.yaml `
  --report result.json `
  --verbose
```

### Issue: Check Database State
```powershell
python -c "
import sqlite3
conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()
cur.execute(\"SELECT status, COUNT(*) FROM checkpoints WHERE workload='sharepoint' GROUP BY status\")
for status, count in cur.fetchall():
    print(f'{status}: {count}')
conn.close()
"
```

---

## 📁 SCRIPT FILES YOU HAVE

| File | Purpose | Command |
|------|---------|---------|
| `master_control.py` | **Interactive menu** | `python master_control.py` |
| `run_migration_debug.py` | Run migration with debug | `python run_migration_debug.py` |
| `monitor_progress.py` | Live progress monitor | `python monitor_progress.py` |
| `generate_failed_report.py` | **Generate failed items report** | `python generate_failed_report.py` |
| `show_comprehensive_final_report.py` | Status summary | `python show_comprehensive_final_report.py` |
| `quick_reference.py` | Display this guide | `python quick_reference.py` |

---

## ⚡ TYPICAL WORKFLOW

### Run 1: Initial Migration
```powershell
python run_migration_debug.py
```
Monitor in another terminal:
```powershell
python monitor_progress.py
```

### After Migration Completes:
```powershell
# See what failed
python generate_failed_report.py

# Review report files
notepad failed-items-report-*.json
```

### Run 2: Retry Failed Items
```powershell
python master_control.py
# Option 5: Mark Failed Items for Retry
python run_migration_debug.py
```

### Final Report:
```powershell
python show_comprehensive_final_report.py
```

---

## 💾 OUTPUT FILES GENERATED

Each migration run creates:
```
sharepoint-result-20260829-122500.json       ← Migration stats
migration-401-retry.log                      ← Full output log
failed-items-report-20260829-122500.json    ← Detailed failures + paths
migration.log                                ← Console output log
```

### Logs Location:
```
c:\Users\Admin\Downloads\ms-migration\
├── sharepoint-pilot.sqlite          (State database)
├── sharepoint-pilot.yaml            (Configuration)
├── sharepoint-result-*.json         (Results from runs)
├── failed-items-report-*.json       (Failed items with paths)
└── *.log                            (Output logs)
```

---

## ✅ SUCCESS CHECKLIST

- [ ] Virtual environment activated
- [ ] `python run_migration_debug.py` runs without errors
- [ ] Live progress updates every 5-10 seconds
- [ ] Files show `[+] Copied file:` messages
- [ ] No `[FAIL]` errors appearing
- [ ] `python monitor_progress.py` shows increasing %
- [ ] Migration completes with exit code 0
- [ ] `python generate_failed_report.py` shows 0 failed items OR <5%
- [ ] JSON report files generated successfully

---

## 🎯 NEXT STEPS FOR YOU

### Immediate:
1. **Try the interactive menu:**
   ```powershell
   python master_control.py
   ```

2. **If you need to debug:**
   - Run: `python run_migration_debug.py`
   - Monitor in 2nd terminal: `python monitor_progress.py`
   - After done: `python generate_failed_report.py`

3. **To retry 86 pending items:**
   - Use menu option 5 to mark as pending
   - Run migration again
   - Check report

### For Production:
- Keep migration logs for audit trail
- Store JSON reports in backup location
- Monitor progress in 2nd terminal during long runs
- Check failed report after each run

---

## 📞 COMMON QUESTIONS

**Q: How long does migration take?**
A: ~2-4 hours for 3,451 items (depends on file sizes and network)

**Q: Can I stop and resume?**
A: Yes! State is saved in SQLite. Stop (Ctrl+C) and restart anytime.

**Q: Will items be duplicated if I retry?**
A: No! SQLite prevents duplicates.

**Q: How do I export failed items list?**
A: Run `python generate_failed_report.py` - creates JSON file with all failures + paths

**Q: Can I change file size limit?**
A: Yes, edit `sharepoint-pilot.yaml` or modify Python scripts (max_file_size_mb=5000)

**Q: What does "pending" mean?**
A: Item not yet processed or needs retry. Will be attempted in next migration run.

---

**Last Updated:** 2026-08-29  
**Current Success Rate:** 97.6%  
**Status:** Ready for local debug runs

