#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 80)
print("MIGRATION RETRY RESULTS - DATABASE STATUS")
print("=" * 80)

# Get status counts
cur.execute('SELECT status, COUNT(*) FROM checkpoints WHERE workload="sharepoint" GROUP BY status')
results = dict(cur.fetchall())

completed = results.get('completed', 0)
failed = results.get('failed', 0)
pending = results.get('pending', 0)

total = completed + failed + pending
success_rate = (completed / total * 100) if total > 0 else 0

print(f"\nAfter Retry:")
print(f"  Completed: {completed}")
print(f"  Failed:    {failed}")
print(f"  Pending:   {pending}")
print(f"  Total:     {total}")
print(f"  Success Rate: {success_rate:.1f}%")

# Get sample of remaining failures
if failed > 0:
    print(f"\nRemaining {failed} failures - Sample errors:")
    cur.execute('SELECT source_id, detail FROM checkpoints WHERE status="failed" AND workload="sharepoint" LIMIT 5')
    for source_id, detail in cur.fetchall():
        short_id = source_id[-30:] if source_id else 'unknown'
        short_err = detail[:60] if detail else 'no detail'
        print(f"  • ...{short_id}")
        print(f"    {short_err}...")

print("\n" + "=" * 80)

conn.close()
