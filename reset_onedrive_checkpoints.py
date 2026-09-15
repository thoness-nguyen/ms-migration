from pathlib import Path
import shutil
import sqlite3

db_path = Path("state/onedrive-checkpoint.sqlite")
backup_path = Path("state/onedrive-checkpoint-before-reset.sqlite")

if not db_path.exists():
    raise FileNotFoundError(f"Database not found: {db_path}")

# Create a backup before changing anything.
shutil.copy2(db_path, backup_path)
print(f"Backup created: {backup_path}")

with sqlite3.connect(db_path) as connection:
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE checkpoints
        SET
            status = 'pending',
            detail = ?
        WHERE workload = 'batch-onedrive'
          AND status = 'completed'
        """,
        (
            "Reset after correcting OneDrive batch checkpoint logic",
        ),
    )

    updated_count = cursor.rowcount

    print(
        f"Updated {updated_count} batch-onedrive checkpoint(s)."
    )

    rows = cursor.execute(
        """
        SELECT
            workload,
            status,
            COUNT(*) AS total
        FROM checkpoints
        WHERE workload = 'batch-onedrive'
        GROUP BY workload, status
        ORDER BY status
        """
    ).fetchall()

    print("\nCurrent batch-onedrive status:")
    for workload, status, total in rows:
        print(f"{workload}: {status} = {total}")