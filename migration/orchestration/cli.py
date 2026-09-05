from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ..common.auth import source_token, target_token
from ..common.checkpoint import StateStore, open_state_store
from ..common.graph import GraphClient, GraphError
from ..onedrive.migration import copy_drive
from ..teams.services import extract_chat, import_channel_messages, import_chat
from .batch import load_plan, run_batch


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Resumable Microsoft 365 tenant migration helpers")
    parser.add_argument("--state-dir", default="state", help="Directory holding per-area checkpoint databases (sharepoint/onedrive/teams-checkpoint.sqlite)")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    teams = sub.add_parser("teams-channel")
    teams.add_argument("--team-id", required=True)
    teams.add_argument("--channel-id", required=True)
    teams.add_argument("--messages", type=Path, required=True, help="Extracted channel message JSON array")
    extract = sub.add_parser("extract-chat")
    extract.add_argument("--chat-id", required=True)
    extract.add_argument("--output", type=Path, required=True)
    chat = sub.add_parser("teams-chat")
    chat.add_argument("--bundle", type=Path, required=True, help="Chat bundle produced by extract-chat")
    chat.add_argument("--user-map", type=Path, required=True, help="JSON object mapping source user IDs to target user IDs")
    complete = sub.add_parser("complete-chat")
    complete.add_argument("--chat-id", required=True, help="Target chat ID currently in migration mode")
    batch = sub.add_parser("batch")
    batch.add_argument("--config", type=Path, required=True, help="YAML or JSON batch plan")
    batch.add_argument("--report-dir", type=Path, default=Path("reports"), help="Directory to write <area>-migration-result.json reports into")
    batch.add_argument("--retry-failed-only", action="store_true", help="Skip the full folder-tree walk on libraries/drives that already have a checkpoint and only re-attempt items previously marked failed (SharePoint + OneDrive)")
    drive = sub.add_parser("onedrive")
    drive.add_argument("--source-user", required=True)
    drive.add_argument("--target-user", required=True)
    list_drives = sub.add_parser("list-drives", help="List SharePoint sites and their document library drive IDs")
    list_drives.add_argument("--tenant", choices=["source", "target"], default="source")
    list_drives.add_argument("--test-site", help="Test access to a specific site by ID (e.g., site.sharepoint.com,guid1,guid2)")
    args = parser.parse_args()
    open_stores: dict[str, StateStore] = {}

    def area_state(area: str) -> StateStore:
        if area not in open_stores:
            open_stores[area] = open_state_store(args.state_dir, area)
        return open_stores[area]

    try:
        if args.command == "complete-chat":
            target_client = GraphClient(target_token())
            target_client.request("POST", f"/chats/{args.chat_id}/completeMigration")
            count = 1
        elif args.command == "batch":
            plan = load_plan(args.config)
            source_client = GraphClient(source_token())
            target_client = GraphClient(target_token())
            states = {"sharepoint": area_state("sharepoint"), "onedrive": area_state("onedrive"), "teams": area_state("teams")}
            report = run_batch(source_client, target_client, plan, args.config.parent, states, args.report_dir, args.dry_run, args.retry_failed_only)
            count = sum(len(area_report.get("results", [])) for area_report in report["areas"].values())
        elif args.command == "extract-chat":
            source_client = GraphClient(source_token())
            args.output.write_text(json.dumps(extract_chat(source_client, args.chat_id), indent=2), encoding="utf-8")
            count = 1
        elif args.command == "teams-channel":
            target_client = GraphClient(target_token())
            count = import_channel_messages(target_client, args.team_id, args.channel_id, json.loads(args.messages.read_text(encoding="utf-8")), area_state("teams"), args.dry_run)
        elif args.command == "teams-chat":
            target_client = GraphClient(target_token())
            target_chat_id, count = import_chat(target_client, json.loads(args.bundle.read_text(encoding="utf-8")), json.loads(args.user_map.read_text(encoding="utf-8")), area_state("teams"), args.dry_run)
            print(f"Target chat: {target_chat_id}")
        elif args.command == "list-drives":
            client = GraphClient(source_token() if args.tenant == "source" else target_token())
            rows: list[dict] = []
            if args.test_site:
                try:
                    for drive in client.pages(f"/sites/{args.test_site}/drives?$select=id,name,driveType"):
                        rows.append({"site": args.test_site, "site_id": args.test_site, "drive": drive.get("name"), "drive_id": drive["id"], "type": drive.get("driveType")})
                    if rows:
                        print(f"✓ Site {args.test_site} is accessible")
                    else:
                        print(f"⚠ Site {args.test_site} has no document libraries")
                except GraphError as e:
                    print(f"✗ Site {args.test_site} access failed: {str(e)[:200]}")
                    return 1
            else:
                sites = list(client.pages("/sites/getAllSites?$select=id,displayName,webUrl,isPersonalSite"))
                for site in sites:
                    # Skip personal sites and special sites
                    if site.get("isPersonalSite"):
                        continue
                    
                    site_id = site["id"]
                    site_name = site.get("displayName", "Unknown")
                    
                    try:
                        drives = list(client.pages(f"/sites/{site_id}/drives?$select=id,name,driveType"))
                        if not drives:
                            continue  # No document libraries to migrate
                        
                        for drive in drives:
                            rows.append({"site": site_name, "site_id": site_id, "drive": drive.get("name"), "drive_id": drive["id"], "type": drive.get("driveType")})
                    
                    except GraphError as e:
                        # Distinguish between different error types
                        if hasattr(e, 'status_code') and e.status_code == 403:
                            print(f"⚠ Access denied: {site_name} ({site_id})", file=__import__("sys").stderr)
                        elif hasattr(e, 'status_code') and e.status_code == 404:
                            print(f"⚠ Site not found: {site_name} ({site_id})", file=__import__("sys").stderr)
                        else:
                            print(f"⚠ Error accessing {site_name} ({site_id}): {str(e)[:100]}", file=__import__("sys").stderr)
                        continue
            print(json.dumps(rows, indent=2))
            count = len(rows)
        else:
            source_client = GraphClient(source_token())
            target_client = GraphClient(target_token())
            drive_stats = copy_drive(source_client, target_client, args.source_user, args.target_user, area_state("onedrive"), args.dry_run)
            count = drive_stats.get("files_copied", 0) if isinstance(drive_stats, dict) else drive_stats
    finally:
        for store in open_stores.values():
            store.close()
    print(f"Processed {count} item(s){' (dry run)' if args.dry_run else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
