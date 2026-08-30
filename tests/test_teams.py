from datetime import datetime, timezone

from migration.teams.services import _timestamp


def test_timestamp_is_unique_to_millisecond():
    first = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _timestamp("2025-01-01T00:00:00Z", first) == "2025-01-01T00:00:00.001Z"


def test_timestamp_preserves_order():
    assert _timestamp("2025-01-01T00:00:01.123Z", None) == "2025-01-01T00:00:01.123Z"
