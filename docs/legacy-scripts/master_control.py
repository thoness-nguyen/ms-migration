#!/usr/bin/env python3
"""
Master Control Script - Easy menu to run migration tasks
"""
import subprocess
import os
import sys
import sqlite3
from datetime import datetime

def print_menu():
    """Display main menu"""
    print("\n" + "=" * 80)
    print("SHAREPOINT MIGRATION - MASTER CONTROL")
    print("(SharePoint workload only - Teams/OneDrive not configured yet)")
    print("=" * 80)
    print()
    print("  1. Run Migration - SharePoint ONLY (Debug mode)")
    print("  2. Monitor Progress (Real-time)")
    print("  3. Generate Failed Items Report")
    print("  4. Show Migration Status")
    print("  5. Mark Failed Items for Retry")
    print("  6. Clear Batch State (reset if stuck)")
    print("  7. Open Debug Guide")
    print("  8. Exit")
    print()

def run_migration():
    """Run migration with debug output (SharePoint workload only)"""
    print("\n🚀 Starting SharePoint migration with debug output...")
    print("   ℹ️  Only the 'sharepoint' workload in sharepoint-pilot.yaml runs.")
    print("   ℹ️  Teams and OneDrive are not configured/processed by this run.\n")
    os.system('python run_migration_debug.py')

def monitor_progress():
    """Monitor migration progress"""
    print("\n📊 Starting progress monitor (Ctrl+C to stop)...\n")
    try:
        os.system('python monitor_progress.py')
    except KeyboardInterrupt:
        print("\n⏹️  Monitoring stopped")

def generate_report():
    """Generate failed items report"""
    print("\n📋 Generating failed items report...\n")
    os.system('python generate_failed_report.py')

def show_status():
    """Show current migration status"""
    try:
        conn = sqlite3.connect('sharepoint-pilot.sqlite')
        cur = conn.cursor()
        
        print("\n" + "=" * 80)
        print("CURRENT MIGRATION STATUS")
        print("=" * 80)
        
        cur.execute('''
            SELECT status, COUNT(*) FROM checkpoints 
            WHERE workload="sharepoint"
            GROUP BY status
        ''')
        
        stats = dict(cur.fetchall())
        completed = stats.get('completed', 0)
        pending = stats.get('pending', 0)
        failed = stats.get('failed', 0)
        total = completed + pending + failed
        
        pct = (completed / total * 100) if total > 0 else 0
        
        print(f"\n  Completed: {completed:,} items ✓")
        print(f"  Pending:   {pending:,} items (needs retry)")
        print(f"  Failed:    {failed:,} items")
        print(f"  ────────────────────────")
        print(f"  Total:     {total:,} items")
        print(f"  Success:   {pct:.1f}%")
        
        if failed > 0:
            print(f"\n  ⚠️  {failed} items failed - Run option 5 to retry")
        elif pending > 0:
            print(f"\n  ⏳ {pending} items pending - Run option 1 to continue")
        else:
            print(f"\n  ✅ Migration complete!")
        
        print("\n" + "=" * 80 + "\n")
        
        conn.close()
    except Exception as e:
        print(f"  ❌ Error: {e}")

def mark_for_retry():
    """Mark failed items for retry"""
    try:
        conn = sqlite3.connect('sharepoint-pilot.sqlite')
        cur = conn.cursor()
        
        # Count current failed items
        cur.execute('SELECT COUNT(*) FROM checkpoints WHERE status="failed" AND workload="sharepoint"')
        count = cur.fetchone()[0]
        
        if count == 0:
            print("\n  ✅ No failed items to retry\n")
            return
        
        print(f"\n  🔄 Marking {count} failed items for retry...")
        
        # Mark as pending
        cur.execute('UPDATE checkpoints SET status="pending" WHERE status="failed"')
        cur.execute('DELETE FROM checkpoints WHERE workload="batch-sharepoint"')
        conn.commit()
        
        print(f"  ✓ {count} items marked as pending")
        print("  ℹ️  Run option 1 to retry these items\n")
        
        conn.close()
    except Exception as e:
        print(f"  ❌ Error: {e}")

def clear_batch_state():
    """Clear batch state if stuck"""
    confirm = input("\n⚠️  Clear batch checkpoint? (yes/no): ")
    if confirm.lower() == 'yes':
        try:
            conn = sqlite3.connect('sharepoint-pilot.sqlite')
            cur = conn.cursor()
            cur.execute('DELETE FROM checkpoints WHERE workload="batch-sharepoint"')
            conn.commit()
            print("✓ Batch checkpoint cleared\n")
            conn.close()
        except Exception as e:
            print(f"❌ Error: {e}\n")

def open_guide():
    """Open the debug guide"""
    print("\n📖 Opening debug guide...\n")
    os.system('notepad DEBUG_RUN_GUIDE.md')

def main():
    """Main loop"""
    while True:
        print_menu()
        choice = input("Enter choice (1-8): ").strip()
        
        if choice == '1':
            run_migration()
        elif choice == '2':
            monitor_progress()
        elif choice == '3':
            generate_report()
        elif choice == '4':
            show_status()
        elif choice == '5':
            mark_for_retry()
        elif choice == '6':
            clear_batch_state()
        elif choice == '7':
            open_guide()
        elif choice == '8':
            print("\n✅ Goodbye!\n")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Try again.\n")

if __name__ == "__main__":
    main()
