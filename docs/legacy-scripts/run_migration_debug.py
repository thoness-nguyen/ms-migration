#!/usr/bin/env python3
"""
Run SharePoint Migration with DEBUG mode locally
Includes live progress, error tracking, and detailed reporting
"""
import subprocess
import os
import sys
import json
import yaml
from datetime import datetime

def check_sharepoint_only(config_path='sharepoint-pilot.yaml'):
    """Warn and abort if the config defines workloads other than sharepoint"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    workloads = config.get('workloads', {}) or {}
    other = [w for w in workloads if w != 'sharepoint']
    if other:
        print(f"⚠️  {config_path} defines non-SharePoint workload(s): {', '.join(other)}")
        print("   This script only runs SharePoint. Remove or move them out before continuing.")
        sys.exit(1)

def run_migration_with_debug():
    """Run migration with debug logging and real-time output"""
    
    check_sharepoint_only()
    
    print("=" * 90)
    print("SHAREPOINT MIGRATION - LOCAL DEBUG RUN")
    print("(SharePoint workload ONLY - Teams and OneDrive are NOT processed)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)
    print()
    
    # Set environment variables for debug output
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'  # Unbuffered output for live display
    env['DEBUG'] = '1'  # Enable debug logging if supported
    
    report_path = f'sharepoint-result-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
    
    # Command to run (use the installed console-script entry point, not
    # `python -m tenant_migrator.cli`, which historically no-opped silently)
    cmd = [
        '.venv\\Scripts\\tenant-migrator.exe',
        '--state', 'sharepoint-pilot.sqlite',
        'batch',
        '--config', 'sharepoint-pilot.yaml',
        '--report', report_path,
    ]
    
    print("📋 COMMAND:")
    print(f"  {' '.join(cmd)}")
    print()
    
    print("⚙️  CONFIGURATION:")
    print("  • Workload: SharePoint ONLY (sharepoint-pilot.yaml has no teams/onedrive entries)")
    print("  • File size limit: 5 GB")
    print("  • Timeout: 30 minutes per attempt")
    print("  • Max retries: 3 with exponential backoff")
    print("  • Permission migration: Enabled (everyone can edit)")
    print("  • Output: Live progress + JSON report + Error tracking")
    print()
    
    print("🔄 STARTING MIGRATION...")
    print("-" * 90)
    print()
    
    # Run the migration command
    try:
        result = subprocess.run(cmd, env=env, cwd=os.getcwd())
        exit_code = result.returncode
        
        print()
        print("-" * 90)
        print()
        
        # The subprocess exit code is 0 even when individual libraries/sites
        # failed internally - always inspect the JSON report to know the truth.
        failed_entries = []
        report = None
        if os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
            for entry in report.get('results', []):
                if entry.get('status') == 'failed':
                    failed_entries.append(entry)
                for lib in entry.get('libraries', []):
                    if lib.get('status') == 'failed':
                        failed_entries.append({**lib, 'site': entry.get('site')})
        
        if exit_code != 0:
            print(f"⚠️  MIGRATION PROCESS FINISHED WITH CODE: {exit_code}")
        elif report is None:
            print(f"❌ NO REPORT FILE WAS PRODUCED ({report_path})")
            print("   The process exited with code 0 but did no verifiable work.")
            print("   Do NOT trust this as a success - investigate before retrying.")
        elif not report.get('results'):
            print("⚠️  MIGRATION FINISHED BUT THE REPORT HAS ZERO RESULTS")
            print("   Nothing was actually processed this run - check sharepoint-pilot.yaml.")
        elif failed_entries:
            print("⚠️  MIGRATION FINISHED, BUT SOME LIBRARIES/SITES FAILED:")
            for entry in failed_entries:
                label = entry.get('site') or entry.get('name') or 'unknown'
                print(f"    - {label}: {entry.get('error', 'unknown error')}")
            print()
            print("   Run: python generate_failed_report.py")
            print("   Then re-run this script to retry - it resumes from where it stopped.")
        else:
            print("✅ MIGRATION COMPLETED SUCCESSFULLY (all libraries/sites reported completed)")
        
        print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        sys.exit(1)
    
    print()
    print("=" * 90)
    print("NEXT STEPS:")
    print("  1. Check the JSON report file for summary statistics")
    print("  2. Run: python generate_failed_report.py")
    print("  3. This shows detailed failed items with file paths")
    print("=" * 90)

if __name__ == "__main__":
    run_migration_with_debug()
