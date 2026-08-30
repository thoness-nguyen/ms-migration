#!/usr/bin/env python3
"""
Monitor and display live migration progress
Shows completion %, files/folders copied, errors, and estimated time
"""
import sqlite3
import time
from datetime import datetime

def monitor_migration():
    """Monitor migration progress in real-time"""
    
    print("=" * 80)
    print("SHAREPOINT MIGRATION - LIVE PROGRESS MONITOR")
    print("=" * 80)
    print()
    
    conn = sqlite3.connect('sharepoint-pilot.sqlite')
    cur = conn.cursor()
    
    last_count = 0
    start_time = datetime.now()
    
    while True:
        try:
            # Get current stats
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
            
            # Calculate progress
            pct = (completed / total * 100) if total > 0 else 0
            elapsed = datetime.now() - start_time
            
            # Clear screen and print status
            print(f"\r[{datetime.now().strftime('%H:%M:%S')}] Progress: {completed}/{total} ({pct:.1f}%) | " + 
                  f"Completed: {completed} | Pending: {pending} | Failed: {failed} | " +
                  f"Elapsed: {int(elapsed.total_seconds())}s", end='', flush=True)
            
            # Check if migration is complete
            if pending == 0 and completed == total:
                print("\n")
                print("=" * 80)
                print("✅ MIGRATION COMPLETE!")
                print(f"  Total items: {total}")
                print(f"  Completed: {completed} ({pct:.1f}%)")
                print(f"  Failed: {failed}")
                print(f"  Total time: {elapsed}")
                print("=" * 80)
                break
            
            time.sleep(5)  # Update every 5 seconds
            
        except KeyboardInterrupt:
            print("\n\n⏹️  Monitoring stopped by user")
            break
        except Exception as e:
            print(f"\n⚠️  Error: {e}")
            time.sleep(5)
    
    conn.close()

if __name__ == "__main__":
    monitor_migration()
