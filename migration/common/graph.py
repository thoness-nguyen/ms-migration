from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests
from requests.adapters import HTTPAdapter


class GraphError(RuntimeError):
    def __init__(self, message: str = "", status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# Statuses that will not change by retrying the same request. 429/5xx/408 are
# transient and are handled (or retried) elsewhere.
_PERMANENT_STATUSES = (400, 403, 404, 409, 410)


def is_mysite_not_found(error: BaseException) -> bool:
    """True for the permanent 404 'User's mysite not found.' (OneDrive not provisioned)."""
    text = str(error).lower()
    return "mysite not found" in text


def is_permanent_graph_error(error: BaseException) -> bool:
    """Permanent Graph failure (retrying the same request cannot succeed)."""
    if not isinstance(error, GraphError):
        return False
    status = getattr(error, "status_code", None)
    return status in _PERMANENT_STATUSES


class GraphClient:
    def __init__(
        self,
        token: str | Callable[[], str],
        base_url: str = "https://graph.microsoft.com/v1.0",
        session: requests.Session | None = None,
        on_throttle: Callable[[], None] | None = None,
        pool_size: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        # Concurrent workers share this one session; make sure the underlying
        # connection pool is big enough that concurrency doesn't just queue up
        # waiting for a free connection.
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Accept either a fixed token string or a callable that returns a fresh
        # one on each call (e.g. auth.TokenProvider), so long-running migrations
        # can transparently refresh an expired token instead of erroring out.
        self._token_provider: Callable[[], str] = token if callable(token) else (lambda: token)
        # Called whenever the server throttles us (429), so a concurrency
        # controller (common/concurrency.py) can shrink its worker count.
        self._on_throttle = on_throttle
        self._lock = threading.Lock()
        self.metrics = {"requests": 0, "throttled_429": 0, "server_errors_5xx": 0, "auth_refreshes": 0}
        self._apply_token(self._token_provider())

    def set_throttle_hook(self, on_throttle: Callable[[], None] | None) -> None:
        """Rewire the throttle callback - a domain function can point this at
        whichever AdaptiveGate is active for its current concurrent phase."""
        with self._lock:
            self._on_throttle = on_throttle

    def _apply_token(self, access_token: str) -> None:
        with self._lock:
            self.session.headers.update({"Authorization": f"Bearer {access_token}", "Accept": "application/json"})

    def _record(self, key: str) -> None:
        with self._lock:
            self.metrics[key] += 1

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("https://") else f"{self.base_url}/{path.lstrip('/')}"
        self._record("requests")
        for attempt in range(6):
            try:
                response = self.session.request(method, url, timeout=120, **kwargs)
            except requests.exceptions.RequestException as error:
                if attempt == 5:
                    raise
                delay = min(120, 5 * 2**attempt)
                print(f"  [retry] network error on {method} {url}: {type(error).__name__} - waiting {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
                continue
            if response.status_code == 401 and attempt < 5:
                # Token expired/invalid mid-run - refresh and retry once per attempt
                self._record("auth_refreshes")
                print(f"  [auth] Token rejected (401), refreshing and retrying: {method} {url}")
                self._apply_token(self._token_provider())
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                if response.status_code == 429:
                    self._record("throttled_429")
                    if self._on_throttle:
                        self._on_throttle()
                else:
                    self._record("server_errors_5xx")
                delay = int(response.headers.get("Retry-After", min(60, 2**attempt)))
                print(f"  [throttle] {response.status_code} on {method} {url} - waiting {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
                continue
            if not response.ok:
                raise GraphError(f"{method} {url} failed ({response.status_code}): {response.text[:500]}", status_code=response.status_code)
            return response.json() if response.content else None
        raise GraphError(f"{method} {url} failed after retries")

    def raw_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Like session.request(), for raw byte transfers (file download/upload chunks)
        that need direct access to the response instead of the JSON-decoding wrapper above.
        Shares the same resilience as request(): refreshes the token on 401, retries
        429/5xx with backoff (honoring Retry-After, same as request()), and retries
        transient network errors (connection reset/timeout/etc.) with backoff. A
        permanent 4xx (other than 401/429) is returned as-is - the caller's own
        raise_for_status()/`.ok` check records it, same as before.
        """
        self._record("requests")
        last_response: requests.Response | None = None
        for attempt in range(6):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.exceptions.RequestException as error:
                if attempt == 5:
                    raise
                delay = min(120, 5 * 2**attempt)
                print(f"  [retry] network error during transfer on {method} {url}: {error} - waiting {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
                continue
            last_response = response
            if response.status_code == 401 and attempt < 5:
                self._record("auth_refreshes")
                print(f"  [auth] Token rejected (401) during transfer, refreshing: {method} {url}")
                self._apply_token(self._token_provider())
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                if response.status_code == 429:
                    self._record("throttled_429")
                    if self._on_throttle:
                        self._on_throttle()
                else:
                    self._record("server_errors_5xx")
                if attempt == 5:
                    return response
                delay = int(response.headers.get("Retry-After", min(60, 2**attempt)))
                print(f"  [throttle] {response.status_code} during transfer on {method} {url} - waiting {delay}s (attempt {attempt + 1}/6)")
                time.sleep(delay)
                continue
            return response
        return last_response

    def pages(self, path: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        next_url: str | None = path
        while next_url:
            page = self.request("GET", next_url, **kwargs)
            yield from page.get("value", [])
            next_url = page.get("@odata.nextLink")
