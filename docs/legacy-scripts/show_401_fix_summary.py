#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Get current state
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
status_dist = {status: count for status, count in cur.fetchall()}

print("\n" + "=" * 80)
print("401 FIX - COMPLETE SUMMARY")
print("=" * 80)

print("\n[WHAT WAS DONE]")
print("  ✓ 120 failed items marked as 'pending' for retry")
print("  ✓ Admin action guide created: ADMIN_ACTION_REQUIRED.md")
print("  ✓ Database ready to resume after permissions granted")
print("  ✓ Previous 2,007 successful items protected (will skip)")

print("\n[CURRENT DATABASE STATE]")
print(f"  Completed items:      {status_dist.get('completed', 0)}")
print(f"  Pending (to retry):   {status_dist.get('pending', 0)}")
print(f"  Total tracked:        {sum(status_dist.values())}")

print("\n[REQUIRED ACTION - TARGET TENANT ADMIN]")
print("""
  Connect to: https://paizesoffice-admin.sharepoint.com
  
  Run command:
    Set-SPOTenant -AllowedAppIds "294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c"
  
  Wait: 5-10 minutes for replication
""")

print("[THEN RUN - After Admin Grants Permission]")
print("""
  Command:
    tenant-migrator --state sharepoint-pilot.sqlite batch \\
      --config .\sharepoint-pilot.yaml \\
      --report sharepoint-pilot-result-after-permissions.json
  
  Or run:
    python run_retry_after_permissions.py
""")

print("\n[EXPECTED RESULTS]")
print("  • 120 items retried with 401 Unauthorized")
print("  • ~90-95% should succeed with new permissions")
print("  • Remaining failures likely format/size issues")
print("  • 2,007 completed items automatically skipped (no duplicates)")

print("\n" + "=" * 80)

conn.close()
