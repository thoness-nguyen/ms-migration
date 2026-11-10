"""Discover source Teams channels with activity since a cutoff date and map
them to existing target channels, producing `workloads.teams.channels` entries
for the Teams plan (config/teams-pilot.yaml).

Read-only on both tenants: it never creates teams or channels. Channels whose
target team/channel can't be matched are written to an `unresolved` list for
the operator to provision (a target channel must be created in migration mode,
with a backdated createdDateTime, before history can be imported into it).

Activity = any root message created/modified, or reply created, on/after the
cutoff (default 2026-01-01).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from migration.common.auth import source_token, target_token
from migration.common.graph import GraphClient, GraphError
from migration.common.env import load_env_file

DEFAULT_OUTPUT = "config/discovery/channels.yaml"
DEFAULT_SINCE = "2026-01-01T00:00:00Z"
TEAM_FILTER = "resourceProvisioningOptions/Any(x:x eq 'Team')"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Teams channels active since a date and map them to target channels.")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="ISO timestamp; channels with activity on/after it are selected.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output YAML file.")
    parser.add_argument("--existing", default=None, help="Existing plan (e.g. config/discovery/legacy-teams-channels.yaml) whose channel entries / target ids are reused.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file.")
    return parser.parse_args()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def list_teams(graph: GraphClient) -> list[dict[str, Any]]:
    return list(graph.pages(f"/groups?$filter={TEAM_FILTER}&$select=id,displayName,mailNickname"))


def list_channels(graph: GraphClient, team_id: str) -> list[dict[str, Any]]:
    return list(graph.pages(f"/teams/{team_id}/channels?$select=id,displayName,membershipType,createdDateTime"))


def last_activity(graph: GraphClient, team_id: str, channel_id: str, since: datetime) -> datetime | None:
    """Most recent activity timestamp found at/after `since`, else None.

    Uses the documented delta filter first (root messages modified after the
    cutoff); falls back to scanning messages with replies expanded."""
    since_iso = since.isoformat().replace("+00:00", "Z")
    stamps: list[datetime] = []
    try:
        page = graph.request("GET", f"/teams/{team_id}/channels/{channel_id}/messages/delta?$filter=lastModifiedDateTime gt {since_iso}&$top=5")
        stamps += [stamp for stamp in (_parse(m.get("lastModifiedDateTime") or m.get("createdDateTime")) for m in (page or {}).get("value", [])) if stamp]
    except GraphError:
        pass
    # Delta only reflects root messages - a 2026 reply under an older post would
    # be missed, so also check the most recent page of posts with their replies.
    page = graph.request("GET", f"/teams/{team_id}/channels/{channel_id}/messages?$top=50&$expand=replies")
    for message in (page or {}).get("value", []):
        candidates = [message.get("createdDateTime"), message.get("lastModifiedDateTime")]
        candidates += [reply.get("createdDateTime") for reply in message.get("replies") or []]
        stamps += [stamp for stamp in map(_parse, candidates) if stamp]
    recent = [stamp for stamp in stamps if stamp >= since]
    return max(recent) if recent else None


def find_target_team(graph: GraphClient, display_name: str, cache: dict[str, str | None]) -> str | None:
    """Target team id by exact display name (case-insensitive), via GET /teams -
    needs only Team.ReadBasic.All (the target app has no Group.Read.All). Errors
    propagate: a permission failure must not be reported as "no matching team"."""
    if not cache:
        teams: dict[str, list[str]] = {}
        for team in graph.pages("/teams?$select=id,displayName"):
            teams.setdefault((team.get("displayName") or "").strip().lower(), []).append(team["id"])
        cache.update({name: ids[0] if len(ids) == 1 else None for name, ids in teams.items()})
        cache["\0loaded"] = None
    return cache.get(display_name.strip().lower())


def load_existing(path: Path | None) -> dict[str, dict[str, Any]]:
    """source_channel_id -> existing channel entry (keeps reviewed target ids)."""
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channels = (data.get("workloads", {}).get("teams", {}) or {}).get("channels", []) or []
    return {entry["source_channel_id"]: entry for entry in channels if isinstance(entry, dict) and entry.get("source_channel_id")}


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.env_file))
    since = _parse(args.since)
    assert since is not None
    source = GraphClient(source_token())
    target = GraphClient(target_token())
    existing = load_existing(Path(args.existing) if args.existing else None)

    # Reuse source team -> target team pairs already established in the plan.
    team_map: dict[str, str] = {entry["source_team_id"]: entry["target_team_id"] for entry in existing.values() if entry.get("target_team_id")}
    target_team_cache: dict[str, str | None] = {}
    target_channel_cache: dict[str, dict[str, str]] = {}

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    inactive = 0
    teams = list_teams(source)
    print(f"Found {len(teams)} source teams. Selecting channels active since {args.since}.")
    for team in teams:
        team_id, team_name = team["id"], team.get("displayName") or team["id"]
        try:
            channels = list_channels(source, team_id)
        except GraphError as error:
            print(f"[!] {team_name}: cannot list channels - {str(error)[:120]}")
            continue
        for channel in channels:
            channel_name = channel.get("displayName") or channel["id"]
            try:
                activity = last_activity(source, team_id, channel["id"], since)
            except GraphError as error:
                unresolved.append({"team": team_name, "channel": channel_name, "source_team_id": team_id, "source_channel_id": channel["id"], "reason": f"cannot read messages: {str(error)[:150]}"})
                continue
            if activity is None:
                inactive += 1
                continue
            print(f"  [active] {team_name} / {channel_name} (last activity {activity:%Y-%m-%d})")
            known = existing.get(channel["id"])
            if known and known.get("target_team_id") and known.get("target_channel_id"):
                entry = {key: known[key] for key in ("name", "source_team_id", "source_channel_id", "target_team_id", "target_channel_id")}
                entry["last_activity"] = activity.strftime("%Y-%m-%d")
                resolved.append(entry)
                continue
            target_team_id = team_map.get(team_id) or find_target_team(target, team_name, target_team_cache)
            target_channel_id = None
            if target_team_id:
                if target_team_id not in target_channel_cache:
                    try:
                        target_channel_cache[target_team_id] = {c.get("displayName", "").strip().lower(): c["id"] for c in list_channels(target, target_team_id)}
                    except GraphError:
                        target_channel_cache[target_team_id] = {}
                target_channel_id = target_channel_cache[target_team_id].get(channel_name.strip().lower())
            base = {"name": channel_name, "source_team_id": team_id, "source_channel_id": channel["id"]}
            if target_team_id and target_channel_id:
                resolved.append({**base, "target_team_id": target_team_id, "target_channel_id": target_channel_id, "last_activity": activity.strftime("%Y-%m-%d")})
            else:
                reason = "no matching target channel" if target_team_id else "no matching target team"
                unresolved.append({**base, "team": team_name, "membership_type": channel.get("membershipType"), "target_team_id": target_team_id, "last_activity": activity.strftime("%Y-%m-%d"), "reason": reason})

    output = {"since": args.since, "channels": resolved, "unresolved": unresolved}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print()
    print(f"Active channels mapped to a target: {len(resolved)}")
    print(f"Active channels needing a target:   {len(unresolved)}")
    print(f"Inactive channels (skipped):        {inactive}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
