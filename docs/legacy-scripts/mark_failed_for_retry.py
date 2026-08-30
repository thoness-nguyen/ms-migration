#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("[MARKING FAILED ITEMS FOR RETRY]")
print("─" * 70)

# Get all failed items
cur.execute("""
    SELECT id, workload, source_id, detail FROM checkpoints 
    WHERE status='failed' ORDER BY id DESC LIMIT 20
""")

failed_items = cur.fetchall()
print(f"\nShowing sample of {len(failed_items)} failed items:")
for row in failed_items[:10]:
    item_id, workload, source_id, detail = row
    err = detail[:50] if detail else "unknown"
    print(f"  {item_id}: {err}...")

# Count 401 errors
cur.execute("""
    SELECT COUNT(*) FROM checkpoints 
    WHERE status='failed' AND detail LIKE '%401%'
""")
count_401 = cur.fetchone()[0]

print(f"\nTotal failed items with 401 errors: {count_401}")

# Mark all failed items for retry by changing status to 'pending'
cur.execute("UPDATE checkpoints SET status='pending' WHERE status='failed'")
conn.commit()

# Verify
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE status='pending'")
pending = cur.fetchone()[0]

print(f"\n[MARKED FOR RETRY]")
print(f"  {pending} items marked as 'pending' (will retry on next run)")

print("\n[READY TO RETRY]")
print("  After target tenant admin grants permissions:")
print("  1. Wait 5-10 minutes for replication")
print("  2. Run: tenant-migrator batch --config .\sharepoint-pilot.yaml")
print("  3. All 120 items will be attempted again")

conn.close()
