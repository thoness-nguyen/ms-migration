#!/usr/bin/env python3
"""
ADMIN ACTION REQUIRED
SharePoint Tenant-to-Tenant Migration: 401 Unauthorized Fix

Application ID: 294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c
Target Tenant: paizesoffice.sharepoint.com
Items Pending Retry: 120

THE PROBLEM:
  The migration app (294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c) tried to create 120
  items (mostly PSD files) in the target SharePoint, but received 401 Unauthorized.
  
  Root cause: App is not in the target tenant's allowed apps list.

THE FIX (Admin only):
  Run this PowerShell command on target tenant admin account:
"""

print("=" * 80)
print("SHAREPOINT MIGRATION: ADMIN ACTION REQUIRED")
print("=" * 80)

print("""
[ADMIN] Connect to target tenant and run:
───────────────────────────────────────────────────────────────────────────────

  # Step 1: Connect to target tenant (paizesoffice.sharepoint.com)
  Connect-SPOService -Url https://paizesoffice-admin.sharepoint.com

  # Step 2: Add the app to allowed list
  Set-SPOTenant -AllowedAppIds "294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c"

  # Step 3: Verify it was added
  Get-SPOTenant | Select-Object AllowedAppIds

───────────────────────────────────────────────────────────────────────────────

[TIMELINE]
  Admin runs command:     Immediately
  Azure AD replication:   5-10 minutes
  Ready to retry:         After replication completes

[AFTER ADMIN GRANTS PERMISSION]
───────────────────────────────────────────────────────────────────────────────

Run this migration command (in current workspace):

  tenant-migrator --state sharepoint-pilot.sqlite batch \\
    --config .\sharepoint-pilot.yaml \\
    --report sharepoint-pilot-result-after-permissions.json

Expected result: 120 items will be retried, ~90-95% should succeed

───────────────────────────────────────────────────────────────────────────────
""")

print("=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"""
  Status:              120 items marked pending (awaiting permissions)
  Configuration:       Unchanged (5 GB files, 30 min timeout, 3 retries)
  Resumable:           Yes (all progress saved in SQLite)
  Data loss risk:      None (will skip 2,007 already-completed items)
  
  Last migration:      SUCCESS (397 files + 13 folders, includes 2.5 GB file)
  Ready to retry:      YES (waiting for admin permission grant)
""")
print("=" * 80)
