#!/usr/bin/env python3
"""Migration Control Panel - lightweight terminal interface (ADR-0001).

Supports area selection across all migration domains (SharePoint, Teams,
OneDrive, or Run All) with a consistent start / monitor / report / retry /
status interface, backed by the shared checkpoint database.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

# Windows consoles/redirected output default to cp1252, which crashes on the
# emoji used below; force UTF-8 so the panel never dies on its own output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

STATE_DB = os.environ.get("MIGRATION_STATE_DB", "sharepoint-pilot.sqlite")
CONFIG_FILE = os.environ.get("MIGRATION_CONFIG", "sharepoint-pilot.yaml")

# Maps each ADR migration area to the checkpoint "workload" prefixes it owns.
AREA_WORKLOADS = {
    "sharepoint": ["sharepoint"],
    "teams": ["teams-channel", "teams-message", "teams-chat", "teams-chat-message"],
    "onedrive": ["onedrive"],
}


def print_menu(area: str | None) -> None:
    print("\n" + "=" * 80)
    print("MIGRATION CONTROL PANEL")
    label = area.upper() if area else "NO AREA SELECTED"
    print(f"Area: {label}")
    print("=" * 80)
    print()
    print("  1. Select migration area (SharePoint / Teams / OneDrive / Run All)")
    print("  2. Start migration")
    print("  3. Monitor progress (real-time)")
    print("  4. Show migration status")
    print("  5. Generate failed items report")
    print("  6. Retry failed items (fast - skips full re-scan, all areas)")
    print("  7. Clear batch checkpoint (reset if stuck)")
    print("  8. Exit")
    print()


def select_area() -> str:
    print("\nMigration Area")
    print("  [1] SharePoint")
    print("  [2] Teams")
    print("  [3] OneDrive")
    print("  [4] Run All")
    choice = input("Select area (1-4): ").strip()
    return {"1": "sharepoint", "2": "teams", "3": "onedrive", "4": "all"}.get(choice, "sharepoint")


def _workloads_for(area: str) -> list[str]:
    if area == "all":
        return [w for workloads in AREA_WORKLOADS.values() for w in workloads]
    return AREA_WORKLOADS.get(area, ["sharepoint"])


def start_migration(area: str, retry_failed_only: bool = False) -> None:
    print(f"\n🚀 Starting migration ({area})...")
    if area != "sharepoint" and area != "all":
        print(f"   ℹ️  {area} runs through the same 'batch' command — it only processes")
        print(f"      what's defined under 'workloads.{area}' in {CONFIG_FILE}.")
    cmd = [
        sys.executable, "-m", "migration.orchestration.cli",
        "--state", STATE_DB,
        "batch", "--config", CONFIG_FILE,
        "--report", "migration-result.json",
    ]
    if retry_failed_only:
        cmd.append("--retry-failed-only")
    subprocess.run(cmd, cwd=os.getcwd())


def monitor_progress(area: str) -> None:
    print("\n📊 Monitoring progress (Ctrl+C to stop)...\n")
    import time
    workloads = _workloads_for(area)
    placeholders = ",".join("?" for _ in workloads)
    try:
        while True:
            conn = sqlite3.connect(STATE_DB)
            cur = conn.cursor()
            cur.execute(f"SELECT status, COUNT(*) FROM checkpoints WHERE workload IN ({placeholders}) GROUP BY status", workloads)
            stats = dict(cur.fetchall())
            conn.close()
            completed = stats.get("completed", 0)
            pending = stats.get("pending", 0) + stats.get("started", 0) + stats.get("created", 0)
            failed = stats.get("failed", 0)
            total = completed + pending + failed
            pct = (completed / total * 100) if total else 0
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] Progress: {completed}/{total} ({pct:.1f}%) | Completed: {completed} | Pending: {pending} | Failed: {failed}")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n⏹️  Monitoring stopped")


def show_status(area: str) -> None:
    workloads = _workloads_for(area)
    placeholders = ",".join("?" for _ in workloads)
    try:
        conn = sqlite3.connect(STATE_DB)
        cur = conn.cursor()
        print("\n" + "=" * 80)
        print(f"MIGRATION STATUS ({area})")
        print("=" * 80)
        cur.execute(f"SELECT status, COUNT(*) FROM checkpoints WHERE workload IN ({placeholders}) GROUP BY status", workloads)
        stats = dict(cur.fetchall())
        completed = stats.get("completed", 0)
        pending = sum(v for k, v in stats.items() if k not in ("completed", "failed"))
        failed = stats.get("failed", 0)
        total = completed + pending + failed
        pct = (completed / total * 100) if total else 0
        print(f"\n  Completed: {completed:,} items")
        print(f"  Pending:   {pending:,} items")
        print(f"  Failed:    {failed:,} items")
        print(f"  ────────────────────────")
        print(f"  Total:     {total:,} items")
        print(f"  Success:   {pct:.1f}%")
        if failed:
            print(f"\n  ⚠️  {failed} items failed - option 5 to see details, option 6 to retry")
        elif pending:
            print(f"\n  ⏳ {pending} items pending - option 2 to continue")
        else:
            print(f"\n  ✅ Migration complete!")
        print("\n" + "=" * 80 + "\n")
        conn.close()
    except Exception as e:
        print(f"  ❌ Error: {e}")


def generate_failed_report(area: str) -> None:
    from collections import defaultdict
    from datetime import datetime
    import json

    workloads = _workloads_for(area)
    placeholders = ",".join("?" for _ in workloads)
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute(f"SELECT workload, source_id, detail FROM checkpoints WHERE status='failed' AND workload IN ({placeholders})", workloads)
    rows = cur.fetchall()
    conn.close()

    print("\n" + "=" * 100)
    print(f"FAILED ITEMS REPORT ({area}) - Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    if not rows:
        print("\n✅ NO FAILED ITEMS\n")
        return

    categories: dict[str, list] = defaultdict(list)
    for workload, source_id, detail in rows:
        key = (detail or "Unknown error")[:60]
        categories[key].append({"workload": workload, "source_id": source_id, "detail": detail})

    print(f"\nTotal Failed: {len(rows)}\n")
    for key, items in categories.items():
        print(f"{key}  (Count: {len(items)})")
        for item in items[:5]:
            print(f"   [{item['workload']}] {item['source_id']}")
        print()

    report_path = f"failed-items-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(), "area": area, "total_failed": len(rows),
                    "failed_items": [{"workload": w, "source_id": s, "detail": d} for w, s, d in rows]}, f, indent=2)
    print(f"Report exported to: {report_path}")


def retry_failed_items(area: str) -> None:
    """Option 6: re-attempt only the items already marked 'failed' in the
    checkpoint for this area, without redoing the (potentially hours-long)
    full folder walk. Applies to SharePoint and OneDrive (both share the
    same tree-walk cost problem); Teams chats/messages already only
    reprocess non-completed items every run, so this is a normal run for
    that area - no separate fast path needed.
    """
    workloads = _workloads_for(area)
    placeholders = ",".join("?" for _ in workloads)
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM checkpoints WHERE status='failed' AND workload IN ({placeholders})", workloads)
    count = cur.fetchone()[0]
    conn.close()
    if count == 0:
        print("\n  ✅ No failed items recorded for this area - nothing to retry.\n")
        return
    print(f"\n  🔄 {count} item(s) marked failed for '{area}'. Re-attempting just those (fast path)...\n")
    start_migration(area, retry_failed_only=True)
    print("\n  ℹ️  See migration-result.json's 'summary' field, or option 4/5, for the updated status.\n")


def clear_batch_state(area: str) -> None:
    confirm = input("\n⚠️  Clear batch-level checkpoint for this area? (yes/no): ")
    if confirm.lower() != "yes":
        return
    batch_workloads = list({f"batch-{w.split('-')[0]}" for w in _workloads_for(area)})
    placeholders = ",".join("?" for _ in batch_workloads)
    conn = sqlite3.connect(STATE_DB)
    cur = conn.cursor()
    cur.execute(f"DELETE FROM checkpoints WHERE workload IN ({placeholders})", batch_workloads)
    conn.commit()
    conn.close()
    print("✓ Batch checkpoint cleared\n")


def main() -> None:
    area = "sharepoint"
    while True:
        print_menu(area)
        choice = input("Enter choice (1-8): ").strip()
        if choice == "1":
            area = select_area()
        elif choice == "2":
            start_migration(area)
        elif choice == "3":
            monitor_progress(area)
        elif choice == "4":
            show_status(area)
        elif choice == "5":
            generate_failed_report(area)
        elif choice == "6":
            retry_failed_items(area)
        elif choice == "7":
            clear_batch_state(area)
        elif choice == "8":
            print("\n✅ Goodbye!\n")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Try again.\n")


if __name__ == "__main__":
    main()
