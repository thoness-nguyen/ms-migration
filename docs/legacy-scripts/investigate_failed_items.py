#!/usr/bin/env python3
import sqlite3
import json
from collections import defaultdict

conn = sqlite3.connect('sharepoint-pilot.sqlite')
cur = conn.cursor()

print("=" * 90)
print("INVESTIGATING 130 FAILED ITEMS - ERROR ANALYSIS")
print("=" * 90)

# Get all failed items with details
cur.execute("""
    SELECT source_id, detail FROM checkpoints 
    WHERE status='failed' 
    ORDER BY detail
""")

failed_items = cur.fetchall()
print(f"\n[TOTAL FAILED ITEMS] {len(failed_items)}")

# Categorize errors
error_categories = defaultdict(list)
for source_id, detail in failed_items:
    if not detail:
        error_type = "Unknown error"
    elif "401" in detail or "Unauthorized" in detail:
        error_type = "401 Unauthorized"
    elif "403" in detail or "Forbidden" in detail:
        error_type = "403 Forbidden"
    elif "timeout" in detail.lower():
        error_type = "Timeout/Connection"
    elif "not found" in detail.lower() or "404" in detail:
        error_type = "404 Not Found"
    elif "already exists" in detail.lower() or "conflict" in detail.lower():
        error_type = "Conflict/Duplicate"
    elif "permission" in detail.lower():
        error_type = "Permission Issue"
    elif "size" in detail.lower() or "oversized" in detail.lower():
        error_type = "File Size Issue"
    elif "character" in detail.lower() or "encoding" in detail.lower():
        error_type = "Encoding/Character Issue"
    else:
        error_type = "Other Error"
    
    error_categories[error_type].append((source_id, detail))

# Print categorized errors
print(f"\n[ERROR BREAKDOWN BY CATEGORY]")
print("─" * 90)

for error_type in sorted(error_categories.keys(), key=lambda x: -len(error_categories[x])):
    items = error_categories[error_type]
    print(f"\n{error_type}: {len(items)} items")
    
    # Show sample errors
    for i, (source_id, detail) in enumerate(items[:3], 1):
        source_short = source_id[-30:] if source_id else "unknown"
        error_short = detail[:70] if detail else "no detail"
        print(f"  {i}. ...{source_short}")
        print(f"     {error_short}...")
    
    if len(items) > 3:
        print(f"  ... and {len(items) - 3} more")

print(f"\n[ASSESSMENT]")
print("─" * 90)

# Analyze which errors might be retryable
retryable_count = 0
retryable_errors = []

for error_type, items in error_categories.items():
    if error_type in ["Timeout/Connection", "401 Unauthorized", "403 Forbidden"]:
        retryable_count += len(items)
        retryable_errors.append(error_type)

print(f"\nPotentially RETRYABLE errors: {retryable_count} items")
for err_type in retryable_errors:
    print(f"  • {err_type}: {len(error_categories[err_type])} items")

non_retryable_count = len(failed_items) - retryable_count
print(f"\nLikely NON-RETRYABLE errors: {non_retryable_count} items")
for error_type in sorted(error_categories.keys()):
    if error_type not in retryable_errors:
        print(f"  • {error_type}: {len(error_categories[error_type])} items")

print(f"\n[RECOMMENDATION]")
print("─" * 90)
if retryable_count > 0:
    print(f"  ✓ {retryable_count} items CAN be retried (may resolve with second attempt)")
    print(f"  ✗ {non_retryable_count} items likely permanent failures")
    print(f"\n  ACTION: Mark {retryable_count} items for retry + run migration again")
else:
    print(f"  All {len(failed_items)} failures appear to be permanent")

conn.close()
print("\n" + "=" * 90)
