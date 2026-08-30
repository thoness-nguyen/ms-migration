#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("[BEFORE RESET]")
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='batch-sharepoint'")
print(f"  batch-sharepoint checkpoints: {cur.fetchone()[0]}")

# Clear the batch-sharepoint completions to allow re-run
cur.execute("DELETE FROM checkpoints WHERE workload='batch-sharepoint'")
conn.commit()

print("\n[CLEARING BATCH CHECKPOINTS]")
print("  Removed: batch-sharepoint entries")
print("  This allows re-running the Documents library with 5 GB limit")
print("  Individual file/folder tracking (sharepoint workload) preserved")

cur.execute("SELECT COUNT(*) FROM checkpoints WHERE workload='sharepoint'")
print(f"\n[AFTER RESET]")
print(f"  sharepoint checkpoints (files/folders): {cur.fetchone()[0]}")
print(f"  batch-sharepoint checkpoints: 0 (cleared for re-run)")

conn.close()
print("\n[OK] Ready to re-run with new configuration!")
