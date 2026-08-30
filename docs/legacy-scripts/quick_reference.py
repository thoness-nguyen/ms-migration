#!/usr/bin/env python3
"""
Display quick reference for migration commands
"""

def show_quick_reference():
    """Show quick reference card"""
    
    ref = r"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                  SHAREPOINT MIGRATION - QUICK REFERENCE                        ║
╚════════════════════════════════════════════════════════════════════════════════╝

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 🚀 QUICK START - 3 Steps                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Step 1: Activate environment                                                  │
│  $ . .\.venv\Scripts\Activate.ps1                                              │
│                                                                                 │
│  Step 2: Run master control (Interactive menu)                                 │
│  $ python master_control.py                                                    │
│                                                                                 │
│  Step 3: OR run directly with debug                                            │
│  $ python run_migration_debug.py                                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 🎯 MOST USED COMMANDS                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Run migration with debug:                                                     │
│  $ python run_migration_debug.py                                               │
│                                                                                 │
│  Monitor progress (run in 2nd terminal):                                       │
│  $ python monitor_progress.py                                                  │
│                                                                                 │
│  See what failed:                                                              │
│  $ python generate_failed_report.py                                            │
│                                                                                 │
│  Check status:                                                                 │
│  $ python show_comprehensive_final_report.py                                   │
│                                                                                 │
│  Retry failed items:                                                           │
│  $ python mark_401_for_retry.py    (or master_control.py option 5)            │
│  $ python run_migration_debug.py                                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 📊 CONFIGURATION & LIMITS                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Max File Size:        5 GB                                                    │
│  Timeout per item:     30 minutes (1800 seconds)                               │
│  Max retry attempts:   3                                                       │
│  Chunk size:           10 MB                                                   │
│  Permission level:     Edit (everyone)                                         │
│  Database:             sharepoint-pilot.sqlite                                 │
│  Config file:          sharepoint-pilot.yaml                                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 📋 OUTPUT FILES GENERATED                                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Migration result JSON:    sharepoint-result-YYYYMMDD-HHMMSS.json             │
│  Failed items report:      failed-items-report-YYYYMMDD-HHMMSS.json          │
│  Migration log:            migration-401-retry.log                             │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 🔍 UNDERSTANDING DEBUG OUTPUT                                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  >> FILE: 25% (15.6 MB / 61.7 MB)     ← Currently uploading chunks             │
│  [+] Copied file: FILE (61.7 MB)      ← Successfully completed                 │
│  [!] Warning message                  ← Non-fatal issue                        │
│  [-] Error detail                     ← Failed item                            │
│                                                                                 │
│  PYTHONUNBUFFERED=1 = Live output display (no buffering)                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ 🆘 IF MIGRATION HANGS                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. Press Ctrl+C to stop current run                                           │
│  2. Clear batch checkpoint:                                                    │
│     $ python master_control.py → Option 6                                      │
│  3. Restart migration:                                                         │
│     $ python run_migration_debug.py                                            │
│                                                                                 │
│  Note: State is preserved in SQLite - nothing is lost!                         │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ ✅ SUCCESS INDICATORS                                                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ✓ Live progress updates every 5-10 seconds                                    │
│  ✓ File copy percentage increases: 0% → 50% → 100%                            │
│  ✓ [+] Copied file messages appear                                            │
│  ✓ No "Connection timeout" errors                                             │
│  ✓ Success rate > 95% in final report                                         │
│  ✓ Failed items report shows < 5% items                                       │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

📖 For full guide: Read DEBUG_RUN_GUIDE.md
💾 Logs directory: c:\Users\Admin\Downloads\ms-migration\
🔗 Current success rate: 97.6% (3,451 of 3,537 items)

═══════════════════════════════════════════════════════════════════════════════════

"""
    print(ref)

if __name__ == "__main__":
    show_quick_reference()
