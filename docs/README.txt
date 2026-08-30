================================================================================
SHAREPOINT MIGRATION - LOCAL DEBUG RUN SETUP COMPLETE
================================================================================

Welcome! Everything is ready for you to run the SharePoint migration locally
with full debug output and failed items reporting.

================================================================================
QUICK START - 3 STEPS TO RUN MIGRATION
================================================================================

Step 1: Open PowerShell

    cd c:\Users\Admin\Downloads\ms-migration
    . .\.venv\Scripts\Activate.ps1

Step 2: Start the interactive menu

    python master_control.py

Step 3: Select option from the menu

    1. Run Migration (Debug mode) - THIS IS WHAT YOU WANT
    2. Monitor Progress (Real-time) - Run in 2nd terminal
    3. Generate Failed Items Report - SEE WHAT FAILED WITH FILE PATHS
    4. Show Migration Status - Quick check
    5. Mark Failed Items for Retry
    6. Clear Batch State (if stuck)
    7. Open Debug Guide
    8. Exit

That's it! The menu handles everything.

================================================================================
WHAT EACH SCRIPT DOES - CHOOSE ONE
================================================================================

For Running Migration with Debug Output:
  python master_control.py
    ↳ Interactive menu - best for beginners
    ↳ Guides you through each step
    
  python run_migration_debug.py
    ↳ Direct run without menu
    ↳ Shows live progress with % completion
    ↳ Use if you just want to start immediately

For Monitoring Progress (While Migration Runs):
  python monitor_progress.py
    ↳ Run this in a SECOND PowerShell window
    ↳ Shows: [12:25:45] Progress: 125/3451 (3.6%)...
    ↳ Updates every 5 seconds
    ↳ Press Ctrl+C to stop

For Viewing Failed Items (After Migration):
  python generate_failed_report.py
    ↳ THIS IS WHAT YOU ASKED FOR!
    ↳ Shows failed items with file paths and error reasons
    ↳ Creates JSON file: failed-items-report-YYYYMMDD-HHMMSS.json
    ↳ Easy to download and share

For Quick Status Check:
  python show_comprehensive_final_report.py
    ↳ Shows: Completed | Pending | Failed | Success %
    ↳ Takes 2 seconds to display

For Help and References:
  python quick_reference.py
    ↳ Show all commands and what they do
    ↳ Configuration limits and settings
    ↳ Troubleshooting tips

Visual Guides (Read These):
  python SETUP_SUMMARY.py
    ↳ Summary of complete setup
    
  python VISUAL_STEP_BY_STEP.py
    ↳ Detailed walk-through with examples
    ↳ Shows what output looks like
    ↳ Scenarios: run, monitor, check failed, retry

Documentation Files:
  - HOW_TO_RUN_LOCALLY.md
  - DEBUG_RUN_GUIDE.md
  - SCRIPT_GUIDE.py

================================================================================
TYPICAL WORKFLOW
================================================================================

SCENARIO: Run migration, check progress, review failed items

1. Terminal 1 - Run migration:
   python master_control.py
   -> Select: 1. Run Migration (Debug mode)
   -> Wait for completion (2-4 hours for 3,500 items)

2. Terminal 2 (while migration runs) - Monitor progress:
   python monitor_progress.py
   -> Shows: Progress: 125/3451 (3.6%)...
   -> Updates every 5 seconds
   -> Press Ctrl+C to stop

3. After migration completes - Check failed items:
   python generate_failed_report.py
   -> Shows failed items (if any)
   -> Creates JSON file with details
   -> Shows file paths that failed

4. If failures exist - Review and retry:
   python master_control.py
   -> Select: 5. Mark Failed Items for Retry
   -> Select: 1. Run Migration again
   -> Go back to step 3

5. Final status check:
   python show_comprehensive_final_report.py
   -> Shows: 3,451 completed, 86 pending, 0 failed, 97.6% success

================================================================================
OUTPUT FILES GENERATED
================================================================================

When you run migration, these files are created:

  sharepoint-result-YYYYMMDD-HHMMSS.json
    = JSON file with migration statistics
    = Shows how many files copied, errors, etc.
    
  migration-LEVEL-retry.log
    = Text log of all output from migration
    = Detailed debugging information
    
  failed-items-report-YYYYMMDD-HHMMSS.json
    = JSON file with failed items + details
    = Includes file paths and error reasons
    = THIS IS WHAT YOU ASKED FOR
    = Easy to download and share

Location: c:\Users\Admin\Downloads\ms-migration\

================================================================================
DEBUG OUTPUT EXPLAINED
================================================================================

When running migration, you'll see output like:

  [DEBUG] Processing site: Harbour PD Work Flow
  [DEBUG] Source: harbouroutdoorasialimited.sharepoint.com
  
  Creating folder: HARBOUR 1
  >> HARBOUR 1-234567.psd: 25% (15.6 MB / 61.7 MB)
  >> HARBOUR 1-234567.psd: 50% (31.2 MB / 61.7 MB)
  >> HARBOUR 1-234567.psd: 100% (61.7 MB / 61.7 MB)
  [+] Copied file: HARBOUR 1-234567.psd (61.71 MB)

What it means:

  [DEBUG] ... = Configuration/setup information
  Creating folder: ... = Making new folder in target
  >> FILE ... = Currently uploading FILE
  25% (15.6 MB / 61.7 MB) = 25% done, 15.6 MB uploaded of 61.7 MB total
  [+] Copied file = Successfully completed
  [-] Error = Item failed (added to failed list)
  [SUCCESS] = Migration batch completed

================================================================================
CURRENT STATUS
================================================================================

From previous run:

  Completed: 3,451 items (97.6% success)
  Pending:   86 items (needs retry)
  Failed:    0 items (all permanent failures RESOLVED!)

The migration is very close to complete! Next run should resolve most of the
86 pending items.

================================================================================
FAILED ITEMS REPORT - WHAT YOU ASKED FOR
================================================================================

You requested: "there should have report to store which files/path failed
               to move"

We created: generate_failed_report.py

What it does:
  1. Displays failed items in console (easy to read)
  2. Creates JSON file with all details
  3. Shows file paths of items that failed
  4. Shows error type and error message for each

The JSON file is perfect for:
  - Downloading and sharing
  - Importing to Excel
  - Tracking which files need attention
  - Analyzing patterns (is it timeouts? permissions? etc.)

Example output:

  Total Failed: 5
  
  401 Unauthorized - Permission/Auth issue (2 items)
    Item ID: ...XRVEATG7OJC2FDJ3VEK5RQYO
    Error: 401 Client Error: Unauthorized
    
  Timeout - Too slow (3 items)
    Item ID: ...C3APV4ONNCY7QOOSVAMGTDN
    Error: HTTPConnectionPool timeout after 1800 seconds

Then creates: failed-items-report-20260829-122528.json

================================================================================
HELPFUL FEATURES
================================================================================

RESUMABLE MIGRATION
  - If migration stops, you can restart it
  - It picks up where it left off
  - SQLite database tracks completed items
  - Zero duplicates (same files not copied twice)

INTERACTIVE MENU
  - No complex commands needed
  - Just select numbers from the menu
  - Everything guided step by step

PROGRESS MONITORING
  - See real-time % completion
  - Know exactly how many items left
  - Elapsed time and speed

AUTOMATIC RETRY
  - Mark failed items for retry
  - Run migration again on failed items
  - Many temporary failures resolve on retry

ERROR CATEGORIZATION
  - Failed items grouped by error type
  - See which errors are temporary (retryable)
  - See which need manual investigation

================================================================================
COMMON QUESTIONS
================================================================================

Q: How long does the migration take?
A: About 2-4 hours for 3,500 items (depends on file sizes)

Q: Can I stop and restart?
A: Yes! State is saved in SQLite. Restart from where you left off.

Q: What if a file fails?
A: It's added to the failed list. Run generate_failed_report.py to see details.

Q: Can I retry failed items?
A: Yes! Use master_control.py option 5 to mark for retry, then run again.

Q: Will items be copied twice?
A: No! SQLite tracks completed items. Zero duplicates.

Q: How do I get the failed items list?
A: Run: python generate_failed_report.py
   Creates: failed-items-report-YYYYMMDD-HHMMSS.json

Q: Can I see file paths in the report?
A: Yes! JSON file includes all item details and paths.

Q: What if I want to see ONLY failed items?
A: Run: python generate_failed_report.py
   It shows ALL failed items with details.

Q: How do I track what failed?
A: Download the failed-items-report-*.json file. Perfect for tracking.

================================================================================
GETTING STARTED RIGHT NOW
================================================================================

1. Open PowerShell
2. Type: cd c:\Users\Admin\Downloads\ms-migration
3. Type: . .\.venv\Scripts\Activate.ps1
4. Type: python master_control.py
5. Select option 1 to run migration

That's it! You're on your way!

The menu will guide you through everything else.

================================================================================
NEXT STEPS
================================================================================

IMMEDIATE:
  [ ] Run migration: python master_control.py -> Option 1
  [ ] Monitor progress: python monitor_progress.py (in 2nd terminal)
  [ ] After done: python generate_failed_report.py

SHORT TERM:
  [ ] Review failed-items-report-*.json (if any failures)
  [ ] Retry failed items: master_control.py -> Option 5
  [ ] Continue until success rate > 99%

FINAL:
  [ ] Achieve 99%+ success rate
  [ ] Download failed-items-report-*.json as final proof
  [ ] Migration complete!

================================================================================
SUPPORT
================================================================================

If migration hangs:
  1. Press Ctrl+C to stop
  2. Run: python master_control.py
  3. Select option 6: Clear Batch State
  4. Select option 1: Run Migration again

For detailed troubleshooting:
  - Read: HOW_TO_RUN_LOCALLY.md
  - Read: DEBUG_RUN_GUIDE.md
  - Run: python quick_reference.py for quick tips

For step-by-step visual guide:
  - Run: python VISUAL_STEP_BY_STEP.py
  - Shows exactly what to expect at each step

================================================================================

You're all set! Good luck with the migration!

Start with: python master_control.py

================================================================================
