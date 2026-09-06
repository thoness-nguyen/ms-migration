from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Any

import msal
import requests

load_dotenv()


GRAPH_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

SOURCE_DOMAIN = "harbouroutdoor.com"
TARGET_DOMAIN = "paizes.com"

OUTPUT_FILE = Path("config/user-mapping.generated.json")


class GraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        tenant_name: str,
    ) -> None:
        self.tenant_name = tenant_name

        authority = f"https://login.microsoftonline.com/{tenant_id}"

        self.app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=authority,
            client_credential=client_secret,
        )

        result = self.app.acquire_token_for_client(scopes=GRAPH_SCOPE)

        if "access_token" not in result:
            raise RuntimeError(
                f"[{tenant_name}] Failed to acquire token: "
                f"{result.get('error')} - {result.get('error_description')}"
            )

        self.token = result["access_token"]

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            }
        )

    def get_all(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = endpoint if endpoint.startswith("http") else f"{GRAPH_URL}{endpoint}"

        results: list[dict[str, Any]] = []

        while url:
            response = self.session.get(
                url,
                params=params,
                timeout=60,
            )

            if not response.ok:
                raise RuntimeError(
                    f"[{self.tenant_name}] Graph GET failed\n"
                    f"URL: {url}\n"
                    f"HTTP: {response.status_code}\n"
                    f"Response: {response.text[:2000]}"
                )

            payload = response.json()

            values = payload.get("value", [])
            if not isinstance(values, list):
                raise RuntimeError(
                    f"[{self.tenant_name}] Unexpected Graph response: "
                    f"'value' is not a list"
                )

            results.extend(values)

            url = payload.get("@odata.nextLink")
            params = None

        return results

    def get_user_by_upn(self, upn: str) -> dict[str, Any] | None:
        # Graph supports /users/{id | userPrincipalName}
        url = f"{GRAPH_URL}/users/{upn}"

        response = self.session.get(url, timeout=60)

        if response.status_code == 404:
            return None

        if not response.ok:
            raise RuntimeError(
                f"[{self.tenant_name}] Failed to lookup user {upn}\n"
                f"HTTP: {response.status_code}\n"
                f"Response: {response.text[:2000]}"
            )

        return response.json()


def required_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value


def build_client(prefix: str, tenant_name: str) -> GraphClient:
    return GraphClient(
        tenant_id=required_env(f"{prefix}_TENANT_ID"),
        client_id=required_env(f"{prefix}_GRAPH_CLIENT_ID"),
        client_secret=required_env(f"{prefix}_GRAPH_CLIENT_SECRET"),
        tenant_name=tenant_name,
    )


def normalize_upn(value: str) -> str:
    return value.strip().lower()


def get_key_from_upn(upn: str, domain: str) -> str | None:
    upn = normalize_upn(upn)
    suffix = f"@{domain.lower()}"

    if not upn.endswith(suffix):
        return None

    return upn[: -len(suffix)]


def main() -> int:
    print("=" * 80)
    print("MICROSOFT 365 USER MAPPING GENERATOR")
    print("=" * 80)
    print(f"Source domain : {SOURCE_DOMAIN}")
    print(f"Target domain : {TARGET_DOMAIN}")
    print(f"Output        : {OUTPUT_FILE}")
    print()

    source_graph = build_client("SOURCE", "SOURCE")
    target_graph = build_client("TARGET", "TARGET")

    print("[1/4] Loading source users...")

    source_users = source_graph.get_all(
        "/users",
        params={
            "$select": "id,displayName,userPrincipalName,mail,accountEnabled",
            "$top": "999",
        },
    )

    print(f"      Found {len(source_users)} total source users")

    source_users_filtered: list[dict[str, Any]] = []

    for user in source_users:
        upn = user.get("userPrincipalName")

        if not upn:
            continue

        key = get_key_from_upn(upn, SOURCE_DOMAIN)

        if key is None:
            continue

        user["_mapping_key"] = key
        source_users_filtered.append(user)

    print(
        f"      Found {len(source_users_filtered)} users "
        f"with @{SOURCE_DOMAIN}"
    )

    print()
    print("[2/4] Loading target users...")

    target_users = target_graph.get_all(
        "/users",
        params={
            "$select": "id,displayName,userPrincipalName,mail,accountEnabled",
            "$top": "999",
        },
    )

    print(f"      Found {len(target_users)} total target users")

    target_by_upn: dict[str, dict[str, Any]] = {}

    for user in target_users:
        upn = user.get("userPrincipalName")

        if not upn:
            continue

        target_by_upn[normalize_upn(upn)] = user

    print()
    print("[3/4] Building mappings...")

    mappings: list[dict[str, Any]] = []
    missing_targets: list[dict[str, Any]] = []

    for source_user in sorted(
        source_users_filtered,
        key=lambda item: item["_mapping_key"].lower(),
    ):
        source_upn = normalize_upn(source_user["userPrincipalName"])
        key = source_user["_mapping_key"]

        target_upn = f"{key}@{TARGET_DOMAIN}".lower()

        target_user = target_by_upn.get(target_upn)

        if not target_user:
            # Fallback to direct Graph lookup.
            # This also protects against any target list/pagination issue.
            target_user = target_graph.get_user_by_upn(target_upn)

        if not target_user:
            missing_targets.append(
                {
                    "key": key,
                    "source_upn": source_upn,
                    "target_upn": target_upn,
                    "source_id": source_user["id"],
                    "display_name": source_user.get("displayName"),
                }
            )

            print(
                f"  [MISSING] {source_upn} -> {target_upn}"
            )
            continue

        mapping = {
            "key": key,
            "display_name": source_user.get("displayName")
            or target_user.get("displayName")
            or key,
            "source_upn": source_upn,
            "target_upn": normalize_upn(
                target_user.get("userPrincipalName") or target_upn
            ),
            "source_id": source_user["id"],
            "target_id": target_user["id"],
        }

        mappings.append(mapping)

        print(
            f"  [MATCH]   {source_upn} -> "
            f"{mapping['target_upn']}"
        )

    output = {
        "source_domain": SOURCE_DOMAIN,
        "target_domain": TARGET_DOMAIN,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").UTC
        ).isoformat(),
        "summary": {
            "source_users_scanned": len(source_users),
            "source_users_in_domain": len(source_users_filtered),
            "matched": len(mappings),
            "missing_target": len(missing_targets),
        },
        "users": mappings,
        "missing_target_users": missing_targets,
    }

    print()
    print("[4/4] Writing mapping file...")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_FILE.write_text(
        json.dumps(
            output,
            indent=4,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"      Written: {OUTPUT_FILE}")

    print()
    print("=" * 80)
    print("RESULT")
    print("=" * 80)
    print(f"Source users in domain : {len(source_users_filtered)}")
    print(f"Matched                : {len(mappings)}")
    print(f"Missing target         : {len(missing_targets)}")

    if missing_targets:
        print()
        print("WARNING: Some users could not be mapped.")
        print("Review 'missing_target_users' before running migration.")
        return 2

    print()
    print("Mapping generated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())