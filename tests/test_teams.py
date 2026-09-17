from datetime import datetime, timezone

from migration.teams.services import _timestamp, make_attachment_resolver


def test_timestamp_is_unique_to_millisecond():
    first = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _timestamp("2025-01-01T00:00:00Z", first) == "2025-01-01T00:00:00.001Z"


def test_timestamp_preserves_order():
    assert _timestamp("2025-01-01T00:00:01.123Z", None) == "2025-01-01T00:00:01.123Z"


def test_attachment_resolver_rewrites_to_target_file_when_found():
    class Graph:
        def request(self, method, path, **kwargs):
            assert path == "/users/nguyet@paizes.com/drive/root:/foo.pdf"
            return {"webUrl": "https://target-my.sharepoint.com/personal/nguyet_paizes_com/Documents/foo.pdf"}

    resolve = make_attachment_resolver(Graph(), {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"})
    url = resolve("https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf")
    assert url == "https://target-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf".replace("harbouroutdoor", "paizes")


def test_attachment_resolver_falls_back_to_source_when_not_found():
    from migration.common.graph import GraphError

    class Graph:
        def request(self, method, path, **kwargs):
            raise GraphError("404 not found")

    resolve = make_attachment_resolver(Graph(), {"nguyet@harbouroutdoor.com": "nguyet@paizes.com"})
    source_url = "https://contoso-my.sharepoint.com/personal/nguyet_harbouroutdoor_com/Documents/foo.pdf"
    assert resolve(source_url) == source_url


def test_attachment_resolver_falls_back_when_owner_unmapped():
    resolve = make_attachment_resolver(None, {})
    source_url = "https://contoso-my.sharepoint.com/personal/someone_harbouroutdoor_com/Documents/foo.pdf"
    assert resolve(source_url) == source_url

