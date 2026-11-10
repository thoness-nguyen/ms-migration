"""Discover source SharePoint sites whose document libraries changed since a
cutoff date, and map each to the SAME site path on the target tenant, producing
`workloads.sharepoint.sites` entries for config/sharepoint-pilot.yaml.

Read-only on both tenants: it never creates sites or libraries. The batch maps
libraries by NAME (see orchestration.batch._run_batch_sharepoint), so for each
selected site this also reports source libraries with no same-named target
library - those would otherwise be skipped as "target library not found".

Activity = a document library (list) lastModifiedDateTime on/after the cutoff.
Personal OneDrive sites are excluded (migrated by the onedrive workload).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from migration.common.auth import source_token, target_token
from migration.common.graph import GraphClient, GraphError
from migration.sharepoint.services import get_site_libraries
from migration.common.env import load_env_file

DEFAULT_OUTPUT = "config/discovery/sites.yaml"
DEFAULT_SINCE = "2026-01-01T00:00:00Z"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover SharePoint sites active since a date and map them to the same target site path.")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="ISO timestamp; sites with library activity on/after it are selected.")
    parser.add_argument("--target-host", required=True, help="Target SharePoint host, e.g. paizesoffice.sharepoint.com")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output YAML file.")
    parser.add_argument("--existing", default=None, help="Existing plan (e.g. config/sharepoint-pilot.yaml) whose reviewed source->target site pairs are reused as-is.")
    parser.add_argument("--env-file", default=".env", help="Path to .env file.")
    return parser.parse_args()


# Every tenant has these; a same-path match is not evidence that the target site
# is the intended destination (it is the target organization's own site).
_TENANT_DEFAULT_PATHS = {"", "/sites/allcompany", "/search", "/sites/contenttypehub"}


def load_existing(path: Path | None) -> dict[str, dict[str, Any]]:
    """source_site_id -> existing reviewed site entry."""
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites = (data.get("workloads", {}).get("sharepoint", {}) or {}).get("sites", []) or []
    return {entry["source_site_id"]: entry for entry in sites if isinstance(entry, dict) and entry.get("source_site_id")}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def list_sites(graph: GraphClient) -> list[dict[str, Any]]:
    select = "$select=id,webUrl,displayName,name,lastModifiedDateTime,isPersonalSite"
    try:
        return list(graph.pages(f"/sites/getAllSites?{select}"))
    except GraphError:
        # getAllSites unavailable for this app - search covers non-personal sites.
        return list(graph.pages(f"/sites?search=*&{select}"))


def library_activity(graph: GraphClient, site_id: str) -> tuple[datetime | None, list[str]]:
    """(latest document-library change, library names) for one site."""
    latest: datetime | None = None
    names: list[str] = []
    for lst in graph.pages(f"/sites/{site_id}/lists?$select=id,displayName,lastModifiedDateTime,list"):
        info = lst.get("list") or {}
        if info.get("template") != "documentLibrary" or info.get("hidden"):
            continue
        names.append(lst.get("displayName") or "")
        stamp = _parse(lst.get("lastModifiedDateTime"))
        if stamp and (latest is None or stamp > latest):
            latest = stamp
    return latest, names


def main() -> None:
    args = parse_args()
    load_env_file(Path(args.env_file))
    since = _parse(args.since)
    assert since is not None
    source = GraphClient(source_token())
    target = GraphClient(target_token())
    existing = load_existing(Path(args.existing) if args.existing else None)

    selected: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    inactive = 0
    sites = list_sites(source)
    print(f"Found {len(sites)} source sites. Selecting sites with library activity since {args.since}.")
    for site in sites:
        web_url = site.get("webUrl") or ""
        parsed = urlparse(web_url)
        if site.get("isPersonalSite") or "-my.sharepoint.com" in parsed.netloc or "/personal/" in parsed.path:
            continue
        try:
            activity, _ = library_activity(source, site["id"])
        except GraphError as error:
            unresolved.append({"name": site.get("displayName"), "source_site_url": web_url, "reason": f"cannot read libraries: {str(error)[:150]}"})
            continue
        if activity is None or activity < since:
            inactive += 1
            continue
        name = site.get("displayName") or site.get("name") or web_url
        path = parsed.path.rstrip("/")
        target_url = f"https://{args.target_host}{path}"
        print(f"  [active] {name}  {web_url}  (last library change {activity:%Y-%m-%d})")
        known = existing.get(site["id"])
        try:
            if known and known.get("target_site_id"):
                target_site = target.request("GET", f"/sites/{known['target_site_id']}")
            else:
                target_site = target.request("GET", f"/sites/{args.target_host}:{path or '/'}" if path else f"/sites/{args.target_host}")
        except GraphError:
            unresolved.append({"name": name, "source_site_id": site["id"], "source_site_url": web_url, "target_site_url": target_url, "last_activity": activity.strftime("%Y-%m-%d"), "reason": "target site with the same path does not exist"})
            continue
        source_libraries = {lib.get("name") for lib in get_site_libraries(source, site["id"])}
        target_libraries = {lib.get("name") for lib in get_site_libraries(target, target_site["id"])}
        entry = {
            "name": name,
            "source_site_id": site["id"],
            "target_site_id": target_site["id"],
            "source_site_url": web_url,
            "target_site_url": target_site.get("webUrl") or target_url,
            "last_activity": activity.strftime("%Y-%m-%d"),
        }
        missing = sorted(filter(None, source_libraries - target_libraries))
        if missing:
            entry["libraries_missing_on_target"] = missing
        if known:
            entry["name"] = known.get("name", name)
            selected.append(entry)
        elif path.lower() in _TENANT_DEFAULT_PATHS:
            entry["reason"] = "same-path match is the target tenant's own default site - confirm before merging into it"
            needs_review.append(entry)
        else:
            selected.append(entry)

    output = {"since": args.since, "sites": selected, "needs_review": needs_review, "unresolved": unresolved}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(yaml.safe_dump(output, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print()
    print(f"Active sites with a matching target site: {len(selected)}")
    print(f"Active sites matched to a tenant default: {len(needs_review)} (review before adding)")
    print(f"Active sites needing a target site:       {len(unresolved)}")
    print(f"Inactive sites (skipped):                 {inactive}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
