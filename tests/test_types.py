from datetime import UTC, datetime

import pytest

from sentinel.types import Event


def test_event_round_trips_and_normalizes_utc() -> None:
    event = Event.from_dict(
        {
            "ts": "2025-04-07T09:13:08-04:00",
            "entity": " webpack ",
            "kind": "release",
            "text": None,
            "value": None,
            "meta": {"version": "5.99.0"},
        }
    )

    assert event.ts == datetime(2025, 4, 7, 13, 13, 8, tzinfo=UTC)
    assert event.entity == "webpack"
    assert event.to_dict()["ts"] == "2025-04-07T13:13:08Z"


def test_event_requires_timezone_and_known_kind() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Event(datetime(2025, 4, 7), "webpack", "release")
    with pytest.raises(ValueError, match="unsupported"):
        Event(datetime.now(UTC), "webpack", "push")  # type: ignore[arg-type]
