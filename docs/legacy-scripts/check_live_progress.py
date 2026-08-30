#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Get current stats
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
stats = dict(cur.fetchall())

completed = stats.get('completed', 0)
pending = stats.get('pending', 0)
failed = stats.get('failed', 0)

print("=" * 70)
print("LIVE MIGRATION STATUS")
print("=" * 70)
print(f"\nDatabase status:")
print(f"  Completed items:  {completed}")
print(f"  Pending (retry):  {pending}")
print(f"  Failed items:     {failed}")
print(f"  Total:            {sum(stats.values())}")

print(f"\n[MIGRATION PROGRESS]")
if pending > 0:
    pct = (completed / (completed + pending)) * 100 if (completed + pending) > 0 else 0
    print(f"  Progress: {pct:.1f}% complete")
    print(f"  Remaining: {pending} items")
else:
    print(f"  All items processed!")

# Show recent entries
print(f"\n[SAMPLE RECENT COMPLETED ITEMS]")
cur.execute("""
    SELECT source_id, status FROM checkpoints 
    WHERE status='completed' 
    ORDER BY rowid DESC LIMIT 5
""")

for i, (source_id, status) in enumerate(cur.fetchall(), 1):
    short_id = source_id[-30:] if source_id else "unknown"
    print(f"  {i}. {status}: ...{short_id}")

conn.close()
print("\n" + "=" * 70)
