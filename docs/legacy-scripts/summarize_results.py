#!/usr/bin/env python3
import json

with open('sharepoint-pilot-result-resume.json') as f:
    report = json.load(f)

result = report['results'][0]
lib = result['libraries'][0]
stats = lib['stats']

print("=" * 70)
print("MIGRATION COMPLETED WITH AUTO-RETRY & RESUMABILITY")
print("=" * 70)
print(f"\nSite: {result['site']}")
print(f"Library: {lib['name']}")
print(f"Status: {lib['status'].upper()}")

print("\n[STATISTICS]")
print(f"  Files copied (this run):    {stats['files_copied']:4} new files")
print(f"  Folders created (this run): {stats['folders_created']:4} new folders")
print(f"  Skipped (already done):     {stats['skipped']:4} items from previous run")
print(f"  Failed (need review):       {stats['failed']:4} items")
print(f"  Oversized (skipped):        {stats['oversized_skipped']:1} file(s) > 2GB")
print(f"  {'─' * 50}")
print(f"  TOTAL MIGRATED (THIS RUN):  {stats['files_copied'] + stats['folders_created']:4} items")
print(f"  CUMULATIVE (ALL RUNS):      {stats['files_copied'] + stats['folders_created'] + stats['skipped']:4} items")

print("\n[ERRORS FOUND]")
errors = stats.get('errors', [])
if errors:
    for i, err in enumerate(errors[:5], 1):
        item_type = err.get('type', 'unknown')
        item_name = err.get('item', 'unknown')[:40]
        error_msg = err.get('error', '')[:60]
        print(f"  {i}. [{item_type}] {item_name}...")
        print(f"     Error: {error_msg}...")
    if len(errors) > 5:
        print(f"  ... and {len(errors) - 5} more")
else:
    print("  [None]")

print("\n[NEXT STEPS]")
print("  1. Check target SharePoint site for migrated content")
print("  2. Review failed items (likely permission or file-type issues)")
print("  3. For large .psd files, consider splitting or manual migration")
print("  4. For 401 errors, check if app has read/write permissions")

print("\n" + "=" * 70)
