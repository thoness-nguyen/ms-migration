#!/usr/bin/env python3
"""
Generate detailed report of failed items with file paths
Shows what failed to migrate and why
"""
import sqlite3
import json
from datetime import datetime
from collections import defaultdict

def generate_failed_report():
    """Generate comprehensive report of failed migration items"""
    
    conn = sqlite3.connect('sharepoint-pilot.sqlite')
    cur = conn.cursor()
    
    # Get all failed items
    cur.execute('''
        SELECT source_id, detail FROM checkpoints 
        WHERE status="failed" AND workload="sharepoint"
        ORDER BY detail
    ''')
    
    failed_items = cur.fetchall()
    
    # Get item details from checkpoint table (if available)
    cur.execute('''
        SELECT DISTINCT source_id FROM checkpoints 
        WHERE status="failed" AND workload="sharepoint"
    ''')
    
    failed_ids = [row[0] for row in cur.fetchall()]
    
    print("=" * 100)
    print("SHAREPOINT MIGRATION - FAILED ITEMS DETAILED REPORT")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    if not failed_items:
        print("\n✅ NO FAILED ITEMS - MIGRATION COMPLETE!")
        conn.close()
        return
    
    # Categorize by error type
    error_categories = defaultdict(list)
    
    for source_id, detail in failed_items:
        if not detail:
            error_type = "Unknown error"
        elif "401" in detail or "Unauthorized" in detail:
            error_type = "401 Unauthorized - Permission/Auth issue"
        elif "403" in detail or "Forbidden" in detail:
            error_type = "403 Forbidden - Access denied"
        elif "404" in detail or "not found" in detail:
            error_type = "404 Not Found - Source item missing"
        elif "timeout" in detail.lower() or "deadline" in detail.lower():
            error_type = "Timeout - Connection/operation too slow"
        elif "charmap" in detail.lower() or "encode" in detail.lower():
            error_type = "Encoding - Invalid filename characters"
        elif "conflict" in detail.lower() or "already exists" in detail.lower():
            error_type = "Conflict - Item already exists in target"
        elif "permission" in detail.lower():
            error_type = "Permission - Cannot grant permissions"
        elif "size" in detail.lower():
            error_type = "File Size - Item too large or quota exceeded"
        else:
            error_type = f"Other - {detail[:40]}"
        
        error_categories[error_type].append((source_id, detail))
    
    # Print summary by error type
    print(f"\n📊 FAILED ITEMS SUMMARY")
    print("-" * 100)
    print(f"Total Failed: {len(failed_items)}\n")
    
    for error_type in sorted(error_categories.keys(), key=lambda x: -len(error_categories[x])):
        items = error_categories[error_type]
        print(f"\n🔴 {error_type}")
        print(f"   Count: {len(items)} items")
        print()
        
        # Show all items in this category
        for source_id, detail in items:
            # Try to extract filename from source_id if possible
            source_short = source_id[-50:] if source_id else "unknown"
            error_short = detail[:75] if detail else "no detail"
            
            print(f"   • Item ID: ...{source_short}")
            print(f"     Error: {error_short}")
            if len(detail) > 75:
                print(f"             {detail[75:150]}")
            print()
    
    # Generate exportable report file
    report_data = {
        "generated_at": datetime.now().isoformat(),
        "total_failed": len(failed_items),
        "error_categories": {},
        "failed_items": []
    }
    
    for error_type, items in error_categories.items():
        report_data["error_categories"][error_type] = len(items)
        for source_id, detail in items:
            report_data["failed_items"].append({
                "source_id": source_id,
                "error_type": error_type,
                "error_detail": detail
            })
    
    # Save JSON report
    report_file = f"failed-items-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report_data, f, indent=2)
    
    print("-" * 100)
    print(f"\n✅ Report exported to: {report_file}")
    print()
    print("=" * 100)
    
    conn.close()

if __name__ == "__main__":
    generate_failed_report()
