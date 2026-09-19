from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import yaml

from migration.common.auth import source_token
from migration.common.graph import GraphClient


DEFAULT_INPUT = "config/mapping.example.json"
DEFAULT_OUTPUT = "config/source.chats.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover all Teams chats for users, remove duplicates, "
            "and exclude meeting chats."
        )
    )

    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="JSON file containing user mappings.",
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Output YAML file.",
    )

    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file.",
    )

    return parser.parse_args()


def load_env_file(path: Path) -> None:
    """
    Load variables from .env without modifying auth.py.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Environment file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            # Remove optional surrounding quotes.
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            # Do not overwrite an existing environment variable.
            os.environ.setdefault(key, value)


def validate_environment() -> None:
    required = [
        "SOURCE_TENANT_ID",
        "SOURCE_GRAPH_CLIENT_ID",
        "SOURCE_GRAPH_CLIENT_SECRET",
    ]

    missing = [
        variable
        for variable in required
        if not os.environ.get(variable)
    ]

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


def load_users(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"User mapping file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(
            "Expected the input JSON to contain a list of users."
        )

    return data


def get_user_chats(
    graph: GraphClient,
    source_user_id: str,
) -> list[dict]:
    """
    Get all chats belonging to one source user.
    """
    endpoint = f"/users/{source_user_id}/chats"

    return list(graph.pages(endpoint))


def discover_chats(
    graph: GraphClient,
    users: list[dict],
) -> dict[str, dict]:
    """
    Discover chats for all users.

    Duplicate chats are removed using source_chat_id.

    Meeting chats are excluded using:
        chatType == "meeting"

    Supported chat types:
        oneOnOne
        group
    """
    unique_chats: dict[str, dict] = {}

    total_users = len(users)

    meeting_count = 0
    duplicate_count = 0

    for index, user in enumerate(users, start=1):
        source_user_id = user.get("source_id")
        display_name = user.get("display_name", "Unknown")
        key = user.get("key", "unknown")

        if not source_user_id:
            print(
                f"[{index}/{total_users}] "
                f"{key}: missing source_id, skipped"
            )
            continue

        print(
            f"[{index}/{total_users}] "
            f"{display_name} ({key})"
        )

        try:
            chats = get_user_chats(
                graph=graph,
                source_user_id=source_user_id,
            )
        except Exception as exc:
            print(
                f"  [!] Failed to get chats: {exc}"
            )
            continue

        print(f"  Found {len(chats)} chats")

        for chat in chats:
            chat_id = chat.get("id")

            if not chat_id:
                continue

            chat_type = chat.get("chatType")

            # Exclude meeting chats.
            if chat_type == "meeting":
                meeting_count += 1

                print(
                    f"  [meeting] {chat_id} -> skipped"
                )

                continue

            # Remove duplicate chats.
            if chat_id in unique_chats:
                duplicate_count += 1

                print(
                    f"  [duplicate] {chat_id} -> skipped"
                )

                continue

            unique_chats[chat_id] = {
                "id": chat_id,
                "topic": chat.get("topic"),
                "chat_type": chat_type,
            }

            print(
                f"  [new] {chat_id} "
                f"(type={chat_type})"
            )

    print()
    print("------------------------------")
    print("Discovery statistics")
    print("------------------------------")
    print(f"Unique chats:       {len(unique_chats)}")
    print(f"Duplicate chats:    {duplicate_count}")
    print(f"Meeting chats:      {meeting_count}")
    print("------------------------------")

    return unique_chats


def build_output(
    unique_chats: dict[str, dict],
) -> dict:
    """
    Build the requested YAML structure.

    Names are generic and incremental.
    """
    chats = []

    for index, chat_id in enumerate(
        unique_chats.keys(),
        start=1,
    ):
        chats.append(
            {
                "name": f"chat-{index:03d}",
                "source_chat_id": chat_id,
                "sync_new_messages": true,
                "include_unmapped_senders": true,
            }
        )

    return {
        "chats": chats,
    }


def save_yaml(
    path: Path,
    data: dict,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            data,
            file,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    env_path = Path(args.env_file)

    print(
        f"Loading environment from: {env_path}"
    )

    load_env_file(env_path)
    validate_environment()

    print(
        f"Loading users from: {input_path}"
    )

    users = load_users(input_path)

    print(
        f"Found {len(users)} users."
    )

    print()
    print("Connecting to source Microsoft Graph...")

    graph = GraphClient(source_token())

    print()
    print("Discovering chats...")
    print()

    unique_chats = discover_chats(
        graph=graph,
        users=users,
    )

    output = build_output(unique_chats)

    save_yaml(
        path=output_path,
        data=output,
    )

    print()
    print("==============================")
    print("Chat discovery complete")
    print("==============================")
    print(
        f"Users processed: {len(users)}"
    )
    print(
        f"Unique non-meeting chats: {len(unique_chats)}"
    )
    print(
        f"Output: {output_path}"
    )


if __name__ == "__main__":
    main()