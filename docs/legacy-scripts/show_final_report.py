#!/usr/bin/env python3
import json

with open('sharepoint-pilot-final.json') as f:
    report = json.load(f)

result = report['results'][0]
lib = result['libraries'][0]
stats = lib['stats']

print("=" * 70)
print("MIGRATION FINAL RUN - WITH 5GB FILE SIZE SUPPORT")
print("=" * 70)
print(f"\nSite: {result['site']}")
print(f"Library: {lib['name']}")
print(f"Status: {lib['status'].upper()}")

print("\n[STATISTICS]")
print(f"  Files copied:       {stats['files_copied']:4}")
print(f"  Folders created:    {stats['folders_created']:4}")
print(f"  Skipped:            {stats['skipped']:4}")
print(f"  Failed:             {stats['failed']:4}")
print(f"  Oversized (>5GB):   {stats.get('oversized_skipped', 0):1}")
print(f"  {'─' * 50}")
total = stats['files_copied'] + stats['folders_created']
print(f"  TOTAL THIS RUN:      {total:4} items")

errors = stats.get('errors', [])
print(f"\n[ERRORS] {len(errors)} items")
if errors:
    for i, err in enumerate(errors[:3], 1):
        item = err.get('item', 'unknown')[:35]
        err_msg = err.get('error', '')[:50]
        print(f"  {i}. {item}...")
        print(f"     {err_msg}...")
    if len(errors) > 3:
        print(f"  ... and {len(errors)-3} more")

print("\n[CONFIGURATION]")
print(f"  File size limit:    5 GB (previously 2 GB)")
print(f"  Timeout per attempt: 30 minutes (previously 15 min)")
print(f"  Max retries:        3 attempts")

print("\n" + "=" * 70)
