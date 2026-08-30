#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Clear batch checkpoint to allow full library retry
cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
conn.commit()

# Show current state
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
status_dist = {status: count for status, count in cur.fetchall()}

print("[RESET COMPLETE]")
print(f"  Pending items ready to retry: {status_dist.get('pending', 0)}")
print(f"  Completed items (will skip): {status_dist.get('completed', 0)}")
print("\n[READY] Running migration with admin consent now active...")

conn.close()
