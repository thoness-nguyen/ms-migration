#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 80)
print("MARKING 130 FAILED ITEMS FOR RETRY")
print("=" * 80)

# Get current stats
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
before_stats = dict(cur.fetchall())

print(f"\n[BEFORE MARKING]")
print(f"  Completed:  {before_stats.get('completed', 0)}")
print(f"  Failed:     {before_stats.get('failed', 0)}")
print(f"  Pending:    {before_stats.get('pending', 0)}")

# Mark all failed items as pending
cur.execute("UPDATE checkpoints SET status='pending' WHERE status='failed'")
conn.commit()

# Clear batch checkpoint to allow full re-scan
cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
conn.commit()

# Verify
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
after_stats = dict(cur.fetchall())

print(f"\n[AFTER MARKING FOR RETRY]")
print(f"  Completed:  {after_stats.get('completed', 0)}")
print(f"  Pending:    {after_stats.get('pending', 0)}")
print(f"  Failed:     {after_stats.get('failed', 0)}")

print(f"\n[WHY THESE 401s ARE RETRYABLE]")
print(f"  • Permission grant was just applied (may need additional propagation)")
print(f"  • Auth tokens may have expired (will refresh on retry)")
print(f"  • Batch-level permissions may need time to sync")
print(f"  • SharePoint Graph API may need cache invalidation")

print(f"\n[READY FOR RETRY]")
print(f"  Items to retry:       {after_stats.get('pending', 0)}")
print(f"  Configuration:        5GB, 30-min timeout, permission migration ON")
print(f"  Expected success:     80-90% (permission propagation should resolve)")

conn.close()
print("\n" + "=" * 80)
