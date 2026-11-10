from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

# Run from the repository root: python -m migration.onedrive.compare_report

from migration.common.auth import source_token, target_token
from migration.common.graph import GraphClient

BYTES_PER_GIB = 1024 ** 3
COLUMNS = [
    "sourceUPN",
    "targetUPN",
    "source_file_count",
    "target_file_count",
    "source_size(GB)",
    "target_size(GB)",
]


def load_mappings(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Mapping JSON must be a list of user objects.")

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Mapping row {i} is not an object.")
        source = str(row.get("source_upn") or "").strip()
        target = str(row.get("target_upn") or "").strip()
        if not source or not target:
            continue
        key = (source.casefold(), target.casefold())
        if key not in seen:
            seen.add(key)
            result.append({"sourceUPN": source, "targetUPN": target})
    if not result:
        raise ValueError(
            "No mappings containing both source_upn and target_upn were found."
        )
    return result


def enumerate_drive(graph: GraphClient, upn: str) -> tuple[int, int]:
    """Recursively enumerate all visible drive items, counting files (not folders)."""
    encoded_upn = quote(upn, safe="")
    root = graph.request(
        "GET",
        f"/users/{encoded_upn}/drive/root?$select=id,name,folder",
    )
    root_id = root.get("id")
    if not root_id:
        raise RuntimeError(f"Could not resolve the drive root for {upn}.")

    count = 0
    total_bytes = 0
    pending_folders = [root_id]
    while pending_folders:
        folder_id = pending_folders.pop()
        path = (
            f"/users/{encoded_upn}/drive/items/{quote(folder_id, safe='')}"
            "/children?$select=id,name,size,file,folder"
        )
        # GraphClient.pages follows @odata.nextLink, so no paging truncation.
        for item in graph.pages(path):
            if item.get("folder") is not None:
                child_id = item.get("id")
                if child_id:
                    pending_folders.append(child_id)
            elif item.get("file") is not None:
                size = item.get("size")
                if size is None:
                    raise RuntimeError(
                        f"Graph returned no file size for {upn}/{item.get('name', '?')}."
                    )
                count += 1
                total_bytes += int(size)
    return count, total_bytes


def size_gb(byte_count: int) -> float:
    # Binary GB/GiB conversion, aligned with the rest of this project.
    return round(byte_count / BYTES_PER_GIB, 3)


def compare_mapping(
    source_graph: GraphClient,
    target_graph: GraphClient,
    mapping: dict[str, str],
) -> tuple[dict[str, Any], str]:
    source_upn = mapping["sourceUPN"]
    target_upn = mapping["targetUPN"]
    row: dict[str, Any] = {
        "sourceUPN": source_upn,
        "targetUPN": target_upn,
        "source_file_count": "",
        "target_file_count": "",
        "source_size(GB)": "",
        "target_size(GB)": "",
    }
    errors: list[str] = []
    try:
        n, b = enumerate_drive(source_graph, source_upn)
        row["source_file_count"] = n
        row["source_size(GB)"] = size_gb(b)
    except Exception as exc:
        errors.append(f"source: {exc}")
    try:
        n, b = enumerate_drive(target_graph, target_upn)
        row["target_file_count"] = n
        row["target_size(GB)"] = size_gb(b)
    except Exception as exc:
        errors.append(f"target: {exc}")
    return row, " | ".join(errors)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Read-only OneDrive file count and size comparison for mapped users."
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="JSON mapping file containing source_upn and target_upn.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/onedrive-compare-report.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Number of user mappings to process concurrently (default: 2).",
    )
    args = parser.parse_args()
    if not args.mapping.is_file():
        print(f"ERROR: Mapping file not found: {args.mapping}", file=sys.stderr)
        return 2
    if args.concurrency < 1:
        print("ERROR: --concurrency must be at least 1.", file=sys.stderr)
        return 2

    try:
        mappings = load_mappings(args.mapping)
        source_graph = GraphClient(source_token())
        target_graph = GraphClient(target_token())
    except Exception as exc:
        print(f"ERROR: Could not initialize report: {exc}", file=sys.stderr)
        return 2

    print(f"Checking {len(mappings)} source/target user mappings (read-only).")
    results: dict[int, dict[str, Any]] = {}
    error_count = 0

    def worker(i: int, mapping: dict[str, str]):
        return i, *compare_mapping(source_graph, target_graph, mapping)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(worker, i, mapping) for i, mapping in enumerate(mappings)]
        for completed, future in enumerate(as_completed(futures), 1):
            i, row, error = future.result()
            results[i] = row
            status = "ERROR" if error else "OK"
            print(
                f"[{completed}/{len(mappings)}] {row['sourceUPN']} -> "
                f"{row['targetUPN']}: {status}"
            )
            if error:
                error_count += 1
                print(f"    {error}", file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i in range(len(mappings)):
            writer.writerow(results[i])

    print(f"Report saved to: {args.output.resolve()}")
    print(f"Total mappings: {len(mappings)}; mappings with errors: {error_count}")
    if error_count:
        print("Blank metrics mean the measurement failed; they do not mean zero files.")
        return 1
    print(
        "Reminder: matching counts and total size are indicators, not proof of "
        "file-by-file integrity. Run the project's OneDrive validation for that."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
