from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

from .graph import GraphError

T = TypeVar("T")

# Errors worth retrying: transient network failures plus the broader set of
# per-item exceptions domains may raise (a single bad item should not be fatal).
RETRIABLE_ERRORS = (
    ConnectionError, ConnectionResetError, BrokenPipeError, TimeoutError,
    GraphError, OSError, TypeError, ValueError, KeyError, AttributeError,
)


def with_retry(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    backoff_base_seconds: int = 10,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: Any,
) -> tuple[T | None, bool, BaseException | None, int]:
    """Call func(*args, **kwargs), retrying transient/per-item failures with backoff.

    Shared across every migration domain (SharePoint, Teams, OneDrive) so retry
    behavior stays consistent instead of being reimplemented per domain.

    Returns (result, success, last_error, attempts_made). result is None when all attempts fail.
    """
    last_error: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                wait_time = min(60, 2 ** (attempt - 2) * backoff_base_seconds)
                time.sleep(wait_time)
            result = func(*args, **kwargs)
            return result, True, None, attempt
        except RETRIABLE_ERRORS as error:
            last_error = error
            if on_retry:
                on_retry(attempt, error)
            continue
    return None, False, last_error, max_retries
