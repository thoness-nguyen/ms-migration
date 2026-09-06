#!/usr/bin/env python3
"""Migration Control Panel - lightweight terminal interface (ADR-0001).

Supports area selection across all migration domains (SharePoint, Teams,
OneDrive) with a consistent start / monitor / report / retry / status
interface. Each area has its own checkpoint database under STATE_DIR
(<area>-checkpoint.sqlite) and its own config file, so a run against one
area can never touch another area's config, checkpoint data, or report.
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

STATE_DIR = os.environ.get("MIGRATION_STATE_DIR", "state")
REPORT_DIR = os.environ.get("MIGRATION_REPORT_DIR", "reports")

CONFIG_FILES = {
    "sharepoint": "config/sharepoint-pilot.yaml",
    "teams": "config/teams-pilot.yaml",
    "onedrive": "config/onedrive-pilot.yaml",
}

AREAS = ("sharepoint", "onedrive", "teams")


def _db_path(area: str) -> str:
    return os.path.join(STATE_DIR, f"{area}-checkpoint.sqlite")

def _config_path(area: str) -> str:
    return os.environ.get(
        "MIGRATION_CONFIG",
        CONFIG_FILES[area],
    )

def _areas_for(area: str) -> list[str]:
    return [area]


def print_menu(area: str | None) -> None:
    print("\n" + "=" * 80)
    print("MIGRATION CONTROL PANEL")
    label = area.upper() if area else "NO AREA SELECTED"
    print(f"Area: {label}")
    print("=" * 80)
    print()
    print("  1. Select migration area (SharePoint / Teams / OneDrive)")
    print("  2. Start migration")
    print("  3. Monitor progress (real-time)")
    print("  4. Show migration status")
    print("  5. Generate failed items report")
    print("  6. Retry failed items (fast - skips full re-scan)")
    print("  7. Validate migrated data (post-migration integrity check)")
    print("  8. Clear batch checkpoint (reset if stuck)")
    print("  9. Exit")
    print()


def select_area() -> str:
    print("\nMigration Area")
    print("  [1] SharePoint")
    print("  [2] Teams")
    print("  [3] OneDrive")
    choice = input("Select area (1-3): ").strip()
    return {"1": "sharepoint", "2": "teams", "3": "onedrive"}.get(choice, "sharepoint")


def start_migration(area: str, retry_failed_only: bool = False) -> None:
    """Start migration for the selected area."""

    config_file = _config_path(area)

    print()
    print(f"🚀 Starting migration ({area})...")
    print(f"   📄 Config: {config_file}")
    print(f"   💾 State:  {_db_path(area)}")

    if area == "onedrive":
        print(
            "   ℹ️  OneDrive runs through the same 'batch' command — "
            "it only processes what's defined under "
            "'workloads.onedrive' in the selected config."
        )

    elif area == "teams":
        print(
            "   ℹ️  Teams runs through the same 'batch' command — "
            "it only processes what's defined under "
            "'workloads.teams' in the selected config."
        )

    elif area == "sharepoint":
        print(
            "   ℹ️  SharePoint runs through the same 'batch' command — "
            "it only processes what's defined under "
            "'workloads.sharepoint' in the selected config."
        )

    cmd = [
        sys.executable,
        "-m",
        "migration.orchestration.cli",
        "--state-dir",
        STATE_DIR,
        "batch",
        "--config",
        config_file,
        "--report-dir",
        REPORT_DIR,
        "--area",
        area,
    ]
    if retry_failed_only:
        cmd.append("--retry-failed-only")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print()
        print(f"❌ Migration failed with exit code {exc.returncode}")
    except KeyboardInterrupt:
        print()
        print("⏹️ Migration stopped by user.")


def monitor_progress(area: str) -> None:
    print("\n📊 Monitoring progress (Ctrl+C to stop)...\n")
    import time
    try:
        while True:
            stats: dict[str, int] = {}
            for a in _areas_for(area):
                path = _db_path(a)
                if not os.path.exists(path):
                    continue
                conn = sqlite3.connect(path)
                cur = conn.cursor()
                cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
                for status, count in cur.fetchall():
                    stats[status] = stats.get(status, 0) + count
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
    try:
        print("\n" + "=" * 80)
        print(f"MIGRATION STATUS ({area})")
        print("=" * 80)
        stats: dict[str, int] = {}
        for a in _areas_for(area):
            path = _db_path(a)
            if not os.path.exists(path):
                continue
            conn = sqlite3.connect(path)
            cur = conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM checkpoints GROUP BY status")
            for status, count in cur.fetchall():
                stats[status] = stats.get(status, 0) + count
            conn.close()
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
    except Exception as e:
        print(f"  ❌ Error: {e}")


def generate_failed_report(area: str) -> None:
    from collections import defaultdict
    from datetime import datetime
    import json

    rows: list[tuple[str, str, str | None]] = []
    for a in _areas_for(area):
        path = _db_path(a)
        if not os.path.exists(path):
            continue
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT workload, source_id, detail FROM checkpoints WHERE status='failed'")
        rows.extend(cur.fetchall())
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

    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"failed-items-report-{area}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
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
    count = 0
    for a in _areas_for(area):
        path = _db_path(a)
        if not os.path.exists(path):
            continue
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM checkpoints WHERE status='failed'")
        count += cur.fetchone()[0]
        conn.close()
    if count == 0:
        print("\n  ✅ No failed items recorded for this area - nothing to retry.\n")
        return
    print(f"\n  🔄 {count} item(s) marked failed for '{area}'. Re-attempting just those (fast path)...\n")
    start_migration(area, retry_failed_only=True)
    print(f"\n  ℹ️  See {REPORT_DIR}/<area>-migration-result.json's 'summary' field, or option 4/5, for the updated status.\n")


def clear_batch_state(area: str) -> None:
    confirm = input("\n⚠️  Clear batch-level checkpoint for this area? (yes/no): ")
    if confirm.lower() != "yes":
        return
    for a in _areas_for(area):
        path = _db_path(a)
        if not os.path.exists(path):
            continue
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("DELETE FROM checkpoints WHERE workload LIKE 'batch-%'")
        conn.commit()
        conn.close()
    print("✓ Batch checkpoint cleared\n")


def validate_migration(area: str) -> None:
    """Option 7: post-migration integrity check - a separate pass from the
    copy itself (size always, content hash optionally). SharePoint's
    file/folder counts are already checked as part of the retry pass
    (option 6), so this is onedrive/teams only.
    """
    if area == "sharepoint":
        print(f"\n  ℹ️  SharePoint validation already runs as part of option 6's retry pass - see {REPORT_DIR}/sharepoint-migration-result.json's 'validation' field.\n")
        return

    config_file = _config_path(area)
    verify_hash = False
    if area == "onedrive":
        answer = input("Also compare content hashes, not just file size? Slower but higher confidence (y/N): ").strip().lower()
        verify_hash = answer in ("y", "yes")

    print(f"\n🔍 Validating migrated {area} data against source...")
    cmd = [
        sys.executable,
        "-m",
        "migration.orchestration.cli",
        "--state-dir",
        STATE_DIR,
        "validate",
        "--config",
        config_file,
        "--area",
        area,
        "--report-dir",
        REPORT_DIR,
    ]
    if verify_hash:
        cmd.append("--hash")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print()
        print(f"❌ Validation failed with exit code {exc.returncode}")
        return
    print(f"\n  ℹ️  See {REPORT_DIR}/{area}-validation-result.json for the full per-item report.\n")


def main() -> None:
    area = "sharepoint"
    while True:
        print_menu(area)
        choice = input("Enter choice (1-9): ").strip()
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
            validate_migration(area)
        elif choice == "8":
            clear_batch_state(area)
        elif choice == "9":
            print("\n✅ Goodbye!\n")
            sys.exit(0)
        else:
            print("\n❌ Invalid choice. Try again.\n")


if __name__ == "__main__":
    main()
