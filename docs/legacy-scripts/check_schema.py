#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Check schema
cur.execute("PRAGMA table_info(checkpoints)")
columns = cur.fetchall()

print("[DATABASE SCHEMA]")
print("Columns in checkpoints table:")
for col in columns:
    print(f"  {col[1]}: {col[2]}")

# Count by status
print("\n[STATUS DISTRIBUTION]")
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
for status, count in cur.fetchall():
    print(f"  {status}: {count}")

# Sample failed items
print("\n[SAMPLE FAILED ITEMS]")
cur.execute("SELECT workload, source_id, detail FROM checkpoints WHERE status='failed' LIMIT 5")
for row in cur.fetchall():
    workload, source_id, detail = row
    print(f"  {workload}: {source_id}")
    print(f"    Error: {detail[:60]}...")

conn.close()
