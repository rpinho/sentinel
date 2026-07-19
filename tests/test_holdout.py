from datetime import UTC, datetime, timedelta

from sentinel.holdout import (
    auto_label_firing,
    canonical_sha256,
    classify_acknowledgment,
    find_detector_misses,
    iter_hours,
    mode_metrics,
    routing_for_firing,
)
from sentinel.store import Firing
from sentinel.types import Event

NOW = datetime(2025, 5, 12, 12, tzinfo=UTC)


def _firing(*, bucket_total: int = 12) -> Firing:
    return Firing(
        firing_id="firing:holdout-test",
        ts=NOW,
        entity="facebook/react",
        metric_id="issue_activity_per_hour",
        kind="metric",
        window_start=NOW.replace(minute=0),
        window_end=NOW.replace(minute=0) + timedelta(hours=1),
        observed=5,
        baseline_mean=0.5,
        baseline_stddev=0.5,
        score=9,
        detector_version="sha256:frozen",
        meta={"bucket_total": bucket_total},
    )


def _event(
    *,
    ts: datetime,
    kind: str,
    text: str,
    issue_number: int = 7,
    association: str | None = None,
) -> Event:
    meta = {
        "source": "gharchive",
        "source_event_id": f"{kind}-{ts.timestamp()}",
        "issue_number": issue_number,
        "action": "created" if kind == "comment" else "opened",
        "url": f"https://github.com/facebook/react/issues/{issue_number}",
    }
    if association:
        meta["author_association"] = association
    return Event(
        ts=ts,
        entity="facebook/react",
        kind=kind,  # type: ignore[arg-type]
        text=text,
        meta=meta,
    )


def test_acknowledgment_rules_are_deterministic_and_maintainer_scoped() -> None:
    assert classify_acknowledgment("Confirmed regression; fixed in #123") == "TRUE"
    assert classify_acknowledgment("This is expected behavior, not a bug") == "FALSE"
    assert classify_acknowledgment("Thanks for the report") is None

    issue = _event(ts=NOW - timedelta(minutes=20), kind="issue", text="Regression")
    outsider = _event(
        ts=NOW + timedelta(minutes=5),
        kind="comment",
        text="Confirmed regression; fixed in #123",
        association="NONE",
    )
    maintainer = _event(
        ts=NOW + timedelta(minutes=20),
        kind="comment",
        text="Confirmed regression; fixed in #123",
        association="MEMBER",
    )

    verdict = auto_label_firing(
        firing=_firing(), events=[issue, outsider, maintainer], issue_numbers={7}
    )

    assert verdict.verdict == "TRUE"
    assert verdict.issue_number == 7
    assert verdict.lead_minutes == 20


def test_metrics_publish_false_positives_misses_and_unresolved() -> None:
    rows = [
        {
            "verdict": {"verdict": "TRUE", "lead_minutes": 30},
            "modes": {
                "full": {"real": True},
                "no_feedback": {"real": False},
                "no_agent": {"real": None},
            },
        },
        {
            "verdict": {"verdict": "FALSE", "lead_minutes": None},
            "modes": {
                "full": {"real": False},
                "no_feedback": {"real": True},
                "no_agent": {"real": None},
            },
        },
        {
            "verdict": {"verdict": "UNRESOLVED", "lead_minutes": None},
            "modes": {
                "full": {"real": True},
                "no_feedback": {"real": True},
                "no_agent": {"real": None},
            },
        },
    ]

    full = mode_metrics(rows, mode="full", weeks=1)
    no_feedback = mode_metrics(rows, mode="no_feedback", weeks=1)
    no_agent = mode_metrics(rows, mode="no_agent", weeks=1)

    assert full["precision"] == 1
    assert full["true_negatives"] == 1
    assert full["unresolved"] == 1
    assert no_feedback["precision"] == 0
    assert no_feedback["false_positives"] == 1
    assert no_feedback["misses"] == 1
    assert no_agent["precision"] == 0.5
    assert no_agent["false_positives_per_week"] == 1


def test_detector_misses_and_routing_are_disclosed() -> None:
    issue = _event(
        ts=NOW - timedelta(minutes=20),
        kind="issue",
        text="Regression: rendering is broken",
    )
    ack = _event(
        ts=NOW + timedelta(minutes=20),
        kind="comment",
        text="Confirmed regression; fixed in #123",
        association="OWNER",
    )

    misses = find_detector_misses(
        events=[issue, ack],
        firings=[],
        start=NOW - timedelta(hours=1),
        end=NOW + timedelta(hours=1),
    )

    assert len(misses) == 1
    assert misses[0]["issue_number"] == 7
    assert routing_for_firing(_firing(bucket_total=20))["severity"] == "critical"
    assert routing_for_firing(_firing(bucket_total=12))["severity"] == "high"
    assert routing_for_firing(_firing(bucket_total=6))["severity"] == "watch"


def test_freeze_hash_is_canonical_and_week_has_168_hours() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
    assert (
        len(
            list(
                iter_hours(
                    datetime(2025, 5, 12, tzinfo=UTC),
                    datetime(2025, 5, 19, tzinfo=UTC),
                )
            )
        )
        == 168
    )
