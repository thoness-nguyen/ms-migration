from __future__ import annotations

import argparse
import csv
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

from ..common.auth import target_token
from ..common.graph import GraphClient

UPN_COLUMN = "Owner Principal Name"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_REPORT_PERIOD = "D7"


def read_user_list(input_file: Path) -> list[dict]:
    """Read a reference CSV containing ONLY Owner Principal Name."""
    with input_file.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise RuntimeError(f"CSV has no header: {input_file}")

        fields = [f.strip() for f in reader.fieldnames if f is not None]
        if fields != [UPN_COLUMN]:
            raise RuntimeError(
                "Reference CSV must contain exactly one column:\n"
                f"  {UPN_COLUMN}\n\nFound columns:\n{reader.fieldnames}"
            )

        rows = []
        for row in reader:
            upn = (row.get(UPN_COLUMN) or "").strip()
            if upn:
                rows.append({UPN_COLUMN: upn})
        return rows


def fetch_one_drive_usage_report(token: str, period: str = DEFAULT_REPORT_PERIOD) -> dict[str, dict]:
    """Fetch the TARGET OneDrive usage report once for file/activity metrics."""
    url = f"{GRAPH_BASE_URL}/reports/getOneDriveUsageAccountDetail(period='{period}')"
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/csv"}

    response = requests.get(url, headers=headers, allow_redirects=False, timeout=120)
    if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("Location")
        if not location:
            raise RuntimeError("Graph returned a report redirect without Location header.")
        response = requests.get(location, timeout=120)
        if response.status_code == 401:
            response = requests.get(location, headers=headers, timeout=120)

    response.raise_for_status()
    text = response.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise RuntimeError("Microsoft Graph returned an empty OneDrive usage report.")

    required = [
        UPN_COLUMN,
        "File Count",
        "Active File Count",
        "Last Activity Date",
        "Is Deleted",
    ]
    missing = [c for c in required if c not in reader.fieldnames]
    if missing:
        raise RuntimeError(
            "OneDrive usage report is missing required columns:\n"
            f"{missing}\n\nAvailable columns:\n{reader.fieldnames}"
        )

    lookup = {}
    for row in reader:
        upn = (row.get(UPN_COLUMN) or "").strip()
        if upn:
            lookup[upn.lower()] = row
    return lookup


def bytes_to_gb(value) -> float:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        value = 0
    return round(value / (1024 ** 3), 2)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_number(value) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return value


def get_drive_info(graph: GraphClient, upn: str) -> dict:
    """Get LIVE TARGET OneDrive metadata and quota; never enumerate /children."""
    encoded_upn = quote(upn, safe="")
    path = (
        f"/users/{encoded_upn}/drive"
        "?$select=id,webUrl,driveType,quota"
    )
    drive = graph.request("GET", path)
    quota = drive.get("quota") or {}
    return {
        "id": drive.get("id", ""),
        "webUrl": drive.get("webUrl", ""),
        "driveType": drive.get("driveType", ""),
        "usedBytes": quota.get("used"),
        "totalBytes": quota.get("total"),
    }


def process_user(graph: GraphClient, user_row: dict, usage_lookup: dict[str, dict]) -> dict:
    """Input contributes only UPN; every other value comes from TARGET."""
    upn = (user_row.get(UPN_COLUMN) or "").strip()
    drive = get_drive_info(graph, upn)

    try:
        used_bytes = int(float(drive.get("usedBytes") or 0))
    except (TypeError, ValueError):
        used_bytes = 0
    try:
        allocated_bytes = int(float(drive.get("totalBytes") or 0))
    except (TypeError, ValueError):
        allocated_bytes = 0

    usage = usage_lookup.get(upn.lower())
    if usage:
        file_count = clean_number(usage.get("File Count"))
        active_file_count = clean_number(usage.get("Active File Count"))
        last_activity = (usage.get("Last Activity Date") or "").strip()
        is_deleted = (usage.get("Is Deleted") or "").strip()
        status = "Success"
        error = ""
    else:
        file_count = active_file_count = last_activity = is_deleted = ""
        status = "Partial"
        error = "Live OneDrive found, but no matching row was returned by the TARGET usage report."

    return {
        "Owner Principal Name": upn,
        "Drive Type": drive.get("driveType", ""),
        "OneDrive URL": drive.get("webUrl", ""),
        "Storage Used (Bytes)": used_bytes,
        "Storage Used (GB)": bytes_to_gb(used_bytes),
        "Storage Allocated (Bytes)": allocated_bytes,
        "Storage Allocated (GB)": bytes_to_gb(allocated_bytes),
        "File Count": file_count,
        "Active File Count": active_file_count,
        "Last Activity Date": last_activity,
        "Is Deleted": is_deleted,
        "Checked At": now_iso(),
        "Status": status,
        "Error": error,
    }


def build_failed_result(user_row: dict, error: Exception) -> dict:
    return {
        "Owner Principal Name": (user_row.get(UPN_COLUMN) or "").strip(),
        "Drive Type": "",
        "OneDrive URL": "",
        "Storage Used (Bytes)": "",
        "Storage Used (GB)": "",
        "Storage Allocated (Bytes)": "",
        "Storage Allocated (GB)": "",
        "File Count": "",
        "Active File Count": "",
        "Last Activity Date": "",
        "Is Deleted": "",
        "Checked At": now_iso(),
        "Status": "Failed",
        "Error": str(error),
    }


def process_users(graph: GraphClient, users: list[dict], usage_lookup: dict[str, dict], concurrency: int) -> list[dict]:
    results_by_index = {}
    total = len(users)
    print(f"Processing {total} users with concurrency={concurrency}\n")

    def worker(index, user):
        try:
            return index, process_user(graph, user, usage_lookup)
        except Exception as error:
            return index, build_failed_result(user, error)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, i, user) for i, user in enumerate(users)]
        for completed, future in enumerate(as_completed(futures), 1):
            index, result = future.result()
            results_by_index[index] = result
            upn = result["Owner Principal Name"]
            if result["Status"] == "Success":
                print(f"[{completed}/{total}] {upn} | {result['Storage Used (GB)']} GB | Files: {result['File Count']} | SUCCESS")
            elif result["Status"] == "Partial":
                print(f"[{completed}/{total}] {upn} | {result['Storage Used (GB)']} GB | Files: N/A | PARTIAL")
            else:
                print(f"[{completed}/{total}] {upn} | FAILED | {result['Error']}")

    return [results_by_index[i] for i in range(total)]


def write_results(output_file: Path, results: list[dict]) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "Owner Principal Name", "Drive Type", "OneDrive URL",
        "Storage Used (Bytes)", "Storage Used (GB)",
        "Storage Allocated (Bytes)", "Storage Allocated (GB)",
        "File Count", "Active File Count", "Last Activity Date", "Is Deleted",
        "Checked At", "Status", "Error",
    ]
    with output_file.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(results)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Get live TARGET OneDrive usage from a UPN-only reference CSV.")
    parser.add_argument("--users", type=Path, required=True, help="Reference CSV containing ONLY Owner Principal Name.")
    parser.add_argument("--output", type=Path, required=True, help="Output CSV.")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent live drive checks. Default: 8")
    parser.add_argument("--period", choices=["D7", "D30", "D90", "D180"], default=DEFAULT_REPORT_PERIOD,
                        help="TARGET OneDrive usage-report period. Default: D7")
    args = parser.parse_args()

    if not args.users.exists():
        print(f"ERROR: Input file not found:\n{args.users}")
        return 1
    if args.concurrency < 1:
        print("ERROR: --concurrency must be at least 1.")
        return 1

    try:
        users = read_user_list(args.users)
    except Exception as error:
        print("ERROR: Failed to read reference user list:")
        print(error)
        return 1
    if not users:
        print("ERROR: No users found in reference CSV.")
        return 1

    print("=" * 70)
    print("OneDrive TARGET Current Usage")
    print("=" * 70)
    print(f"Reference   : {args.users}")
    print(f"Output      : {args.output}")
    print(f"Users       : {len(users)}")
    print(f"Concurrency : {args.concurrency}")
    print(f"Report      : {args.period}\n")

    print("Connecting to TARGET tenant...")
    try:
        token = target_token()
        graph = GraphClient(token)
    except Exception as error:
        print("ERROR: Failed to connect to TARGET tenant.")
        print(error)
        return 1
    print("Connected.\n")

    print("Fetching TARGET OneDrive usage report...")
    try:
        usage_lookup = fetch_one_drive_usage_report(token, args.period)
        print(f"Usage report rows : {len(usage_lookup)}")
    except Exception as error:
        usage_lookup = {}
        print("WARNING: Could not fetch TARGET OneDrive usage report.")
        print(error)
        print("Live storage/drive checks will continue; file-count fields will be unavailable.")
    print()

    results = process_users(graph, users, usage_lookup, args.concurrency)
    write_results(args.output, results)

    def number(row, column):
        try:
            return float(row.get(column) or 0)
        except (TypeError, ValueError):
            return 0

    success = sum(r["Status"] == "Success" for r in results)
    partial = sum(r["Status"] == "Partial" for r in results)
    failed = sum(r["Status"] == "Failed" for r in results)
    total_storage = sum(number(r, "Storage Used (GB)") for r in results if r["Status"] != "Failed")
    total_files = sum(int(number(r, "File Count")) for r in results if r["Status"] != "Failed" and str(r.get("File Count") or "").strip())
    total_active = sum(int(number(r, "Active File Count")) for r in results if r["Status"] != "Failed" and str(r.get("Active File Count") or "").strip())

    print("\n" + "=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Users checked       : {len(results)}")
    print(f"Success             : {success}")
    print(f"Partial             : {partial}")
    print(f"Failed              : {failed}")
    print(f"Total storage       : {total_storage:,.2f} GB")
    print(f"Total files         : {total_files:,}")
    print(f"Total active files  : {total_active:,}")
    print("\nOutput:")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
