#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("[RESETTING BATCH CHECKPOINT FOR FULL RETRY]")
print("─" * 70)

# Show current state
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='batch-sharepoint'")
batch_count = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='sharepoint'")
item_count = cur.fetchone()[0]

print(f"\nBefore reset:")
print(f"  Batch-level markers:  {batch_count} (blocks full retry)")
print(f"  Item-level markers:   {item_count} (tracks files/folders)")

# Clear batch-level marker to force full retry
cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
conn.commit()

print(f"\nAfter reset:")
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='batch-sharepoint'")
print(f"  Batch-level markers:  {cur.fetchone()[0]} (cleared for retry)")
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='sharepoint'")
print(f"  Item-level markers:   {cur.fetchone()[0]} (preserved)")

print("\n[READY FOR FULL RETRY]")
print("  Migration will:")
print("    1. Skip 2,007 already-completed items (~1 min)")
print("    2. Retry 120 pending items with new permissions")
print("    3. Update report with combined results")

conn.close()
