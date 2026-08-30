#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Check if batch-sharepoint checkpoint exists
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='batch-sharepoint'")
batch_count = cur.fetchone()[0]

print("=" * 70)
print("CHECKING BATCH CHECKPOINT")
print("=" * 70)

print(f"\nBatch checkpoints (workload='batch-sharepoint'): {batch_count}")

if batch_count > 0:
    print("\n[ISSUE FOUND]")
    print("  Batch checkpoint exists - preventing full retry")
    print("  Clearing batch checkpoint to allow library processing...")
    
    cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
    conn.commit()
    
    print("\n[FIXED]")
    print("  Batch checkpoints cleared")
    print("  Ready for full retry of 111 pending items")

conn.close()
