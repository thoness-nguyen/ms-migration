from __future__ import annotations

import pytest

from migration.sharepoint.migration import CHUNK_SIZE, stream_copy_large_file

PAYLOAD = bytes(range(256)) * 400  # 102,400 bytes
PIECE = 32 * 1024


class Resp:
    def __init__(self, content=b"", status=200, body=None):
        self.content = content if body is None else b"{}"
        self.status_code = status
        self._body = body
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise OSError(f"HTTP {self.status_code}")

    def json(self):
        return self._body or {}


class Source:
    base_url = "https://graph.microsoft.com/v1.0"

    def __init__(self):
        self.ranges = []

    def raw_request(self, method, url, headers=None, **kwargs):
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        self.ranges.append((start, end))
        return Resp(PAYLOAD[start:end + 1])


class Target:
    def __init__(self, fail_once_at=None):
        self.received = bytearray()
        self.fail_once_at = fail_once_at
        self.puts = 0

    def raw_request(self, method, url, data=None, headers=None, **kwargs):
        if method == "GET":  # session status
            return Resp(body={"nextExpectedRanges": [f"{len(self.received)}-"]})
        start = int(headers["Content-Range"].split(" ")[1].split("-")[0])
        self.puts += 1
        if self.fail_once_at == start:
            self.fail_once_at = None
            raise OSError("connection reset")
        assert start == len(self.received), "pieces must arrive in order with no gaps"
        self.received.extend(data)
        done = len(self.received) == len(PAYLOAD)
        return Resp(body={"id": "new-item"} if done else {"nextExpectedRanges": [f"{len(self.received)}-"]})


def test_chunk_size_is_graph_compliant():
    assert CHUNK_SIZE % (320 * 1024) == 0 and CHUNK_SIZE <= 60 * 1024 * 1024


def test_streams_file_piece_by_piece_without_buffering_whole_file():
    source, target = Source(), Target()
    item = stream_copy_large_file(source, target, "d", "i", len(PAYLOAD), "https://upload", chunk_size=PIECE)
    assert item == {"id": "new-item"}
    assert bytes(target.received) == PAYLOAD
    assert max(end - start + 1 for start, end in source.ranges) <= PIECE


def test_failed_piece_resumes_from_session_instead_of_restarting():
    source, target = Source(), Target(fail_once_at=PIECE)
    item = stream_copy_large_file(source, target, "d", "i", len(PAYLOAD), "https://upload", chunk_size=PIECE)
    assert item == {"id": "new-item"} and bytes(target.received) == PAYLOAD
    assert source.ranges.count((0, PIECE - 1)) == 1  # first piece never re-read


def test_gives_up_after_repeated_failures():
    class AlwaysFails(Target):
        def raw_request(self, method, url, data=None, headers=None, **kwargs):
            if method == "GET":
                return Resp(body={"nextExpectedRanges": ["0-"]})
            raise OSError("down")

    with pytest.raises(OSError):
        stream_copy_large_file(Source(), AlwaysFails(), "d", "i", len(PAYLOAD), "https://upload", chunk_size=PIECE)
