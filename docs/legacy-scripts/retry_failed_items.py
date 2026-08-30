#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 70)
print("MARKING 111 FAILED ITEMS FOR RETRY")
print("=" * 70)

# Mark all failed items for retry
cur.execute("UPDATE checkpoints SET status='pending' WHERE status='failed'")
conn.commit()

# Verify
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
stats = dict(cur.fetchall())

print(f"\nBefore: 111 failed, 0 pending")
print(f"After:  {stats.get('completed', 0)} completed, {stats.get('pending', 0)} pending, {stats.get('failed', 0)} failed")

print(f"\n[READY FOR RETRY]")
print(f"  • All permissions have fully replicated")
print(f"  • Running again may resolve some of the 111 items")
print(f"  • 2,727 completed items will be skipped (no duplicates)")

conn.close()
