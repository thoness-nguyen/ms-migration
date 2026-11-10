"""Make each migrated group-connected site/team have the same owners and
members as its source, via the user mapping.

Pairs come from config/sharepoint-pilot.yaml (source_site_id -> target_site_id).
Each site's Microsoft 365 group is found from its default library's owner.
Membership is changed on the GROUP, which also drives the connected team's
membership and SharePoint access (owners -> site owners, members -> site members).
Standard channels inherit team membership, so they need nothing extra.

DRY RUN BY DEFAULT. --apply writes. Additive by default; --remove-extra-owners
also removes target owners that are not mapped source owners (e.g. a person
who created the site manually as a placeholder owner).

Skipped:
- the target tenant's own default sites (All Company, root) - --skip-site;
- teams created by provision_targets that are not finalized yet - a team in
  migration mode cannot take members (finalize calls this same logic).

Requires GroupMember.ReadWrite.All (application) on the TARGET app registration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from migration.common.auth import source_token, target_token
from migration.common.graph import GraphClient, GraphError
from migration.common.env import load_env_file

DIRECTORY_OBJECT = "https://graph.microsoft.com/v1.0/directoryObjects/{}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync owners/members of migrated group sites and teams (dry run unless --apply).")
    parser.add_argument("--config", default="config/sharepoint-pilot.yaml")
    parser.add_argument("--mapping", default="config/users.mapping.json")
    parser.add_argument("--provisioned", default="config/discovery/provisioned-teams.yaml", help="provision_targets output - unfinalized teams are skipped.")
    parser.add_argument("--skip-site", action="append", default=["All Company", "Communication site (root)"], help="Site name to leave alone (repeatable).")
    parser.add_argument("--site", action="append", default=[], help="Only these site names (repeatable).")
    parser.add_argument("--remove-extra-owners", action="store_true", help="Also remove target owners who are not mapped source owners.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--env-file", default=".env")
    return parser.parse_args()


def site_group_id(graph: Any, site_id: str) -> str | None:
    try:
        drive = graph.request("GET", f"/sites/{site_id}/drive?$select=owner")
    except GraphError:
        return None
    return (((drive or {}).get("owner") or {}).get("group") or {}).get("id")


def _ids(graph: Any, path: str) -> set[str]:
    return {item["id"] for item in graph.pages(path) if item.get("@odata.type", "#microsoft.graph.user") == "#microsoft.graph.user"}


def sync_group(source: Any, target: Any, source_group: str, target_group: str, user_map: dict[str, str], skipped_users: set[str], apply: bool, remove_extra_owners: bool = False, label: str = "") -> dict[str, Any]:
    src_owners = _ids(source, f"/groups/{source_group}/owners?$select=id")
    src_members = _ids(source, f"/groups/{source_group}/members?$select=id") | src_owners
    unmapped = sorted(u for u in src_members if u not in user_map and u not in skipped_users)
    excluded = sorted(u for u in src_members if u in skipped_users)
    want_owners = {user_map[u] for u in src_owners if u in user_map}
    want_members = {user_map[u] for u in src_members if u in user_map}
    report: dict[str, Any] = {"site": label, "owners": len(want_owners), "members": len(want_members), "unmapped": unmapped, "excluded": excluded,
                              "added_owners": 0, "added_members": 0, "removed_owners": 0, "errors": []}
    if not apply:
        return report
    have_owners = _ids(target, f"/groups/{target_group}/owners?$select=id")
    have_members = _ids(target, f"/groups/{target_group}/members?$select=id")
    for user_id in sorted(want_members - have_members):
        try:
            target.request("POST", f"/groups/{target_group}/members/$ref", json={"@odata.id": DIRECTORY_OBJECT.format(user_id)})
            report["added_members"] += 1
        except GraphError as error:
            if "already exist" not in str(error):
                report["errors"].append(f"member {user_id}: {str(error)[:400]}")
    for user_id in sorted(want_owners - have_owners):
        try:
            target.request("POST", f"/groups/{target_group}/owners/$ref", json={"@odata.id": DIRECTORY_OBJECT.format(user_id)})
            report["added_owners"] += 1
        except GraphError as error:
            if "already exist" not in str(error):
                report["errors"].append(f"owner {user_id}: {str(error)[:400]}")
    if remove_extra_owners and want_owners:
        for user_id in sorted(have_owners - want_owners):
            try:
                target.request("DELETE", f"/groups/{target_group}/owners/{user_id}/$ref")
                report["removed_owners"] += 1
            except GraphError as error:
                report["errors"].append(f"remove owner {user_id}: {str(error)[:120]}")
    return report


def load_user_map(path: str) -> tuple[dict[str, str], set[str]]:
    users = json.loads(Path(path).read_text(encoding="utf-8"))
    user_map = {u["source_id"]: u["target_id"] for u in users if u.get("source_id") and u.get("target_id") and not u.get("skip")}
    skipped = {u["source_id"] for u in users if u.get("source_id") and u.get("skip")}
    return user_map, skipped


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.env_file))
    source, target = GraphClient(source_token()), GraphClient(target_token())
    user_map, skipped_users = load_user_map(args.mapping)
    print(f"=== sync_members - {'APPLY' if args.apply else 'DRY RUN (no changes; pass --apply to write)'} ===")
    provisioned = (yaml.safe_load(Path(args.provisioned).read_text(encoding="utf-8")) or {}) if Path(args.provisioned).exists() else {}
    unfinalized = {t["target_team_id"] for t in provisioned.get("teams", []) if t.get("created_by_script") and not t.get("finalized")}
    skip = {name.lower() for name in args.skip_site}
    only = {name.lower() for name in args.site}
    plan = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    for site in plan["workloads"]["sharepoint"]["sites"]:
        name = site["name"]
        if name.lower() in skip or (only and name.lower() not in only):
            continue
        source_group = site_group_id(source, site["source_site_id"])
        target_group = site_group_id(target, site["target_site_id"])
        if not source_group or not target_group:
            print(f"[skip] {name}: not a group-connected site on {'source' if not source_group else 'target'}")
            continue
        if target_group in unfinalized:
            print(f"[wait] {name}: target team still in migration mode - members are added by provision_targets --finalize")
            continue
        r = sync_group(source, target, source_group, target_group, user_map, skipped_users, args.apply, args.remove_extra_owners, name)
        line = f"[site] {name}: {r['owners']} owner(s), {r['members']} member(s) mapped"
        if args.apply:
            line += f" | added {r['added_owners']} owner(s), {r['added_members']} member(s), removed {r['removed_owners']} owner(s)"
        if r["unmapped"]:
            line += f" | {len(r['unmapped'])} source member(s) without target account"
        if r["excluded"]:
            line += f" | {len(r['excluded'])} excluded (skip: true)"
        print(line)
        for error in r["errors"]:
            print(f"   [!] {error}")


if __name__ == "__main__":
    main()
