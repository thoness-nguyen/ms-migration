from __future__ import annotations

import os

import msal


def token(tenant: str, client_id: str, client_secret: str) -> str:
    app = msal.ConfidentialClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=client_secret)
    result = app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "Could not acquire Graph token"))
    return result["access_token"]


def credential(name: str, legacy_name: str) -> str:
    value = os.getenv(name) or os.getenv(legacy_name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name} or {legacy_name}")
    return value


class TokenProvider:
    """Callable that hands GraphClient a fresh access token on every call.

    MSAL caches tokens internally and only hits the token endpoint again once
    the cached one is near/past expiry, so calling this repeatedly during a
    long-running migration is cheap and keeps requests from ever running on
    a stale token.
    """

    def __init__(self, tenant: str, client_id: str, client_secret: str):
        self._app = msal.ConfidentialClientApplication(
            client_id, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=client_secret
        )

    def __call__(self) -> str:
        result = self._app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description", "Could not acquire Graph token"))
        return result["access_token"]


def source_token() -> TokenProvider:
    return TokenProvider(os.environ["SOURCE_TENANT_ID"], credential("SOURCE_GRAPH_CLIENT_ID", "GRAPH_CLIENT_ID"), credential("SOURCE_GRAPH_CLIENT_SECRET", "GRAPH_CLIENT_SECRET"))


def target_token() -> TokenProvider:
    return TokenProvider(os.environ["TARGET_TENANT_ID"], credential("TARGET_GRAPH_CLIENT_ID", "GRAPH_CLIENT_ID"), credential("TARGET_GRAPH_CLIENT_SECRET", "GRAPH_CLIENT_SECRET"))
