from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..common.graph import GraphClient, GraphError

# ============================================================================
# URL RESOLUTION AND SITE/LIBRARY DISCOVERY
# ============================================================================

def resolve_site_url(graph_client: GraphClient, site_url: str) -> str:
    """Resolve a SharePoint site URL to its site ID.
    
    Args:
        graph_client: Authenticated Graph API client
        site_url: Full site URL (e.g., https://tenant.sharepoint.com/sites/SiteName)
                  OR site ID (e.g., "tenant.sharepoint.com,guid1,guid2")
        
    Returns:
        Site ID (e.g., "tenant.sharepoint.com,guid1,guid2")
        
    Raises:
        GraphError: If site not found
    """
    from urllib.parse import urlparse
    
    # If already a site ID (contains commas), return as-is
    if "," in site_url and not site_url.startswith("http"):
        return site_url
    
    parsed = urlparse(site_url)
    host = parsed.netloc  # e.g., "harbouroutdoorasialimited.sharepoint.com"
    path = parsed.path.lstrip("/")  # e.g., "sites/HarbourPD"
    
    if not host or not path:
        raise ValueError(f"Invalid site URL: {site_url}")
    
    try:
        response = graph_client.request("GET", f"/sites/{host}:/{path}?$select=id")
        return response["id"]
    except GraphError as e:
        error_msg = f"Cannot resolve site URL {site_url}: {e!s}\n\n" \
                    f"Alternative: Use site ID directly (format: 'tenant.sharepoint.com,guid1,guid2')\n" \
                    f"Get it from: tenant-migrator list-drives --tenant source"
        raise GraphError(error_msg)

def encode_graph_path_segment(value: str) -> str:
    """
    URL-encode a single Microsoft Graph path segment.

    Use this for user-controlled/resource names such as:
    - file names
    - folder names

    Example:
        HONGTAI #PO.xlsx
        -> HONGTAI%20%23PO.xlsx
    """
    return quote(str(value), safe="")

def discover_sites(graph_client: GraphClient, exclude_personal: bool = True) -> list[dict[str, Any]]:
    """Discover all SharePoint sites in tenant.
    
    Args:
        graph_client: Authenticated Graph API client
        exclude_personal: Skip personal/OneDrive sites (default: True)
        
    Returns:
        List of site objects with id, displayName, webUrl
    """
    sites = []
    try:
        for site in graph_client.pages("/sites/getAllSites?$select=id,displayName,webUrl,isPersonalSite"):
            if exclude_personal and site.get("isPersonalSite"):
                continue
            sites.append(site)
    except GraphError as e:
        print(f"✗ Failed to retrieve sites: {e}")
        raise
    return sites


def get_site_libraries(graph_client: GraphClient, site_id: str) -> list[dict[str, Any]]:
    """Get all document libraries in a site.
    
    Args:
        graph_client: Authenticated Graph API client
        site_id: SharePoint site ID
        
    Returns:
        List of drive objects with id, name, driveType
    """
    drives = []
    try:
        for drive in graph_client.pages(f"/sites/{site_id}/drives?$select=id,name,driveType,webUrl"):
            if drive.get("driveType") == "documentLibrary":
                drives.append(drive)
    except GraphError as e:
        print(f"✗ Failed to get libraries for site {site_id}: {e}")
        raise
    return drives


def inventory_sharepoint(graph_client: GraphClient) -> dict[str, Any]:
    """Inventory all SharePoint sites and their libraries.
    
    Returns:
        Dictionary with sites and their libraries for migration mapping
    """
    inventory = {"sites": [], "total_libraries": 0, "total_files": 0}
    
    try:
        sites = discover_sites(graph_client)
        print(f"[OK] Found {len(sites)} sites")
        
        for site in sites:
            site_info = {
                "id": site["id"],
                "displayName": site.get("displayName"),
                "webUrl": site.get("webUrl"),
                "libraries": []
            }
            
            try:
                libraries = get_site_libraries(graph_client, site["id"])
                for lib in libraries:
                    site_info["libraries"].append({
                        "id": lib["id"],
                        "name": lib.get("name"),
                        "driveType": lib.get("driveType"),
                        "webUrl": lib.get("webUrl")
                    })
                
                inventory["total_libraries"] += len(libraries)
                
                if libraries:
                    print(f"  [OK] {site.get('displayName')}: {len(libraries)} libraries")
                    
            except GraphError as e:
                print(f"  ⚠ Cannot access {site.get('displayName')}: {e}")
                continue
            
            if site_info["libraries"]:
                inventory["sites"].append(site_info)
        
        return inventory
        
    except Exception as e:
        print(f"✗ Inventory failed: {e}")
        raise


# ============================================================================
# APP-LEVEL SITE ACCESS
# ============================================================================

def grant_app_site_permissions(graph_client: GraphClient, app_id: str, roles: list[str] | None = None) -> dict[str, Any]:
    """Grant app access to ALL SharePoint sites via Microsoft Graph API.
    
    Args:
        graph_client: Authenticated Graph API client
        app_id: Application (service principal) object ID
        roles: Permission roles (default: ["read"])
        
    Returns:
        Dictionary with success/failure counts
        
    Example:
        result = grant_app_site_permissions(graph_client, "43824c76-d3e1-4191-9f08-ad4b60dda88a")
        print(f"Granted: {result['success']}, Failed: {result['failed']}")
    """
    if roles is None:
        roles = ["read"]
    
    stats = {"success": 0, "failed": 0, "errors": []}
    
    try:
        for site in graph_client.pages("sites?search=*"):
            site_id = site.get("id")
            site_url = site.get("webUrl", "unknown")
            
            if not site_id:
                continue
            
            try:
                payload = {
                    "roles": roles,
                    "grantedToV2": {
                        "application": {
                            "id": app_id
                        }
                    }
                }
                graph_client.request("POST", f"sites/{site_id}/permissions", json=payload)
                print(f"[OK] Granted access to: {site_url}")
                stats["success"] += 1
            except GraphError as e:
                print(f"✗ Failed to grant access to {site_url}: {e}")
                stats["failed"] += 1
                stats["errors"].append({"site": site_url, "error": str(e)})
    except GraphError as e:
        print(f"✗ Failed to retrieve sites: {e}")
        stats["errors"].append({"error": str(e)})
    
    print(f"\n[OK] Summary: {stats['success']} granted, {stats['failed']} failed")
    return stats


# ============================================================================
# METADATA & VERSIONS (currently tracked for future use)
# ============================================================================

def copy_file_metadata(
    source_graph: GraphClient,
    target_graph: GraphClient,
    source_drive_id: str,
    target_drive_id: str,
    source_item_id: str,
    target_item_id: str,
) -> dict[str, Any]:
    """Copy file metadata from source to target (partial support).
    
    Note: SharePoint metadata copying is complex due to site column requirements.
    Currently tracks which files need metadata migration.
    """
    stats = {"metadata_fields": 0, "errors": []}
    
    try:
        source_props = source_graph.request("GET", f"/drives/{source_drive_id}/items/{source_item_id}?$select=*")
        
        metadata_fields = {
            "lastModifiedDateTime": source_props.get("lastModifiedDateTime"),
            "webUrl": source_props.get("webUrl"),
            "size": source_props.get("size"),
            "cTag": source_props.get("cTag"),
        }
        
        # Note: Full metadata (site columns, custom properties) requires:
        # 1. Mapping field definitions between source and target sites
        # 2. Creating matching columns in target
        # 3. Copying values only after column creation
        
        stats["metadata_fields"] = len([v for v in metadata_fields.values() if v is not None])
        
    except GraphError as e:
        stats["errors"].append(str(e))
    
    return stats


def get_file_versions(
    graph_client: GraphClient,
    drive_id: str,
    item_id: str,
) -> list[dict[str, Any]]:
    """Get version history for a file (information only).
    
    Note: Version copying is not currently supported by Graph migration APIs.
    This function inventories versions for audit purposes.
    """
    versions = []
    try:
        for version in graph_client.pages(f"/drives/{drive_id}/items/{item_id}/versions?$select=id,lastModifiedDateTime,lastModifiedBy"):
            versions.append({
                "id": version.get("id"),
                "lastModifiedDateTime": version.get("lastModifiedDateTime"),
                "lastModifiedBy": version.get("lastModifiedBy"),
            })
    except GraphError as e:
        print(f"⚠ Cannot retrieve versions for {item_id}: {e}")
    
    return versions


# ============================================================================
# USERS & GROUPS (permission principals)
# ============================================================================

def resolve_user_mapping(
    source_upn: str,
    user_map: dict[str, str],
) -> str | None:
    """Resolve source user UPN to target UPN using mapping."""
    return user_map.get(source_upn)


def get_site_groups(graph_client: GraphClient, site_id: str) -> list[dict[str, Any]]:
    """Get all groups in a SharePoint site."""
    try:
        return [
            group
            for group in graph_client.pages(f"/sites/{site_id}/siteGroups?$select=id,displayName,description")
        ]
    except GraphError as e:
        print(f"⚠ Cannot retrieve groups for site {site_id}: {e}")
        return []


