#!/usr/bin/env python3
import json

try:
    with open('sharepoint-pilot-result-after-permissions.json') as f:
        report = json.load(f)
    
    result = report['results'][0]
    lib_result = result.get('libraries', [{}])[0] if result.get('libraries') else {}
    
    stats = lib_result.get('stats', {})
    
    print("\n" + "=" * 80)
    print("RETRY RESULTS - AFTER ADMIN CONSENT GRANTED")
    print("=" * 80)
    
    print(f"\nSite: {result['site']}")
    print(f"Status: {result['status'].upper()}")
    
    if stats:
        print("\n[RETRY STATISTICS - THIS RUN]")
        files = stats.get('files_copied', 0)
        folders = stats.get('folders_created', 0)
        failed = stats.get('failed', 0)
        
        print(f"  Files copied:        {files:>4}")
        print(f"  Folders created:     {folders:>4}")
        print(f"  Failed items:        {failed:>4}")
        print(f"  " + "─" * 60)
        total_this_run = files + folders
        print(f"  Total this run:      {total_this_run:>4} items")
        
        if failed > 0:
            errors = stats.get('errors', [])
            print(f"\n[ERRORS] {len(errors)} items")
            for i, err in enumerate(errors[:5], 1):
                item = str(err.get('item', 'unknown'))[:45]
                err_msg = str(err.get('error', ''))[:60]
                print(f"  {i}. {item}...")
                print(f"     {err_msg}...")
            if len(errors) > 5:
                print(f"  ... and {len(errors)-5} more")
    
    # Check database for cumulative stats
    import sqlite3
    conn = sqlite3.connect('sharepoint-pilot.sqlite')
    cur = conn.cursor()
    
    cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
    status_dist = {status: count for status, count in cur.fetchall()}
    
    print("\n[CUMULATIVE DATABASE STATE]")
    print(f"  Completed items:     {status_dist.get('completed', 0)}")
    print(f"  Pending items:       {status_dist.get('pending', 0)}")
    print(f"  Total tracked:       {sum(status_dist.values())}")
    
    # Calculate success rate
    completed = status_dist.get('completed', 0)
    total = sum(status_dist.values())
    success_rate = (completed / total * 100) if total > 0 else 0
    
    print(f"\n[SUCCESS RATE]")
    print(f"  {success_rate:.1f}% ({completed}/{total} items)")
    
    conn.close()
    
    print("\n" + "=" * 80)
    if failed == 0 or failed < 20:
        print("[SUCCESS] Most/all previously-failed items now migrated! ✓")
    else:
        print(f"[PARTIAL] {total_this_run} items fixed, {failed} still failing")
    print("=" * 80)
    
except FileNotFoundError:
    print("[ERROR] Report file not found. Was migration executed?")
except Exception as e:
    print(f"[ERROR] {e}")
