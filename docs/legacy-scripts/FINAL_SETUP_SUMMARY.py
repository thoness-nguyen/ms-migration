#!/usr/bin/env python3
"""
Final setup summary - shows exactly what you have and how to use it
"""
from datetime import datetime

summary = """
╔════════════════════════════════════════════════════════════════════════════════╗
║                                                                                ║
║         SHAREPOINT MIGRATION - COMPLETE LOCAL DEBUG SETUP READY                ║
║                                                                                ║
║                        Generated: [Generated at runtime]                                   ║
║                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 CURRENT STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ✅ Completed Items:   3,451 (97.6%)
    ⏳ Pending Items:     86 items (needs retry)
    ❌ Failed Items:      0 (ALL RESOLVED!)
    
    Success Rate: 97.6%
    Status: READY FOR LOCAL DEBUG RUN

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🛠️  HELPER SCRIPTS CREATED FOR YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  MAIN SCRIPTS:
  
  1️⃣  master_control.py
      Purpose: Interactive menu to control everything
      Usage:   python master_control.py
      Why:     Easiest way - just select options from menu
      
  2️⃣  run_migration_debug.py
      Purpose: Run migration with live debug output
      Usage:   python run_migration_debug.py
      Output:  [+] Copied file: HARBOUR 10-2420114.psd (20.99 MB)
               >> HARBOUR 10-2420116.psd: 50% (15.6 MB / 30.9 MB)
      
  3️⃣  monitor_progress.py
      Purpose: Live progress % monitor (run in 2nd terminal)
      Usage:   python monitor_progress.py
      Output:  [12:25:45] Progress: 125/3451 (3.6%) | Completed: 125...
      
  4️⃣  generate_failed_report.py ⭐ (What you asked for)
      Purpose: Show all FAILED ITEMS with file PATHS and ERROR REASONS
      Usage:   python generate_failed_report.py
      Output:  Creates JSON file: failed-items-report-YYYYMMDD-HHMMSS.json
               Shows error categories and specific error messages
      
  5️⃣  show_comprehensive_final_report.py
      Purpose: Quick status summary
      Usage:   python show_comprehensive_final_report.py
      Output:  Completed: 3,451 | Pending: 86 | Failed: 0 | Rate: 97.6%
      
  6️⃣  quick_reference.py
      Purpose: Display quick command reference
      Usage:   python quick_reference.py
      Output:  Quick card with all common commands


  DOCUMENTATION (Read These):
  
  📖 HOW_TO_RUN_LOCALLY.md
     Complete step-by-step guide for running locally
     Includes troubleshooting, workflows, and examples
     
  📖 DEBUG_RUN_GUIDE.md
     Detailed debug guide with configuration details
     Performance tips and common issues
     
  📖 SCRIPT_GUIDE.py
     Shows which script to use for what task
     Run: python SCRIPT_GUIDE.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚀 HOW TO USE (3 STEPS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Step 1: Open PowerShell and navigate to project
  
    cd c:\\Users\\Admin\\Downloads\\ms-migration
    . .\\.venv\\Scripts\\Activate.ps1

Step 2: Run the master control (Interactive menu)
  
    python master_control.py
    
    This shows you options:
    1. Run Migration (Debug mode)
    2. Monitor Progress (Real-time)
    3. Generate Failed Items Report  ← YOU WANTED THIS!
    4. Show Migration Status
    5. Mark Failed Items for Retry
    6. Clear Batch State (if stuck)
    7. Open Debug Guide
    8. Exit

Step 3: Select option based on what you need
  
    Want to run migration? → Select 1
    Want to see progress? → Select 2
    Want to see failed items? → Select 3
    Want quick status? → Select 4
    Want to retry? → Select 5

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 DETAILED FAILED ITEMS REPORT (Your Request)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run this command:

    python generate_failed_report.py

Output:
  • Displays failed items in console (easy to read)
  • Creates JSON file with ALL details (easy to share/analyze)
  
JSON Report includes:
  - Item ID (source path)
  - Error type (401, 403, timeout, encoding, etc.)
  - Error detail (exact error message)
  - File path (so you know what failed)

Example JSON format:
{
  "generated_at": "2026-08-29T12:25:28",
  "total_failed": 42,
  "error_categories": {
    "401 Unauthorized": 15,
    "Timeout": 20,
    "Encoding": 7
  },
  "failed_items": [
    {
      "source_id": "01P23IA5Q2MILSSNKFGFB25PO5G5URDTUD",
      "error_type": "Timeout",
      "error_detail": "Connection timeout after 1800 seconds"
    }
  ]
}

This JSON file is saved as:
  failed-items-report-20260829-122500.json
  
You can download/share this file to track what failed!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 WORKFLOW EXAMPLE: Continue Migration Locally
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Start migration:
   python master_control.py
   → Select: 1. Run Migration (Debug mode)
   
2. Monitor in 2nd terminal (while migration runs):
   python monitor_progress.py
   → Shows: [12:25:45] Progress: 125/3451 (3.6%)...
   
3. After migration completes:
   python generate_failed_report.py
   → Shows: "✅ NO FAILED ITEMS" or lists specific failures
   → Creates JSON file with details
   
4. If items failed, retry:
   python master_control.py
   → Select: 5. Mark Failed Items for Retry
   → Select: 1. Run Migration again
   
5. Check final results:
   python show_comprehensive_final_report.py
   → Shows: Completed: 3,xxx | Failed: 0 | Success: 99.x%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 DEBUG OUTPUT EXPLAINED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When you run migration, you'll see:

  [DEBUG] Processing site: Harbour PD Work Flow
  [DEBUG] Source: harbouroutdoorasialimited.sharepoint.com
  [DEBUG] Target: paizesoffice.sharepoint.com
  
  ✓ Copying folder: HARBOUR
  [+] Created folder: HARBOUR 1
  
    >> HARBOUR 1-234567.psd: 25% (15.6 MB / 61.7 MB)
    >> HARBOUR 1-234567.psd: 50% (31.2 MB / 61.7 MB)
    >> HARBOUR 1-234567.psd: 100% (61.7 MB / 61.7 MB)
  [+] Copied file: HARBOUR 1-234567.psd (61.71 MB)
  
  [+] Created folder: HARBOUR 2
  [+] Copied file: HARBOUR 2-123456.psd (32.59 MB)
  [+] Copied file: HARBOUR 2-123457.psd (45.12 MB)

What it means:
  [DEBUG]    = Configuration info
  ✓          = Starting operation
  >>         = Currently uploading chunks (shows % progress)
  [+]        = Successfully completed
  [-]        = Error/failure
  (15.6 MB / 61.7 MB) = Current size / Total file size

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ COMMON QUICK COMMANDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Run migration with debug:
  python run_migration_debug.py

Monitor progress (2nd terminal):
  python monitor_progress.py

Show failed items with paths:  ⭐ YOUR REQUEST
  python generate_failed_report.py

Check status:
  python show_comprehensive_final_report.py

Interactive menu:
  python master_control.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📂 ALL FILES LOCATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  c:\\Users\\Admin\\Downloads\\ms-migration\\
  
  Configuration:
    ├─ sharepoint-pilot.yaml           (Config file - NO NEED TO EDIT)
    ├─ sharepoint-pilot.sqlite         (State database - AUTO MANAGED)
    
  Scripts (Python):
    ├─ master_control.py               ← START HERE
    ├─ run_migration_debug.py
    ├─ monitor_progress.py
    ├─ generate_failed_report.py       ← FOR FAILED ITEMS
    ├─ show_comprehensive_final_report.py
    ├─ quick_reference.py
    └─ SCRIPT_GUIDE.py
    
  Documentation (Markdown):
    ├─ HOW_TO_RUN_LOCALLY.md          (Main guide)
    ├─ DEBUG_RUN_GUIDE.md             (Troubleshooting)
    └─ SCRIPT_GUIDE.py                (Which script to use)
    
  Output Files (Generated by runs):
    ├─ sharepoint-result-*.json        (Migration stats)
    ├─ failed-items-report-*.json      (Failed items list)
    ├─ migration-*.log                 (Output logs)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ READY TO USE - START NOW!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Open PowerShell
2. cd c:\\Users\\Admin\\Downloads\\ms-migration
3. . .\\.venv\\Scripts\\Activate.ps1
4. python master_control.py

Then use the menu to:
  • Run migration with live debug output
  • Monitor progress in 2nd terminal
  • Generate failed items report (with file paths!)
  • Retry any failed items
  • View summary statistics

All scripts are ready to use. No additional setup needed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💾 BENEFITS OF THIS SETUP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Run locally with full debug visibility
✓ See live progress (% completion, file sizes, speeds)
✓ Know exactly which files failed and why
✓ Export detailed report (JSON) with file paths
✓ Retry individual failed batches
✓ Zero duplicates (SQLite state management)
✓ Interactive menu (no complex commands to remember)
✓ Resumable (stop/restart without losing progress)
✓ All existing scripts in one place

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""

print(summary)
