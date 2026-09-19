from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from migration.common.auth import target_token
from migration.common.graph import GraphClient, GraphError


DEFAULT_TEAM_ID = "80c11791-41a0-48ad-b394-063b75595b32"
DEFAULT_CHANNEL_ID = (
    "19:cNGYqU2W46pO2yfhWZt2uyKzmCO4l7CnLPta_PLm0rI1@thread.tacv2"
)
DEFAULT_BACKDATE = "2020-01-01T00:00:00.000Z"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reset a Teams channel migration session and restart it "
            "with a backdated conversationCreationDateTime."
        )
    )

    parser.add_argument(
        "--team-id",
        default=DEFAULT_TEAM_ID,
    )

    parser.add_argument(
        "--channel-id",
        default=DEFAULT_CHANNEL_ID,
    )

    parser.add_argument(
        "--backdate",
        default=DEFAULT_BACKDATE,
        help="conversationCreationDateTime for the new migration session",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the operations without making changes.",
    )

    return parser.parse_args()


def get_channel(
    graph: GraphClient,
    team_id: str,
    channel_id: str,
) -> dict:
    return graph.request(
        "GET",
        f"/teams/{team_id}/channels/{channel_id}",
    )


def print_channel(channel: dict) -> None:
    print(f"      Name:             {channel.get('displayName')}")
    print(f"      Created:          {channel.get('createdDateTime')}")
    print(f"      Original created: {channel.get('originalCreatedDateTime')}")
    print(f"      Migration mode:   {channel.get('migrationMode')}")
    print()


def main() -> int:
    load_dotenv()

    args = parse_args()

    print()
    print("Teams Channel Migration Reset + Backdate")
    print("=" * 70)
    print(f"Team ID:       {args.team_id}")
    print(f"Channel ID:    {args.channel_id}")
    print(f"Backdate to:   {args.backdate}")
    print()

    # ---------------------------------------------------------------
    # Validate environment
    # ---------------------------------------------------------------

    required = [
        "TARGET_TENANT_ID",
        "TARGET_GRAPH_CLIENT_ID",
        "TARGET_GRAPH_CLIENT_SECRET",
    ]

    missing = [name for name in required if not os.getenv(name)]

    if missing:
        print(
            "[!] Missing required environment variable(s): "
            + ", ".join(missing)
        )
        return 1

    graph = GraphClient(target_token())

    # ---------------------------------------------------------------
    # 1. Read current state
    # ---------------------------------------------------------------

    print("[1/5] Reading current channel state...")

    try:
        channel = get_channel(
            graph,
            args.team_id,
            args.channel_id,
        )
    except GraphError as exc:
        print(f"[!] Failed to read channel: {exc}")
        return 1

    print_channel(channel)

    migration_mode = channel.get("migrationMode")

    if migration_mode != "inProgress":
        print(
            f"[!] Expected migrationMode=inProgress, "
            f"but got: {migration_mode!r}"
        )
        print()
        print(
            "This script is intended to reset an existing migration session."
        )
        return 1

    # ---------------------------------------------------------------
    # 2. Complete current migration session
    # ---------------------------------------------------------------

    print("[2/5] Completing current migration session...")
    print()
    print(
        "      POST "
        f"/teams/{args.team_id}/channels/{args.channel_id}"
        "/completeMigration"
    )
    print()

    if args.dry_run:
        print("[DRY RUN] completeMigration would be called.")
    else:
        try:
            graph.request(
                "POST",
                f"/teams/{args.team_id}/channels/"
                f"{args.channel_id}/completeMigration",
            )
        except GraphError as exc:
            print(f"[!] completeMigration failed: {exc}")
            return 1

        print("[+] Current migration session completed.")
        print()

    # ---------------------------------------------------------------
    # 3. Verify migration mode is off
    # ---------------------------------------------------------------

    print("[3/5] Verifying migration mode is off...")

    if args.dry_run:
        print("[DRY RUN] Skipping verification.")
        print()
    else:
        try:
            channel = get_channel(
                graph,
                args.team_id,
                args.channel_id,
            )
        except GraphError as exc:
            print(f"[!] Failed to verify channel: {exc}")
            return 1

        print_channel(channel)

        migration_mode = channel.get("migrationMode")

        if migration_mode != "completed":
            print(
                "[!] Migration mode was not completed successfully."
            )
            print(
                f"    Current migrationMode: {migration_mode!r}"
            )
            return 1

        print("[+] Migration mode is now completed/off.")
        print()

    # ---------------------------------------------------------------
    # 4. Start a NEW migration session with backdated timestamp
    # ---------------------------------------------------------------

    print("[4/5] Starting new migration session...")
    print()
    print(
        "      POST "
        f"/teams/{args.team_id}/channels/{args.channel_id}"
        "/startMigration"
    )
    print()
    print("      Body:")
    print(
        f'      {{"conversationCreationDateTime": '
        f'"{args.backdate}"}}'
    )
    print()

    if args.dry_run:
        print("[DRY RUN] startMigration would be called.")
        return 0

    try:
        graph.request(
            "POST",
            f"/teams/{args.team_id}/channels/"
            f"{args.channel_id}/startMigration",
            json={
                "conversationCreationDateTime": args.backdate,
            },
        )
    except GraphError as exc:
        print(f"[!] startMigration failed: {exc}")
        return 1

    print("[+] New migration session started.")
    print()

    # ---------------------------------------------------------------
    # 5. Final verification
    # ---------------------------------------------------------------

    print("[5/5] Final verification...")

    try:
        channel = get_channel(
            graph,
            args.team_id,
            args.channel_id,
        )
    except GraphError as exc:
        print(f"[!] Failed to verify final channel state: {exc}")
        return 1

    print_channel(channel)

    final_mode = channel.get("migrationMode")
    final_created = channel.get("createdDateTime")

    if final_mode != "inProgress":
        print(
            "[!] ERROR: channel is not in migration mode."
        )
        return 1

    print("[+] SUCCESS")
    print()
    print("The channel is ready for historical message import.")
    print(f"Migration mode: {final_mode}")
    print(f"Effective createdDateTime: {final_created}")
    print()
    print("Next step:")
    print("  Run your normal Teams channel migration.")
    print()
    print("Do NOT call completeMigration manually now.")
    print("Your normal migration will complete it after all messages")
    print("have been successfully imported.")

    return 0


if __name__ == "__main__":
    sys.exit(main())