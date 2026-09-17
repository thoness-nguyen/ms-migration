from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..common.auth import source_token
from ..common.graph import GraphClient, GraphError


def load_users(path: Path) -> list[dict[str, Any]]:
    """Load selected source users from mapping JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("User mapping must be a JSON array.")

    users = []

    for user in data:
        source_id = user.get("source_id")
        source_upn = user.get("source_upn")

        if not source_id or not source_upn:
            continue

        users.append(
            {
                "key": user.get("key"),
                "display_name": user.get("display_name"),
                "source_id": source_id,
                "source_upn": source_upn,
                "target_id": user.get("target_id"),
                "target_upn": user.get("target_upn"),
            }
        )

    return users


def get_user_chats(
    graph: GraphClient,
    source_user_id: str,
) -> list[dict[str, Any]]:
    """
    Retrieve all chats for one source user.

    Pagination is handled by GraphClient.pages().
    """
    path = (
        f"/users/{source_user_id}/chats"
        "?$select=id,chatType,topic,createdDateTime,lastUpdatedDateTime"
    )

    return list(graph.pages(path))


def get_chat_members(
    graph: GraphClient,
    chat_id: str,
) -> list[dict[str, Any]]:
    """Retrieve all members of a source chat."""
    return list(graph.pages(f"/chats/{chat_id}/members"))


def normalize_member(member: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields needed by the discovery output."""
    user = member.get("user") or {}

    return {
        "id": member.get("userId") or user.get("id"),
        "display_name": member.get("displayName"),
        "email": member.get("email"),
        "roles": member.get("roles", []),
    }


def discover_chats(
    graph: GraphClient,
    users: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Discover all chats belonging to at least one selected user.

    Deduplication is performed by source_chat_id.
    """
    selected_user_ids = {
        user["source_id"]
        for user in users
        if user.get("source_id")
    }

    selected_users_by_id = {
        user["source_id"]: user
        for user in users
        if user.get("source_id")
    }

    # source_chat_id -> discovered chat
    chats_by_id: dict[str, dict[str, Any]] = {}

    # Track which selected users discovered each chat.
    discovered_by_user: dict[str, set[str]] = defaultdict(set)

    stats = {
        "selected_users": len(selected_user_ids),
        "users_processed": 0,
        "users_failed": 0,
        "raw_chat_records": 0,
        "unique_chat_ids": 0,
        "group_chats": 0,
        "one_on_one_chats": 0,
        "unsupported_chat_types": 0,
    }

    for index, user in enumerate(users, start=1):
        source_user_id = user["source_id"]
        source_upn = user["source_upn"]

        print(
            f"[{index}/{len(users)}] "
            f"Getting chats for {source_upn}"
        )

        try:
            user_chats = get_user_chats(graph, source_user_id)
        except GraphError as exc:
            stats["users_failed"] += 1
            print(
                f"  [ERROR] Failed to get chats for {source_upn}: {exc}",
                file=sys.stderr,
            )
            continue

        stats["users_processed"] += 1
        stats["raw_chat_records"] += len(user_chats)

        for chat in user_chats:
            source_chat_id = chat.get("id")

            if not source_chat_id:
                continue

            discovered_by_user[source_chat_id].add(source_user_id)

            # Deduplication:
            # The same chat can appear for multiple selected users.
            if source_chat_id in chats_by_id:
                continue

            chat_type = chat.get("chatType")

            if chat_type not in {"group", "oneOnOne"}:
                stats["unsupported_chat_types"] += 1
                continue

            try:
                raw_members = get_chat_members(graph, source_chat_id)
            except GraphError as exc:
                print(
                    f"  [ERROR] Failed to get members for "
                    f"{source_chat_id}: {exc}",
                    file=sys.stderr,
                )
                continue

            members = [
                normalize_member(member)
                for member in raw_members
            ]

            chats_by_id[source_chat_id] = {
                "source_chat_id": source_chat_id,
                "chat_type": chat_type,
                "topic": chat.get("topic"),
                "created_date_time": chat.get("createdDateTime"),
                "last_updated_date_time": chat.get("lastUpdatedDateTime"),
                "members": members,
            }

            if chat_type == "group":
                stats["group_chats"] += 1
            elif chat_type == "oneOnOne":
                stats["one_on_one_chats"] += 1

            print(
                f"  [+] {chat_type}: "
                f"{source_chat_id} "
                f"({len(members)} members)"
            )

    # Add discovery information after all users are processed.
    for source_chat_id, chat in chats_by_id.items():
        discovered_users = discovered_by_user[source_chat_id]

        chat["selected_users"] = [
            selected_users_by_id[user_id]
            for user_id in sorted(discovered_users)
        ]

        chat["selected_user_count"] = len(discovered_users)

        # Whether every member belongs to the selected group.
        member_ids = {
            member["id"]
            for member in chat["members"]
            if member.get("id")
        }

        chat["all_members_selected"] = (
            member_ids.issubset(selected_user_ids)
        )

    stats["unique_chat_ids"] = len(chats_by_id)

    grouped = {
        "group": [],
        "oneOnOne": [],
    }

    for chat in chats_by_id.values():
        grouped[chat["chat_type"]].append(chat)

    # Stable output order.
    for chats in grouped.values():
        chats.sort(key=lambda item: item["source_chat_id"])

    return {
        "selected_users": users,
        "stats": stats,
        "chats": grouped,
    }


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Discover and deduplicate Microsoft Teams chats "
            "for selected source users."
        )
    )

    parser.add_argument(
        "--users",
        type=Path,
        required=True,
        help="User mapping JSON file.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/teams-discovered-chats.json"),
        help="Output JSON file.",
    )

    args = parser.parse_args()

    users = load_users(args.users)

    if not users:
        print("No valid source users found.")
        return 1

    print(f"Selected users: {len(users)}")

    graph = GraphClient(source_token())

    result = discover_chats(graph, users)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    stats = result["stats"]

    print()
    print("Discovery completed.")
    print(f"  Selected users: {stats['selected_users']}")
    print(f"  Users processed: {stats['users_processed']}")
    print(f"  Users failed: {stats['users_failed']}")
    print(f"  Raw chat records: {stats['raw_chat_records']}")
    print(f"  Unique chat IDs: {stats['unique_chat_ids']}")
    print(f"  Group chats: {stats['group_chats']}")
    print(f"  One-on-one chats: {stats['one_on_one_chats']}")
    print(f"  Output: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())