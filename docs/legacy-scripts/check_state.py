#!/usr/bin/env python3
import sqlite3
import json

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 60)
print("SHAREPOINT MIGRATION STATE SUMMARY")
print("=" * 60)

cur.execute('SELECT COUNT(*) FROM checkpoints')
total = cur.fetchone()[0]
print(f"\nTotal checkpoints saved: {total}")

cur.execute('SELECT workload, status, COUNT(*) FROM checkpoints GROUP BY workload, status')
print("\nBy workload and status:")
for row in cur.fetchall():
    print(f"  {row[0]:30} {row[1]:12} = {row[2]:5} items")

print("\nSample completed items (first 10):")
cur.execute("SELECT workload, source_id, detail FROM checkpoints WHERE status='completed' LIMIT 10")
for i, row in enumerate(cur.fetchall(), 1):
    source = row[1][:50] + "..." if len(row[1]) > 50 else row[1]
    detail = row[2][:40] + "..." if row[2] and len(row[2]) > 40 else row[2]
    print(f"  {i}. {source}")
    if detail:
        print(f"     → {detail}")

conn.close()
print("\n" + "=" * 60)
