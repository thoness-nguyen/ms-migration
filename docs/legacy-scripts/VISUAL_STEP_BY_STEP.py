#!/usr/bin/env python3
r"""
STEP-BY-STEP VISUAL GUIDE
Visual walkthrough showing exactly what to do
"""

guide = r"""
================================================================================
STEP-BY-STEP VISUAL GUIDE - RUN SHAREPOINT MIGRATION LOCALLY WITH DEBUG
================================================================================

SCENARIO: You want to continue the migration locally with full debug output
          and see which files/paths failed (with error reasons)

================================================================================
STEP 1: Open PowerShell and Navigate to Project
================================================================================

1. Click Windows Start button
2. Type "PowerShell" and press Enter
3. You should see: PS C:\Users\Admin>

Type these commands:

    cd c:\Users\Admin\Downloads\ms-migration
    . .\.venv\Scripts\Activate.ps1

You should see the prompt change to indicate virtual environment is active.

Expected output:
    PS C:\Users\Admin\Downloads\ms-migration>

================================================================================
STEP 2: Start the Interactive Menu
================================================================================

Type:
    python master_control.py

You will see a menu like this:

    ================================================================================
    SHAREPOINT MIGRATION - MASTER CONTROL
    ================================================================================

      1. Run Migration (Debug mode)
      2. Monitor Progress (Real-time)
      3. Generate Failed Items Report
      4. Show Migration Status
      5. Mark Failed Items for Retry
      6. Clear Batch State (reset if stuck)
      7. Open Debug Guide
      8. Exit

    Enter choice (1-8): _

================================================================================
SCENARIO A: YOU WANT TO RUN MIGRATION
================================================================================

1. See the menu above
2. Type: 1
3. Press Enter
4. Migration starts with debug output:

    [DEBUG] Processing site: Harbour PD Work Flow
    [DEBUG] Source site ID: harbouroutdoorasialimited.sharepoint.com...
    [DEBUG] Target site ID: paizesoffice.sharepoint.com...
    
    Created folder: HARBOUR 1
      >> HARBOUR 1-234567.psd: 25% (15.6 MB / 61.7 MB)
      >> HARBOUR 1-234567.psd: 50% (31.2 MB / 61.7 MB)
      >> HARBOUR 1-234567.psd: 100% (61.7 MB / 61.7 MB)
    [+] Copied file: HARBOUR 1-234567.psd (61.71 MB)
    
    [+] Copied file: HARBOUR 2-123456.psd (32.59 MB)
    [+] Copied file: HARBOUR 2-123457.psd (45.12 MB)

5. Migration runs... Files are copied one by one
6. When complete, you see: [SUCCESS] Migration completed
7. Menu reappears

What it means:
  [DEBUG]              = Configuration info
  Created folder       = Creating new folder
  >>                   = Currently uploading (shows % progress)
  (15.6 MB / 61.7 MB) = Current uploaded / Total file size
  [+] Copied file      = Successfully completed
  [SUCCESS]            = Migration done

================================================================================
SCENARIO B: YOU WANT TO MONITOR PROGRESS (While Migration is Running)
================================================================================

IMPORTANT: Keep FIRST PowerShell window running migration (from Step 2)
           Open a SECOND PowerShell window for monitoring

In new PowerShell window:

1. cd c:\Users\Admin\Downloads\ms-migration
2. . .\.venv\Scripts\Activate.ps1
3. python monitor_progress.py

You will see:

    [12:25:45] Progress: 125/3451 (3.6%) | Completed: 125 | Pending: 3326 | 
    Failed: 0 | Elapsed: 345s
    
    [12:25:50] Progress: 127/3451 (3.7%) | Completed: 127 | Pending: 3324 | 
    Failed: 0 | Elapsed: 350s
    
    [12:25:55] Progress: 129/3451 (3.7%) | Completed: 129 | Pending: 3322 | 
    Failed: 0 | Elapsed: 355s

This updates every 5 seconds showing:
  - Time
  - Items completed / Total items
  - % completion
  - Number of completed, pending, failed items
  - Elapsed time

Press Ctrl+C to stop monitoring (migration continues in 1st window)

================================================================================
SCENARIO C: YOU WANT TO SEE WHAT FAILED (After Migration)
================================================================================

THIS IS WHAT YOU ASKED FOR!

In PowerShell:

1. python master_control.py
2. Type: 3
3. Press Enter

Output will show (like this):

    ====================================================================================================
    SHAREPOINT MIGRATION - FAILED ITEMS DETAILED REPORT
    Generated: 2026-08-29 12:25:28
    ====================================================================================================

    NO FAILED ITEMS - MIGRATION COMPLETE!

OR if there are failures:

    ====================================================================================================
    SHAREPOINT MIGRATION - FAILED ITEMS DETAILED REPORT
    Generated: 2026-08-29 12:25:28
    ====================================================================================================

    FAILED ITEMS SUMMARY
    ────────────────────────────────────────────────────────────────────────

    Total Failed: 42

    401 Unauthorized - Permission/Auth issue
       Count: 15 items

       Item ID: ...3IA5Q2XRVEATG7OJC2FDJ3VEK5RQYO
       Error: 401 Client Error: Unauthorized for url: https://graph.microsoft.com/v1...

       Item ID: ...3IA5QIG6J56WO5MRALHIAVF5OGGWTA
       Error: 401 Client Error: Unauthorized for url: https://graph.microsoft.com/v1...


    Timeout - Connection/operation too slow
       Count: 20 items

       Item ID: ...3IA5QKKC3APV4ONNCY7QOOSVAMGTDN
       Error: HTTPConnectionPool timeout after 1800 seconds...


    Encoding - Invalid filename characters
       Count: 7 items

       Item ID: ...3IA5QPPP3APV4ONNCY7QOOSVAMGTDN
       Error: 'charmap' codec can't encode characters...

    ────────────────────────────────────────────────────────────────────────

    Report exported to: failed-items-report-20260829-122528.json

This creates a JSON file you can download/share:

    failed-items-report-20260829-122528.json

This JSON file contains ALL failed items with:
  - Item ID
  - Error type
  - Error detail
  - File path information

================================================================================
SCENARIO D: YOU WANT TO CHECK CURRENT STATUS
================================================================================

In PowerShell:

1. python master_control.py
2. Type: 4
3. Press Enter

Output shows:

    ================================================================================
    CURRENT MIGRATION STATUS
    ================================================================================

      Completed: 3,451 items
      Pending:   86 items (needs retry)
      Failed:    0 items
      ────────────────────────
      Total:     3,537 items
      Success:   97.6%

      Info: 86 items pending - Run option 1 to continue

    ================================================================================

This shows you exactly where you are:
  - How many completed
  - How many need retry
  - How many permanently failed
  - Overall success percentage

================================================================================
SCENARIO E: YOU WANT TO RETRY FAILED ITEMS
================================================================================

If migration left some items as failed/pending:

1. python master_control.py
2. Type: 5
3. Press Enter

You see:

    Warning: Clear batch checkpoint? (yes/no): yes
    
    Items marked as pending
    Info: Run option 1 to retry these items

4. Type: 1
5. Press Enter

Migration runs again on those failed items. Many will succeed now!

================================================================================
TROUBLESHOOTING: What to do if Migration Hangs
================================================================================

If migration seems stuck (no output for 5+ minutes):

1. Press Ctrl+C in PowerShell to stop it
2. Run: python master_control.py
3. Type: 6
4. Type: yes to clear batch state
5. Run: python master_control.py
6. Type: 1 to restart migration

The state is saved in SQLite - nothing is lost!

================================================================================
COMPLETE WORKFLOW SUMMARY
================================================================================

START HERE:
  1. python master_control.py

THEN FOLLOW ONE OF THESE PATHS:

Path A (Just run migration):
  -> Select 1: Run Migration
  -> Wait for completion
  -> Select 3: View failed items
  
Path B (Monitor progress):
  -> Select 1: Run Migration (in 1st PowerShell window)
  -> Open 2nd PowerShell window
  -> Run: python monitor_progress.py
  -> Watch progress updates every 5 seconds

Path C (Check failed items):
  -> Select 3: Generate Failed Items Report
  -> See what failed + error reasons
  -> Download JSON file for tracking

Path D (Retry failed):
  -> Select 5: Mark Failed Items for Retry
  -> Select 1: Run Migration again
  -> Select 3: Check what still failed

Path E (Quick check):
  -> Select 4: Show Migration Status
  -> See current completed/pending/failed counts

================================================================================
WHAT EACH OUTPUT MEANS
================================================================================

DEBUG OUTPUT:

    [DEBUG] Processing site: Harbour PD Work Flow
        = System starting to process this site

    [DEBUG] Source: harbouroutdoorasialimited.sharepoint.com
        = Where files are coming FROM

    Creating folder: HARBOUR 1
        = Creating a new folder in target

    >> FILE: 25% (15.6 MB / 61.7 MB)
        = Currently uploading FILE
        = 25% done
        = 15.6 MB uploaded so far out of 61.7 MB total

    [+] Copied file: FILE (61.71 MB)
        = Successfully completed copying FILE
        = File size is 61.71 MB

    [!] Warning message
        = Non-fatal issue (usually continues)

    [-] Error message
        = Item failed (added to failed list)

    [SUCCESS] Migration completed
        = Entire migration batch finished

================================================================================
FILES YOU'LL SEE CREATED
================================================================================

After running migration, you'll find these files in the folder:

    sharepoint-result-20260829-122500.json
        = JSON file with migration statistics

    migration-401-retry.log
        = Text log of all output from migration

    failed-items-report-20260829-122500.json
        = JSON file with details of failed items + paths

You can:
  - Download these files to your computer
  - Share with others
  - Analyze in Excel (JSON can be imported)
  - Keep for audit trail

================================================================================
QUICK TIPS
================================================================================

1. KEEP MIGRATIONS RUNNING
   - Once started, let it run to completion
   - Migration takes 2-4 hours for 3,000+ items
   - Don't close PowerShell while running

2. MONITOR IN SECOND WINDOW
   - Always open 2nd PowerShell for monitor_progress.py
   - Shows live % completion
   - Update every 5 seconds

3. CHECK FAILED ITEMS
   - Run generate_failed_report.py after each migration
   - Shows you EXACTLY what failed and why
   - Creates JSON file for analysis

4. RETRY IF NEEDED
   - Most failures are temporary (401, timeout)
   - Run migration again - many will succeed
   - Keep retrying until success rate > 99%

5. SAVE JSON REPORTS
   - Download failed-items-report-*.json files
   - Keep for record of what failed
   - Easy to analyze which items need attention

================================================================================
SUMMARY
================================================================================

You now have everything to run migrations locally with:
  - Live debug output (see every file being copied)
  - Progress monitoring (% completion in 2nd window)
  - Failed items report (file paths + error reasons in JSON)
  - Easy interactive menu (no complex commands)
  - Automatic retry handling (for temporary failures)

All scripts are ready to use. No additional setup needed!

Just run: python master_control.py and follow the menu options.

================================================================================
"""

print(guide)
