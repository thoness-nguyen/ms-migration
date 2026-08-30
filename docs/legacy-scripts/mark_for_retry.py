#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 70)
print("MARKING 120 FAILED ITEMS FOR RETRY")
print("=" * 70)

# Show sample of failed items
print("\n[SAMPLE FAILED ITEMS (First 5)]")
cur.execute("""
    SELECT source_id, detail FROM checkpoints 
    WHERE status='failed' LIMIT 5
""")

for i, (source_id, detail) in enumerate(cur.fetchall(), 1):
    source_short = source_id[-20:] if source_id else "unknown"
    err_short = detail[:50] if detail else "unknown"
    print(f"  {i}. {source_short}")
    print(f"     {err_short}...")

# Count failed
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE status='failed'")
failed_count = cur.fetchone()[0]

# Count 401 errors
cur.execute("""
    SELECT COUNT(*) FROM checkpoints 
    WHERE status='failed' AND detail LIKE '%401%'
""")
count_401 = cur.fetchone()[0]

print(f"\n[CURRENT STATUS]")
print(f"  Total failed items:    {failed_count}")
print(f"  401 Unauthorized:      {count_401}")
print(f"  Other errors:          {failed_count - count_401}")

# Mark all failed items for retry
cur.execute("UPDATE checkpoints SET status='pending' WHERE status='failed'")
conn.commit()

# Verify
cur.execute("SELECT COUNT(*) FROM checkpoints WHERE status='pending'")
pending = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM checkpoints WHERE status='completed'")
completed = cur.fetchone()[0]

print(f"\n[AFTER MARKING FOR RETRY]")
print(f"  Pending (to retry):    {pending}")
print(f"  Completed (will skip): {completed}")
print(f"  Total items:           {pending + completed}")

print(f"\n[NEXT STEPS]")
print(f"  1. Admin runs: Set-SPOTenant -AllowedAppIds 294bb4f3-fb36-49bf-a2a3-fd6fdbfe703c")
print(f"  2. Wait 5-10 minutes for replication")
print(f"  3. Run: tenant-migrator --state sharepoint-pilot.sqlite batch \\")
print(f"          --config .\sharepoint-pilot.yaml --report result-retry.json")

conn.close()

print("\n" + "=" * 70)
print("READY FOR RETRY AFTER PERMISSIONS ARE GRANTED")
print("=" * 70)
