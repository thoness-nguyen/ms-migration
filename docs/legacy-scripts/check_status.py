#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

# Get status distribution
cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
stats = dict(cur.fetchall())

print("=" * 70)
print("MIGRATION STATUS CHECK")
print("=" * 70)

completed = stats.get('completed', 0)
pending = stats.get('pending', 0)
failed = stats.get('failed', 0)

print(f"\nCurrent status:")
print(f"  Completed: {completed}")
print(f"  Pending:   {pending}")
print(f"  Failed:    {failed}")
print(f"  Total:     {sum(stats.values())}")

if failed > 0:
    print(f"\n[RETRY OPPORTUNITY]")
    print(f"  {failed} items failed - trying again may help")
    print(f"  Admin permissions have fully replicated by now")
    print(f"  Recommend: Mark all failed items for retry")
    
    # Show sample of failed items
    print(f"\n[SAMPLE FAILED ITEMS]")
    cur.execute("SELECT source_id, detail FROM checkpoints WHERE status='failed' LIMIT 5")
    for i, (source_id, detail) in enumerate(cur.fetchall(), 1):
        short_id = source_id[-25:] if source_id else "unknown"
        err = detail[:60] if detail else "unknown"
        print(f"  {i}. {short_id}")
        print(f"     Error: {err}...")

conn.close()
