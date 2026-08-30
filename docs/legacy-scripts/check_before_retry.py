#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 60)
print("MIGRATION PROGRESS CHECK (Before Retry)")
print("=" * 60)

# Count items by status
cur.execute('SELECT status, COUNT(*) FROM checkpoints WHERE workload="sharepoint" GROUP BY status')
for status, count in cur.fetchall():
    print(f"  {status:15} = {count:4} items")

# Show some completed folders
print(f"\nSample migrated items:")
cur.execute("""
SELECT source_id, detail 
FROM checkpoints 
WHERE workload="sharepoint" AND status="completed" 
LIMIT 10
""")
for i, (source_id, target_id) in enumerate(cur.fetchall(), 1):
    item_id = source_id.split(':')[-1][:25]
    status_icon = "✓"
    print(f"  {i:2}. {status_icon} {item_id}...")

conn.close()
print("\n" + "=" * 60)
print("Running migration with auto-retry...\n")
