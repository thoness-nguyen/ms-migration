#!/usr/bin/env python3
"""
RUN THIS AFTER ADMIN GRANTS PERMISSIONS

When the admin has completed the permission grant:
  1. Wait 5-10 minutes for Azure AD replication
  2. Run this script to retry the 120 failed items
"""

import subprocess
import sys
from datetime import datetime

print("=" * 80)
print("SHAREPOINT MIGRATION - RETRY FAILED ITEMS")
print("=" * 80)

print(f"\nStarting retry at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("\nConfiguration:")
print("  Database:            sharepoint-pilot.sqlite")
print("  Config file:         sharepoint-pilot.yaml")
print("  Items to retry:      120 (with 401 Unauthorized)")
print("  Previous items:      2,007 (will be skipped)")
print("  Total processed:     2,127")
print("\nExpected behavior:")
print("  • Skips all 2,007 already-completed items (~1 min)")
print("  • Retries 120 pending items (longer - large PSD files)")
print("  • ~90-95% should succeed with new permissions")
print("  • Remaining failures likely due to file format issues")

print("\n" + "=" * 80)
print("RUNNING MIGRATION...\n")

try:
    result = subprocess.run([
        "tenant-migrator",
        "--state", "sharepoint-pilot.sqlite",
        "batch",
        "--config", ".\\sharepoint-pilot.yaml",
        "--report", "sharepoint-pilot-result-after-permissions.json"
    ], cwd=".")
    
    if result.returncode == 0:
        print("\n" + "=" * 80)
        print("[SUCCESS] Migration retry completed!")
        print("=" * 80)
        print("\nReport saved to: sharepoint-pilot-result-after-permissions.json")
        print("\nNext steps:")
        print("  1. Review the report for results")
        print("  2. Run: python show_final_v2_report.py (or .\\show_final_v2_report.py)")
        print("  3. If any items still fail, check error details in the report")
    else:
        print(f"\n[ERROR] Migration failed with exit code {result.returncode}")
        sys.exit(1)
        
except FileNotFoundError:
    print("[ERROR] tenant-migrator not found. Make sure venv is activated:")
    print("  .\.venv\Scripts\Activate.ps1")
    sys.exit(1)

print("=" * 80)
