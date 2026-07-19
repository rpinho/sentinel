from datetime import UTC, datetime

from sentinel.corroborate import corroborate_release
from sentinel.types import Event


def test_release_corroboration_accepts_github_v_prefix() -> None:
    events = [
        Event(
            datetime(2025, 4, 7, 13, 12, 5, tzinfo=UTC),
            "webpack/webpack",
            "release",
            meta={"source": "gharchive", "version": "v5.99.0"},
        ),
        Event(
            datetime(2025, 4, 7, 13, 13, 8, tzinfo=UTC),
            "webpack",
            "release",
            meta={"source": "npm_registry", "version": "5.99.0"},
        ),
    ]

    result = corroborate_release(
        events, repo="webpack/webpack", package="webpack", version="5.99.0"
    )

    assert result is not None
    assert result.difference_seconds == 63
