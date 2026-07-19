import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from sentinel.adapters.gharchive import GHArchiveAdapter


def _gzip_lines(rows: list[dict]) -> bytes:
    return gzip.compress("".join(json.dumps(row) + "\n" for row in rows).encode())


def test_gharchive_uses_unpadded_hour_and_preserves_comment_association(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "id": "1",
            "type": "IssueCommentEvent",
            "actor": {"login": "maintainer"},
            "repo": {"name": "webpack/webpack"},
            "payload": {
                "action": "created",
                "issue": {"number": 19394},
                "comment": {
                    "id": 2784517989,
                    "body": "Found buggy places — PR #19398",
                    "created_at": "2025-04-07T20:14:04Z",
                    "html_url": "https://example.test/comment",
                    "user": {"login": "maintainer"},
                    "author_association": "MEMBER",
                },
            },
            "created_at": "2025-04-07T20:14:05Z",
        },
        {
            "id": "2",
            "type": "WatchEvent",
            "actor": {"login": "someone"},
            "repo": {"name": "webpack/webpack"},
            "payload": {},
            "created_at": "2025-04-07T20:14:06Z",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2025-04-07-7.json.gz")
        return httpx.Response(200, content=_gzip_lines(rows))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = GHArchiveAdapter(cache_dir=tmp_path, client=client)
    events = list(
        adapter.iter_events(
            datetime(2025, 4, 7, 7, tzinfo=UTC), entity="webpack/webpack"
        )
    )

    assert len(events) == 1
    assert events[0].kind == "comment"
    assert events[0].meta["author_association"] == "MEMBER"
    assert events[0].meta["comment_id"] == 2784517989
    assert events[0].ts == datetime(2025, 4, 7, 20, 14, 4, tzinfo=UTC)


def test_gharchive_normalizes_issue_and_release(tmp_path: Path) -> None:
    rows = [
        {
            "id": "issue",
            "type": "IssuesEvent",
            "actor": {"login": "reporter"},
            "repo": {"name": "webpack/webpack"},
            "payload": {
                "action": "opened",
                "issue": {
                    "number": 19394,
                    "title": "Regression",
                    "body": "process is not defined",
                    "created_at": "2025-04-07T18:04:49Z",
                    "html_url": "https://example.test/issue",
                    "user": {"login": "reporter"},
                    "labels": [{"name": "bug"}],
                },
            },
            "created_at": "2025-04-07T18:05:00Z",
        },
        {
            "id": "release",
            "type": "ReleaseEvent",
            "actor": {"login": "bot"},
            "repo": {"name": "webpack/webpack"},
            "payload": {
                "action": "published",
                "release": {
                    "id": 99,
                    "tag_name": "v5.99.0",
                    "name": "v5.99.0",
                    "body": "release notes",
                    "published_at": "2025-04-07T13:12:05Z",
                    "html_url": "https://example.test/release",
                    "prerelease": False,
                    "draft": False,
                },
            },
            "created_at": "2025-04-07T13:12:06Z",
        },
    ]
    hour = datetime(2025, 4, 7, 13, tzinfo=UTC)
    path = tmp_path / "2025-04-07-13.json.gz"
    path.write_bytes(_gzip_lines(rows))

    events = list(GHArchiveAdapter(cache_dir=tmp_path).iter_events(hour))

    assert [event.kind for event in events] == ["issue", "release"]
    assert events[0].text == "Regression\n\nprocess is not defined"
    assert events[0].meta["action"] == "opened"
    assert events[1].meta["version"] == "v5.99.0"
    assert events[1].ts == datetime(2025, 4, 7, 13, 12, 6, tzinfo=UTC)
    assert events[1].meta["published_at"] == "2025-04-07T13:12:05Z"
