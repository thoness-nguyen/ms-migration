from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


PILOT_USER_KEY = "thanh.nguyen"


def load_discovery_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError("Discovery output must be a JSON object.")

    return data


def generate_chat_name(
    chat: dict[str, Any],
    index: int,
) -> str:
    topic = (chat.get("topic") or "").strip()

    if topic:
        name = re.sub(
            r"[^a-zA-Z0-9]+",
            "-",
            topic,
        ).strip("-").lower()

        if name:
            return name

    if chat.get("chat_type") == "group":
        return f"group-{index:03d}"

    return f"one-on-one-{index:03d}"


def get_all_chats(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    chats = []

    for chat_type in ("group", "oneOnOne"):
        chats.extend(
            result.get("chats", {}).get(chat_type, [])
        )

    return chats


def chat_contains_user(
    chat: dict[str, Any],
    user_key: str,
) -> bool:
    """
    Check whether the selected user discovered this chat.

    The discovery JSON contains selected_users for each chat.
    """
    for user in chat.get("selected_users", []):
        if user.get("key") == user_key:
            return True

    return False


def resolve_chat_users(
    chat: dict[str, Any],
) -> list[str]:
    """
    Extract user keys from selected_users.

    This uses the selected_users data already stored
    by discover_chats.py.
    """
    user_keys = []

    for user in chat.get("selected_users", []):
        key = user.get("key")

        if key and key not in user_keys:
            user_keys.append(key)

    return user_keys


def convert_to_yaml(
    result: dict[str, Any],
    user_key: str,
) -> dict[str, Any]:
    all_chats = get_all_chats(result)

    selected_chats = [
        chat
        for chat in all_chats
        if chat_contains_user(chat, user_key)
    ]

    selected_chats.sort(
        key=lambda chat: chat.get("source_chat_id", "")
    )

    yaml_chats = []

    for index, chat in enumerate(selected_chats, start=1):
        yaml_chats.append(
            {
                "name": generate_chat_name(chat, index),
                "source_chat_id": chat["source_chat_id"],
                "users": resolve_chat_users(chat),
            }
        )

    return {
        "chats": yaml_chats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Teams discovery JSON to YAML "
            "for one pilot user."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path(
            "reports/teams-discovered-chats.json"
        ),
        help="Discovery JSON file.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "config/teams-chats-pilot.yaml"
        ),
        help="Generated pilot YAML file.",
    )

    parser.add_argument(
        "--user-key",
        default=PILOT_USER_KEY,
        help="Only include chats discovered by this user key.",
    )

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Input file does not exist: {args.input}")
        return 1

    result = load_discovery_json(args.input)

    yaml_result = convert_to_yaml(
        result,
        args.user_key,
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        yaml.safe_dump(
            yaml_result,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )

    print("Pilot conversion completed.")
    print(f"  User key: {args.user_key}")
    print(f"  Chats: {len(yaml_result['chats'])}")
    print(f"  Output: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())