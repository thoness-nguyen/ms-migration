"""Provision missing TARGET teams/channels for active source channels, in
Microsoft Graph migration mode, so channel history can be imported with its
original timestamps.

Input: the `unresolved` list written by migration.teams.discover_channels
(default config/discovery/channels.yaml). Output: ready-to-use
`workloads.teams.channels` entries (default config/discovery/provisioned-teams.yaml).

DRY RUN BY DEFAULT - prints what it would create. Writes only with --apply.
Pilot first with --team "<source team display name>".

Phases, run at different times:
1. provision (default): create each missing target team with
   teamCreationMode=migration and the source team's createdDateTime (the
   earliest of the team and its channels), then each missing channel with
   channelCreationMode=migration and the source channel's createdDateTime -
   so historical posts AND replies are accepted. Teams come from active
   channels (--input) and from team-connected SharePoint sites with 2026 file
   activity (--sites-input): creating a team is what creates its SharePoint
   site. Established pairs in --existing (e.g. Harbour PD Work Flow) and
   same-name target teams are reused, so re-runs are idempotent.
2. --map-sites: record each new team's source->target SharePoint site pair
   (target site URLs follow the new team's alias) for config/sharepoint-pilot.yaml.
3. --finalize: after `tenant-migrator batch` has completed every provisioned
   channel of a team, complete the TEAM's migration (until then the team is
   invisible to users) and add its source owners/members via the user mapping.
   Refuses for any team whose channels are not all `completed` in the checkpoint.

Private/shared source channels are NOT provisioned (reported instead):
creating one as a standard channel would expose its content to the whole team.

Requires Teamwork.Migrate.All (+ Team.Create / TeamMember.ReadWrite.All for
finalize) on the TARGET app registration.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from migration.common.auth import source_token, target_token
from migration.common.checkpoint import StateStore
from migration.common.graph import GraphClient, GraphError
from migration.sharepoint.sync_members import load_user_map, sync_group
from migration.common.env import load_env_file

DEFAULT_INPUT = "config/discovery/channels.yaml"
DEFAULT_OUTPUT = "config/discovery/provisioned-teams.yaml"
_TEAM_ID_IN_LOCATION = re.compile(r"teams\('([^']+)'\)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision missing target Teams teams/channels in migration mode (dry run unless --apply).")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="discover_channels output containing `unresolved` channels.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Where provisioned channel plan entries are written.")
    parser.add_argument("--team", action="append", default=[], help="Only this source team display name (repeatable) - use for the pilot.")
    parser.add_argument("--apply", action="store_true", help="Actually create teams/channels (otherwise dry run).")
    parser.add_argument("--finalize", action="store_true", help="Complete team migration + add members for provisioned teams whose channels are all completed.")
    parser.add_argument("--map-sites", action="store_true", help="Record source->target SharePoint site pairs for script-created teams (written with --apply).")
    parser.add_argument("--sites-input", default="config/discovery/sites.yaml", help="discover_sites output; team-connected sites under `unresolved` get their team provisioned.")
    parser.add_argument("--no-site-teams", action="store_true", help="Only provision teams for active channels, not for team sites with file activity.")
    parser.add_argument("--existing", default="config/teams-pilot.yaml", help="Plan whose channel entries give established source->target team pairs to reuse.")
    parser.add_argument("--exclude-team", action="append", default=["Harbour PD Work Flow"], help="Source team/site display name to leave alone (repeatable).")
    parser.add_argument("--mapping", default="config/users.mapping.json", help="User mapping (source_id -> target_id) used by --finalize.")
    parser.add_argument("--state-dir", default="state", help="Checkpoint directory read by --finalize.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file.")
    return parser.parse_args()


def _find_target_team(target: Any, display_name: str) -> str | None:
    """Via GET /teams (Team.ReadBasic.All) - the target app has no Group.Read.All."""
    wanted = display_name.strip().lower()
    matches = [t["id"] for t in target.pages("/teams?$select=id,displayName") if (t.get("displayName") or "").strip().lower() == wanted]
    if len(matches) > 1:
        raise RuntimeError(f"More than one target team is named {display_name!r} - resolve manually")
    return matches[0] if matches else None


def _wait_for_team(target: Any, team_id: str, attempts: int = 30, delay: float = 10.0) -> None:
    """Team creation is asynchronous (202) - wait until the team is readable."""
    for _ in range(attempts):
        try:
            target.request("GET", f"/teams/{team_id}?$select=id")
            return
        except GraphError as error:
            if error.status_code != 404:
                raise
        time.sleep(delay)
    raise RuntimeError(f"Target team {team_id} was not ready after {attempts * delay:.0f}s")


def create_migration_team(target: Any, display_name: str, description: str | None, created: str) -> str:
    payload = {
        "@microsoft.graph.teamCreationMode": "migration",
        "template@odata.bind": "https://graph.microsoft.com/v1.0/teamsTemplates('standard')",
        "displayName": display_name,
        "description": description or display_name,
        "createdDateTime": created,
    }
    # 202 Accepted with an empty body - the new team id is only in the
    # Content-Location / Location header, which GraphClient.request() drops.
    response = target.raw_request("POST", f"{target.base_url}/teams", json=payload, timeout=120)
    if response.status_code not in (201, 202):
        raise GraphError(f"POST /teams failed ({response.status_code}): {response.text[:500]}", status_code=response.status_code)
    location = response.headers.get("Content-Location") or response.headers.get("Location") or ""
    match = _TEAM_ID_IN_LOCATION.search(location)
    if not match:
        raise GraphError(f"POST /teams succeeded but returned no team id (Location={location!r})")
    team_id = match.group(1)
    _wait_for_team(target, team_id)
    return team_id


def create_migration_channel(target: Any, team_id: str, display_name: str, description: str | None, created: str) -> str:
    payload = {
        "@microsoft.graph.channelCreationMode": "migration",
        "displayName": display_name,
        "description": description or "",
        "membershipType": "standard",
        "createdDateTime": created,
    }
    return target.request("POST", f"/teams/{team_id}/channels", json=payload)["id"]


def provision(
    source: Any,
    target: Any,
    unresolved: list[dict[str, Any]],
    apply: bool,
    site_team_ids: list[str] | None = None,
    known_team_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (plan channel entries, skipped-with-reason, team records).

    Teams come from two sources: active channels with no target (`unresolved`),
    and team-connected SharePoint sites with 2026 file activity
    (`site_team_ids`) - creating the team is what creates its site, so a
    site-only team gets just its primary channel."""
    known_team_map = known_team_map or {}
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in unresolved:
        if entry.get("source_team_id") and entry.get("source_channel_id"):
            by_team[entry["source_team_id"]].append(entry)
    for team_id in site_team_ids or []:
        by_team.setdefault(team_id, [])

    channels_out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    teams_out: list[dict[str, Any]] = []
    for source_team_id, entries in by_team.items():
        group = source.request("GET", f"/groups/{source_team_id}?$select=displayName,description,createdDateTime")
        team_name = group.get("displayName") or (entries[0].get("team") if entries else None) or source_team_id
        source_channels = {c["id"]: c for c in source.pages(f"/teams/{source_team_id}/channels?$select=id,displayName,description,membershipType,createdDateTime")}
        # The primary ("General") channel can be renamed, so identify it by id:
        # it maps onto the target team's own primary channel, never a new one.
        source_primary_id = (source.request("GET", f"/teams/{source_team_id}/primaryChannel?$select=id") or {}).get("id")

        wanted = []
        for entry in entries:
            channel = source_channels.get(entry["source_channel_id"])
            if channel is None:
                skipped.append({**entry, "reason": "source channel no longer exists"})
            elif channel.get("membershipType") not in (None, "standard"):
                skipped.append({**entry, "reason": f"{channel.get('membershipType')} channel - not provisioned (would become visible to the whole team as a standard channel)"})
            else:
                wanted.append(channel)
        if entries and not wanted and source_team_id not in (site_team_ids or []):
            continue

        # The team must predate every message imported into any of its channels.
        team_created = min([group.get("createdDateTime")] + [c.get("createdDateTime") for c in wanted if c.get("createdDateTime")])
        # Reuse an established pairing first (e.g. Harbour PD Work Flow ->
        # "Paizes Harbour PD Work Flow", whose names differ), then a same-name team.
        target_team_id = known_team_map.get(source_team_id) or _find_target_team(target, team_name)
        created_here = False
        action = "reuse" if target_team_id else "create"
        reason = f"{len(wanted)} active channel(s)" if wanted else "team site with 2026 file activity"
        print(f"[team] {team_name}: {action} target team (migration mode, createdDateTime {team_created}) - {reason}")
        if not target_team_id and apply:
            target_team_id = create_migration_team(target, team_name, group.get("description"), team_created)
            created_here = True
            print(f"  [+] created target team {target_team_id}")
        if target_team_id:
            teams_out.append({"name": team_name, "source_team_id": source_team_id, "target_team_id": target_team_id, "created_by_script": created_here})

        existing_channels: dict[str, str] = {}
        primary_id = None
        if target_team_id:
            existing_channels = {(c.get("displayName") or "").strip().lower(): c["id"] for c in target.pages(f"/teams/{target_team_id}/channels?$select=id,displayName")}
            primary_id = (target.request("GET", f"/teams/{target_team_id}/primaryChannel?$select=id") or {}).get("id")

        for channel in wanted:
            name = channel.get("displayName") or channel["id"]
            is_primary = channel["id"] == source_primary_id or (source_primary_id is None and name.strip().lower() == "general")
            target_channel_id = primary_id if is_primary else existing_channels.get(name.strip().lower())
            if is_primary and not target_team_id:
                print(f"  [channel] {name}: primary channel - maps to the new team's General channel")
                continue
            if target_channel_id:
                print(f"  [channel] {name}: reuse {target_channel_id}")
            elif not apply:
                print(f"  [channel] {name}: create (migration mode, createdDateTime {channel.get('createdDateTime')})")
            else:
                target_channel_id = create_migration_channel(target, target_team_id, name, channel.get("description"), channel.get("createdDateTime") or team_created)
                print(f"  [+] created channel {name}: {target_channel_id}")
            if target_team_id and target_channel_id:
                channels_out.append({
                    "name": f"{team_name} / {name}",
                    "source_team_id": source_team_id,
                    "source_channel_id": channel["id"],
                    "target_team_id": target_team_id,
                    "target_channel_id": target_channel_id,
                    "sync_new_messages": True,
                    "include_unmapped_senders": True,
                })
    return channels_out, skipped, teams_out


def source_site_teams(source: Any, site_urls: set[str]) -> dict[str, str]:
    """source team id -> its root site URL, for the given (lowercased) site URLs."""
    found: dict[str, str] = {}
    for group in source.pages("/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')&$select=id,displayName"):
        try:
            url = (source.request("GET", f"/groups/{group['id']}/sites/root?$select=webUrl") or {}).get("webUrl", "").rstrip("/").lower()
        except GraphError:
            continue
        if url in site_urls:
            found[group["id"]] = url
    return found


def map_team_sites(source: Any, target: Any, teams: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """SharePoint plan entries pairing each team's source site with its target
    team's site (the target URL follows the target team's alias, so it can differ
    from the source path). Returns (entries, names whose target site isn't ready)."""
    entries: list[dict[str, Any]] = []
    not_ready: list[str] = []
    for team in teams:
        try:
            src = source.request("GET", f"/groups/{team['source_team_id']}/sites/root?$select=id,webUrl")
            tgt = target.request("GET", f"/groups/{team['target_team_id']}/sites/root?$select=id,webUrl")
        except GraphError:
            not_ready.append(team["name"])
            continue
        entries.append({"name": team["name"], "source_site_id": src["id"], "target_site_id": tgt["id"], "source_site_url": src.get("webUrl"), "target_site_url": tgt.get("webUrl")})
    return entries, not_ready


def finalize(source: Any, target: Any, provisioned: list[dict[str, Any]], state: StateStore, user_map: dict[str, str], apply: bool, teams: list[dict[str, Any]] | None = None, skipped_users: set[str] | None = None) -> list[str]:
    """Complete migration of script-created teams whose configured channels are
    all completed (a site-only team has none, so it finalizes immediately), then
    give the team the source team's owners/members (same group-based logic as
    sharepoint.sync_members). Teams that already existed are never touched.
    Returns the target team ids finalized in this call."""
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in provisioned:
        by_team[entry["target_team_id"]].append(entry)
    if teams is None:
        teams = [{"target_team_id": tid, "source_team_id": entries[0]["source_team_id"], "created_by_script": True} for tid, entries in by_team.items()]
    finalized: list[str] = []
    for team in teams:
        if not team.get("created_by_script"):
            continue
        target_team_id = team["target_team_id"]
        entries = by_team.get(target_team_id, [])
        pending = [e["name"] for e in entries if state.status("batch-channel", f"{e['source_team_id']}:{e['source_channel_id']}") != "completed"]
        if pending:
            print(f"[team] {team.get('name', target_team_id)}: NOT finalized - channels not completed yet: {pending}")
            continue
        report = sync_group(source, target, team["source_team_id"], target_team_id, user_map, skipped_users or set(), apply=False)
        print(f"[team] {team.get('name', target_team_id)}: {len(entries)} channel(s) completed - complete team migration, then {report['owners']} owner(s) / {report['members']} member(s) from source"
              + (f" ({len(report['unmapped'])} without target account)" if report["unmapped"] else ""))
        if not apply:
            continue
        try:
            # Graph refuses team completeMigration ("General channel must be
            # finalized before team") while any channel is still in migration
            # mode: General of a site-only team (never imported into), or a
            # channel the batch checkpointed as completed without importing
            # (target already had messages). The checkpoint gate above already
            # proved every configured channel completed.
            for channel in target.pages(f"/teams/{target_team_id}/channels?$select=id,displayName"):
                _complete_migration(target, f"/teams/{target_team_id}/channels/{channel['id']}/completeMigration")
            _complete_migration(target, f"/teams/{target_team_id}/completeMigration")
        except GraphError as error:
            # One team's failure must not stop the remaining teams; it stays
            # unfinalized and is retried on the next --finalize run.
            print(f"  [!] NOT finalized - completeMigration failed: {str(error)[:400]}")
            continue
        finalized.append(target_team_id)
        result = sync_group(source, target, team["source_team_id"], target_team_id, user_map, skipped_users or set(), apply=True, label=team.get("name", ""))
        print(f"  [+] added {result['added_owners']} owner(s), {result['added_members']} member(s)")
        for error in result["errors"]:
            print(f"  [!] {error}")
    return finalized


def _complete_migration(target: Any, path: str) -> None:
    """completeMigration, treating an already-completed / never-in-migration
    thread as done (re-runs and teams finalized before a crash)."""
    try:
        target.request("POST", path)
    except GraphError as error:
        if "already completed migration" not in str(error) and "not in migration" not in str(error):
            raise


def _load_yaml(path: str) -> dict[str, Any]:
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}) if Path(path).exists() else {}


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.env_file))
    source = GraphClient(source_token())
    target = GraphClient(target_token())
    mode = "APPLY" if args.apply else "DRY RUN (no changes; pass --apply to write)"
    phase = "finalize" if args.finalize else "map-sites" if args.map_sites else "provision"
    print(f"=== provision_targets: {phase} - {mode} ===")
    output = _load_yaml(args.output)

    if args.finalize:
        user_map, skipped_users = load_user_map(args.mapping)
        state = StateStore(Path(args.state_dir) / "teams-checkpoint.sqlite")
        try:
            done = finalize(source, target, output.get("channels", []), state, user_map, args.apply, output.get("teams"), skipped_users)
        finally:
            state.close()
        if args.apply and done:
            for team in output.get("teams", []):
                if team["target_team_id"] in done:
                    team["finalized"] = True
            Path(args.output).write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return

    if args.map_sites:
        sites, not_ready = map_team_sites(source, target, [t for t in output.get("teams", []) if t.get("created_by_script")])
        for site in sites:
            print(f"[site] {site['name']}: {site['source_site_url']} -> {site['target_site_url']}")
        for name in not_ready:
            print(f"[site] {name}: target team site not provisioned yet - re-run --map-sites later")
        if args.apply:
            output["sharepoint_sites"] = sites
            Path(args.output).write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return

    excluded = {name.lower() for name in args.exclude_team}
    known_team_map = {e["source_team_id"]: e["target_team_id"] for e in (_load_yaml(args.existing).get("workloads", {}).get("teams", {}) or {}).get("channels", []) if e.get("target_team_id")}
    unresolved = [e for e in _load_yaml(args.input).get("unresolved", []) if (e.get("team") or "").lower() not in excluded]
    site_team_ids: list[str] = []
    if not args.no_site_teams:
        site_urls = {s["source_site_url"].rstrip("/").lower() for s in _load_yaml(args.sites_input).get("unresolved", []) if (s.get("name") or "").lower() not in excluded}
        site_team_ids = list(source_site_teams(source, site_urls))
    if args.team:
        wanted = {name.lower() for name in args.team}
        unresolved = [e for e in unresolved if (e.get("team") or "").lower() in wanted]
        site_team_ids = [tid for tid in site_team_ids if (source.request("GET", f"/groups/{tid}?$select=displayName").get("displayName") or "").lower() in wanted]
    channels, skipped, teams = provision(source, target, unresolved, args.apply, site_team_ids, known_team_map)
    for item in skipped:
        print(f"[skip] {item.get('team')} / {item.get('name')}: {item['reason']}")
    if args.apply:
        known = {e["source_channel_id"] for e in channels}
        output["channels"] = [e for e in output.get("channels", []) if e["source_channel_id"] not in known] + channels
        known_teams = {t["source_team_id"] for t in teams}
        output["teams"] = [t for t in output.get("teams", []) if t["source_team_id"] not in known_teams] + teams
        output["skipped"] = skipped
        Path(args.output).write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"\n{len(teams)} team(s), {len(channels)} channel(s) recorded -> {args.output}. Next: --map-sites --apply, then add channels/sites to the plans.")


if __name__ == "__main__":
    main()
