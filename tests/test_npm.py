from datetime import UTC, date, datetime

import httpx

from sentinel.adapters.npm import NpmAdapter


def test_npm_adapter_emits_text_free_releases_and_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "registry.npmjs.org":
            return httpx.Response(
                200,
                json={
                    "time": {
                        "created": "2012-03-10T01:41:12Z",
                        "5.99.0": "2025-04-07T13:13:08Z",
                        "5.99.1": "2025-04-07T22:36:24Z",
                        "modified": "2025-04-08T00:00:00Z",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "downloads": [
                    {"day": "2025-04-07", "downloads": 5_500_000},
                    {"day": "2025-04-08", "downloads": 5_700_000},
                ]
            },
        )

    adapter = NpmAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    releases = list(
        adapter.iter_releases(
            "webpack",
            start=datetime(2025, 4, 7, tzinfo=UTC),
            end=datetime(2025, 4, 8, tzinfo=UTC),
        )
    )
    metrics = list(adapter.iter_downloads("webpack", date(2025, 4, 7), date(2025, 4, 8)))

    assert [event.meta["version"] for event in releases] == ["5.99.0", "5.99.1"]
    assert all(event.text is None and event.value is None for event in releases)
    assert [event.value for event in metrics] == [5_500_000.0, 5_700_000.0]
    assert all(event.text is None and event.kind == "metric" for event in metrics)
