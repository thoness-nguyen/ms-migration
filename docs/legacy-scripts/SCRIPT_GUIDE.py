#!/usr/bin/env python3
"""
Interactive guide to show which script to run based on what you want to do
"""

def show_script_guide():
    guide = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    MIGRATION SCRIPTS - USAGE GUIDE                           ║
╚══════════════════════════════════════════════════════════════════════════════╝

┌──────────────────────────────────────────────────────────────────────────────┐
│ 🎯 WHAT DO YOU WANT TO DO?                                                   │
├──────────────────────────────────────────────────────────────────────────────┤

1️⃣  "I WANT TO RUN THE MIGRATION NOW"
    ├─ Option A (Easiest - Interactive Menu):
    │  $ python master_control.py
    │  → Then select: 1. Run Migration (Debug mode)
    │
    └─ Option B (Direct with Debug):
       $ python run_migration_debug.py


2️⃣  "I WANT TO SEE PROGRESS WHILE MIGRATION IS RUNNING"
    ├─ Open SECOND Terminal and run:
    │  $ python monitor_progress.py
    │
    └─ Shows: [12:25:45] Progress: 125/3451 (3.6%) | Completed: 125 | Pending: ...


3️⃣  "MIGRATION FINISHED - SHOW ME WHAT FAILED"
    ├─ View failed items with error reasons:
    │  $ python generate_failed_report.py
    │
    ├─ Also creates JSON file:
    │  failed-items-report-20260829-122500.json ← Download & review this!
    │
    └─ Shows error categories:
       • 401 Unauthorized
       • 403 Forbidden
       • Timeout errors
       • Encoding issues
       • etc.


4️⃣  "SHOW ME CURRENT STATUS (How many completed?)"
    ├─ Quick status:
    │  $ python show_comprehensive_final_report.py
    │
    └─ Shows:
       Completed: 3,451 (97.6%)
       Failed: 0
       Pending: 86


5️⃣  "MARK FAILED ITEMS FOR RETRY"
    ├─ Option A (Interactive):
    │  $ python master_control.py
    │  → Then select: 5. Mark Failed Items for Retry
    │
    └─ Option B (Direct):
       $ python mark_401_for_retry.py  (Or create new one)


6️⃣  "MIGRATION IS STUCK - HELP!"
    ├─ Press Ctrl+C to stop migration
    │
    ├─ Clear batch checkpoint:
    │  $ python master_control.py
    │  → Then select: 6. Clear Batch State
    │
    └─ Restart:
       $ python run_migration_debug.py


7️⃣  "SHOW ME A QUICK REFERENCE CARD"
    ├─ Display commands at a glance:
    │  $ python quick_reference.py
    │
    └─ Shows all common commands


8️⃣  "SHOW ME THE FULL DEBUG GUIDE"
    ├─ Read comprehensive guide:
    │  $ notepad DEBUG_RUN_GUIDE.md
    │
    └─ OR: Read local setup guide:
       $ notepad HOW_TO_RUN_LOCALLY.md

└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ 📋 SCRIPT REFERENCE TABLE                                                    │
├──────────────────────────────────────────────────────────────────────────────┤

┌─────────────────────────────────────┬──────────────────────────────────────┐
│ SCRIPT                              │ PURPOSE                              │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ master_control.py                   │ 🎯 Interactive menu for everything  │
│                                     │ (EASIEST - START HERE!)             │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ run_migration_debug.py              │ ▶️  Run migration with debug output  │
│                                     │ (Live progress, file sizes shown)   │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ monitor_progress.py                 │ 📊 Live progress % (run in 2nd term) │
│                                     │ Updates every 5 seconds             │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ generate_failed_report.py           │ 📋 **Show all failed items + paths** │
│                                     │ Creates JSON with error categories  │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ show_comprehensive_final_report.py  │ 📈 Summary stats (completed/pending) │
│                                     │ Quick status check                  │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ quick_reference.py                  │ 📖 Quick command reference card      │
│                                     │ All common commands at a glance     │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ DEBUG_RUN_GUIDE.md                  │ 📚 Full troubleshooting guide (read) │
│                                     │ notepad DEBUG_RUN_GUIDE.md          │
├─────────────────────────────────────┼──────────────────────────────────────┤
│ HOW_TO_RUN_LOCALLY.md              │ 📚 Step-by-step setup & usage (read) │
│                                     │ notepad HOW_TO_RUN_LOCALLY.md       │
└─────────────────────────────────────┴──────────────────────────────────────┘

└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ 🚀 RECOMMENDED WORKFLOW                                                      │
├──────────────────────────────────────────────────────────────────────────────┤

Step 1: START HERE - Interactive menu
   $ python master_control.py
        ↓
Step 2: Select option 1 (Run Migration)
        ↓
Step 3: Migration starts... Open 2nd terminal while waiting
   $ python monitor_progress.py
        ↓
Step 4: Migration completes (check 1st terminal)
        ↓
Step 5: Generate report of failed items
   $ python generate_failed_report.py
        ↓
Step 6: Review JSON file (contains file paths & error reasons)
   open "failed-items-report-*.json"
        ↓
Step 7: If items failed, retry
   $ python master_control.py
   → Select option 5 (Mark for retry)
   → Select option 1 (Run migration again)

└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ 📂 COMPLETE FILE LIST                                                        │
├──────────────────────────────────────────────────────────────────────────────┤

SCRIPTS TO RUN (Python):
  ✓ master_control.py                  (Interactive menu - START HERE)
  ✓ run_migration_debug.py             (Run migration directly)
  ✓ monitor_progress.py                (Monitor in 2nd terminal)
  ✓ generate_failed_report.py          (Show failed items + paths)
  ✓ show_comprehensive_final_report.py (Quick status check)
  ✓ quick_reference.py                 (Display quick guide)

DOCUMENTATION (Text files to read):
  ✓ HOW_TO_RUN_LOCALLY.md             (Main guide - READ THIS FIRST)
  ✓ DEBUG_RUN_GUIDE.md                (Troubleshooting guide)

CONFIGURATION:
  ✓ sharepoint-pilot.yaml              (Migration config - DO NOT EDIT)
  ✓ sharepoint-pilot.sqlite            (State database - AUTO MANAGED)

└──────────────────────────────────────────────────────────────────────────────┘

╔══════════════════════════════════════════════════════════════════════════════╗
║                         QUICK START NOW                                      ║
║                                                                              ║
║  1. Open PowerShell                                                          ║
║  2. cd c:\\Users\\Admin\\Downloads\\ms-migration                            ║
║  3. . .\\.venv\\Scripts\\Activate.ps1                                        ║
║  4. python master_control.py                                                 ║
║  5. Select option 1 to run migration                                        ║
║  6. Keep running and selecting options as needed                            ║
║                                                                              ║
║  Current Status: 97.6% complete (3,451/3,537 items)                         ║
║  Ready to continue with remaining 86 items                                  ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

"""
    print(guide)

if __name__ == "__main__":
    show_script_guide()
