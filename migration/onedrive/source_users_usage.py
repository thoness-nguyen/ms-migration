from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ..common.auth import source_token
from ..common.graph import GraphClient, GraphError


# ============================================================
# Constants
# ============================================================

USAGE_COLUMN = "Storage Used (Byte)"
UPN_COLUMN = "Owner Principal Name"


# ============================================================
# Read OneDrive usage report
# ============================================================

def read_usage_report(input_file: Path) -> list[dict]:
    """
    Read the Microsoft 365 OneDrive usage report.

    Expected columns include:

        Owner Principal Name
        Storage Used (Byte)
        Storage Allocated (Byte)
        File Count
        Active File Count
        Site URL
        Site Id
        ...
    """

    with input_file.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise RuntimeError(
                f"CSV has no header: {input_file}"
            )

        required_columns = [
            UPN_COLUMN,
            USAGE_COLUMN,
        ]

        missing = [
            column
            for column in required_columns
            if column not in reader.fieldnames
        ]

        if missing:
            raise RuntimeError(
                "CSV is missing required columns:\n"
                f"{missing}\n\n"
                f"Available columns:\n"
                f"{reader.fieldnames}"
            )

        rows = []

        for row in reader:

            upn = (row.get(UPN_COLUMN) or "").strip()

            if not upn:
                continue

            rows.append(row)

    return rows


# ============================================================
# Build usage lookup
# ============================================================

def build_usage_lookup(rows: list[dict]) -> dict[str, dict]:
    """
    Build:

        UPN -> usage report row

    Matching is case-insensitive.
    """

    lookup = {}

    for row in rows:

        upn = (
            row.get(UPN_COLUMN) or ""
        ).strip()

        if not upn:
            continue

        lookup[upn.lower()] = row

    return lookup


# ============================================================
# Convert bytes to GB
# ============================================================

def bytes_to_gb(value) -> float:
    """
    Convert bytes to GiB.
    """

    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0

    return round(
        value / (1024 ** 3),
        2,
    )


# ============================================================
# Get current OneDrive information
# ============================================================

def get_drive_info(
    graph: GraphClient,
    upn: str,
) -> dict:
    """
    Verify that the user's OneDrive exists.

    Uses:

        GET /users/{UPN}/drive?$select=id,webUrl,driveType
    """

    from urllib.parse import quote

    encoded_upn = quote(
        upn,
        safe="",
    )

    path = (
        f"/users/{encoded_upn}/drive"
        "?$select=id,webUrl,driveType"
    )

    drive = graph.request(
        "GET",
        path,
    )

    return {
        "id": drive.get("id", ""),
        "webUrl": drive.get("webUrl", ""),
        "driveType": drive.get("driveType", ""),
    }


# ============================================================
# Process one user
# ============================================================

def process_user(
    graph: GraphClient,
    usage_row: dict,
) -> dict:

    upn = (
        usage_row.get(UPN_COLUMN) or ""
    ).strip()

    # --------------------------------------------------------
    # Storage from Microsoft 365 usage report
    # --------------------------------------------------------

    used_bytes = float(
        usage_row.get(USAGE_COLUMN) or 0
    )

    allocated_bytes = float(
        usage_row.get(
            "Storage Allocated (Byte)"
        ) or 0
    )

    file_count = usage_row.get(
        "File Count",
        "",
    )

    active_file_count = usage_row.get(
        "Active File Count",
        "",
    )

    last_activity_date = usage_row.get(
        "Last Activity Date",
        "",
    )

    is_deleted = usage_row.get(
        "Is Deleted",
        "",
    )

    # --------------------------------------------------------
    # Verify OneDrive through Graph
    # --------------------------------------------------------

    drive_info = get_drive_info(
        graph,
        upn,
    )

    return {
        "Owner Principal Name": upn,

        "Drive Type": drive_info.get(
            "driveType",
            "",
        ),

        "OneDrive URL": drive_info.get(
            "webUrl",
            "",
        ),

        "Storage Used (Bytes)": int(
            used_bytes
        ),

        "Storage Used (GB)": bytes_to_gb(
            used_bytes
        ),

        "Storage Allocated (Bytes)": int(
            allocated_bytes
        ),

        "Storage Allocated (GB)": bytes_to_gb(
            allocated_bytes
        ),

        "File Count": file_count,

        "Active File Count": active_file_count,

        "Last Activity Date": last_activity_date,

        "Is Deleted": is_deleted,

        "Checked At": datetime.now()
        .astimezone()
        .isoformat(
            timespec="seconds"
        ),

        "Status": "Success",

        "Error": "",
    }


# ============================================================
# Write results
# ============================================================

def write_results(
    output_file: Path,
    results: list[dict],
) -> None:

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = [
        "Owner Principal Name",
        "Drive Type",
        "OneDrive URL",

        "Storage Used (Bytes)",
        "Storage Used (GB)",

        "Storage Allocated (Bytes)",
        "Storage Allocated (GB)",

        "File Count",
        "Active File Count",

        "Last Activity Date",
        "Is Deleted",

        "Checked At",
        "Status",
        "Error",
    ]

    with output_file.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=columns,
        )

        writer.writeheader()

        writer.writerows(results)


# ============================================================
# Main
# ============================================================

def main() -> int:

    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Get current OneDrive storage usage "
            "for source-tenant users."
        )
    )

    parser.add_argument(
        "--users",
        type=Path,
        required=True,
        help=(
            "Source Microsoft 365 OneDrive "
            "usage report CSV"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output CSV",
    )

    args = parser.parse_args()

    # ========================================================
    # 1. Validate input
    # ========================================================

    if not args.users.exists():

        print(
            "ERROR: Input file not found:\n"
            f"{args.users}"
        )

        return 1

    # ========================================================
    # 2. Read usage report
    # ========================================================

    try:

        usage_rows = read_usage_report(
            args.users
        )

    except Exception as error:

        print(
            "ERROR: Failed to read usage report:"
        )

        print(error)

        return 1

    if not usage_rows:

        print(
            "ERROR: No users found "
            "in usage report."
        )

        return 1

    print("=" * 70)
    print("OneDrive SOURCE Current Usage")
    print("=" * 70)

    print(
        f"Input : {args.users}"
    )

    print(
        f"Output: {args.output}"
    )

    print(
        f"Users : {len(usage_rows)}"
    )

    print()

    # ========================================================
    # 3. Create SOURCE Graph client
    # ========================================================

    print(
        "Connecting to SOURCE tenant..."
    )

    try:

        graph = GraphClient(
            source_token()
        )

    except Exception as error:

        print(
            "ERROR: Failed to connect "
            "to SOURCE tenant."
        )

        print(error)

        return 1

    print("Connected.")
    print()

    # ========================================================
    # 4. Process users
    # ========================================================

    results = []

    for index, usage_row in enumerate(
        usage_rows,
        start=1,
    ):

        upn = (
            usage_row.get(
                UPN_COLUMN
            ) or ""
        ).strip()

        print(
            f"[{index}/{len(usage_rows)}] "
            f"{upn}"
        )

        try:

            result = process_user(
                graph,
                usage_row,
            )

            results.append(result)

            print(
                f"    Storage: "
                f"{result['Storage Used (GB)']} GB"
            )

            print(
                f"    Files: "
                f"{result['File Count']}"
            )

            print(
                f"    Status: SUCCESS"
            )

        except GraphError as error:

            print(
                "    Status: FAILED"
            )

            print(
                f"    Error: {error}"
            )

            results.append(
                {
                    "Owner Principal Name": upn,
                    "Drive Type": "",
                    "OneDrive URL": "",

                    "Storage Used (Bytes)": "",
                    "Storage Used (GB)": "",

                    "Storage Allocated (Bytes)": "",
                    "Storage Allocated (GB)": "",

                    "File Count": "",
                    "Active File Count": "",

                    "Last Activity Date": (
                        usage_row.get(
                            "Last Activity Date",
                            "",
                        )
                    ),

                    "Is Deleted": (
                        usage_row.get(
                            "Is Deleted",
                            "",
                        )
                    ),

                    "Checked At": datetime.now()
                    .astimezone()
                    .isoformat(
                        timespec="seconds"
                    ),

                    "Status": "Failed",

                    "Error": str(error),
                }
            )

        except Exception as error:

            print(
                "    Status: FAILED"
            )

            print(
                f"    Error: {error}"
            )

            results.append(
                {
                    "Owner Principal Name": upn,
                    "Drive Type": "",
                    "OneDrive URL": "",

                    "Storage Used (Bytes)": "",
                    "Storage Used (GB)": "",

                    "Storage Allocated (Bytes)": "",
                    "Storage Allocated (GB)": "",

                    "File Count": "",
                    "Active File Count": "",

                    "Last Activity Date": (
                        usage_row.get(
                            "Last Activity Date",
                            "",
                        )
                    ),

                    "Is Deleted": (
                        usage_row.get(
                            "Is Deleted",
                            "",
                        )
                    ),

                    "Checked At": datetime.now()
                    .astimezone()
                    .isoformat(
                        timespec="seconds"
                    ),

                    "Status": "Failed",

                    "Error": str(error),
                }
            )

    # ========================================================
    # 5. Write output
    # ========================================================

    write_results(
        args.output,
        results,
    )

    # ========================================================
    # 6. Summary
    # ========================================================

    success_count = sum(
        1
        for row in results
        if row["Status"] == "Success"
    )

    failed_count = (
        len(results)
        - success_count
    )

    total_storage = sum(
        float(
            row["Storage Used (GB)"] or 0
        )
        for row in results
        if row["Status"] == "Success"
    )

    total_files = sum(
        int(
            float(
                row["File Count"] or 0
            )
        )
        for row in results
        if row["Status"] == "Success"
    )

    print()
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)

    print(
        f"Users checked : {len(results)}"
    )

    print(
        f"Success       : {success_count}"
    )

    print(
        f"Failed        : {failed_count}"
    )

    print(
        f"Total storage : "
        f"{total_storage:,.2f} GB"
    )

    print(
        f"Total files   : "
        f"{total_files:,}"
    )

    print()

    print("Output:")
    print(args.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )