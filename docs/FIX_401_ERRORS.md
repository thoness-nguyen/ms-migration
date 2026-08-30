#!/usr/bin/env python3
"""
SharePoint Migration 401 Fix Guide
Fix for: Unauthorized errors during folder/file copy to target tenant

WHAT'S HAPPENING:
  App (294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c) cannot write to target SharePoint
  because it's not in the allowed apps list.

SOLUTION STEPS:
  1. Target tenant admin runs the PowerShell command below
  2. Wait 5-10 minutes for replication
  3. Re-run migration to retry the 120 failed items
"""

print("=" * 80)
print("SHAREPOINT MIGRATION: FIX FOR 401 UNAUTHORIZED ERRORS")
print("=" * 80)

print("\n[STEP 1] Target Tenant Admin - Run this PowerShell command:")
print("─" * 80)

command = """
# Connect to target tenant
Connect-SPOService -Url https://paizesoffice-admin.sharepoint.com

# Add app to allowed list
Set-SPOTenant -AllowedAppIds "294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c"

# Verify
Get-SPOTenant | Select-Object AllowedAppIds
"""

print(command)

print("─" * 80)
print("\n[STEP 2] Wait 5-10 minutes for Azure AD replication")
print("\n[STEP 3] After admin confirms permission is granted, run:")
print("─" * 80)
print("tenant-migrator --state sharepoint-pilot.sqlite batch --config .\sharepoint-pilot.yaml --report sharepoint-pilot-retry.json")
print("─" * 80)

print("\n[EXPECTED RESULT]")
print("  - 120 previously-failed items will retry")
print("  - Most should succeed (401 errors will be resolved)")
print("  - Remaining failures likely due to file format/size issues")

print("\n" + "=" * 80)
print("CONFIGURATION SUMMARY")
print("=" * 80)
print("  Source app:           294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c")
print("  Target tenant:        paizesoffice.sharepoint.com")
print("  Failed items:         120 (status: 401 Unauthorized)")
print("  Ready to retry:       Yes (via SQLite checkpoint system)")
print("\n" + "=" * 80)
