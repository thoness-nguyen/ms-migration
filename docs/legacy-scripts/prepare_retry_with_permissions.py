#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 80)
print("MARKING FAILED ITEMS FOR RETRY + PERMISSION MIGRATION")
print("=" * 80)

# Get current stats
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
status_dist = dict(cur.fetchall())

print(f"\n[BEFORE RESET]")
print(f"  Completed:  {status_dist.get('completed', 0)}")
print(f"  Failed:     {status_dist.get('failed', 0)}")
print(f"  Pending:    {status_dist.get('pending', 0)}")

# Mark all failed items as pending for retry
if status_dist.get('failed', 0) > 0:
    cur.execute("UPDATE checkpoints SET status='pending' WHERE status='failed'")
    conn.commit()
    print(f"\n[MARKED FOR RETRY]")
    print(f"  {status_dist.get('failed', 0)} items marked as 'pending'")

# Clear batch checkpoint to allow retry
cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
conn.commit()

print(f"\n[BATCH CHECKPOINT RESET]")
print(f"  Cleared batch-sharepoint entries")
print(f"  This allows library to be re-scanned with permission migration")

# Verify
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
new_stats = dict(cur.fetchall())

print(f"\n[AFTER RESET]")
print(f"  Completed:  {new_stats.get('completed', 0)}")
print(f"  Pending:    {new_stats.get('pending', 0)}")
print(f"  Failed:     {new_stats.get('failed', 0)}")
print(f"  Total:      {sum(new_stats.values())}")

print(f"\n[READY TO RUN]")
print(f"  Configuration:")
print(f"    • File size limit:    5 GB")
print(f"    • Timeout:            30 min/attempt")
print(f"    • Max retries:        3 attempts")
print(f"    • Permission setting: ENABLED (edit access for everyone)")
print(f"\n  Starting migration with permission migration enabled...")

conn.close()
print("\n" + "=" * 80)
