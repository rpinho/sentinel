from datetime import UTC, datetime, timedelta

from sentinel.detector_v4 import build_audit_candidates, build_labeled_dataset
from sentinel.types import Event

ARCHIVE_START = datetime(2025, 1, 1, tzinfo=UTC)
DETECTION_START = ARCHIVE_START + timedelta(days=7)


def _protocol() -> dict[str, object]:
    return {
        "study_id": "synthetic-v4",
        "repositories": {"train": ["owner/train"], "test": ["owner/test"]},
        "windows": [
            {
                "window_id": "synthetic",
                "archive_start": ARCHIVE_START.isoformat(),
                "detection_start": DETECTION_START.isoformat(),
                "detection_end": (DETECTION_START + timedelta(hours=3)).isoformat(),
                "confirmation_end": (
                    DETECTION_START + timedelta(days=3, hours=3)
                ).isoformat(),
            }
        ],
        "label_rule": {
            "version": "maintainer-confirmed-multisource-v4",
            "minimum_raw_activity": 1,
            "confirmation_lookahead_hours": 72,
            "bug_label_pattern": (
                "(^|[^a-z])(bug|regression|defect|crash|panic|correctness)"
                "([^a-z]|$)"
            ),
            "fixing_reference_pattern": (
                "\\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)"
                "\\s*:?[ \\t]*(?:#[ \\t]*)?([0-9]+)\\b"
            ),
        },
        "feature_policy": {"history_days": 7, "release_window_hours": 6},
        "power_targets": {"audit_sample_per_source_per_split": 20},
    }


def _issue(
    hour: int,
    minute: int,
    number: int,
    *,
    action: str = "opened",
    actor: str = "reporter",
    labels: list[str] | None = None,
    state_reason: str | None = None,
    entity: str = "owner/train",
) -> Event:
    return Event(
        ts=DETECTION_START + timedelta(hours=hour, minutes=minute),
        entity=entity,
        kind="issue",
        text=f"Regression: issue {number} crashes",
        meta={
            "action": action,
            "issue_number": number,
            "author": "reporter",
            "actor": actor,
            "actor_type": "User",
            "labels": labels or [],
            "state_reason": state_reason,
            "url": f"https://example.test/issues/{number}",
        },
    )


def _comment(hour: int, minute: int, number: int, text: str) -> Event:
    return Event(
        ts=DETECTION_START + timedelta(hours=hour, minutes=minute),
        entity="owner/train",
        kind="comment",
        text=text,
        meta={
            "action": "created",
            "issue_number": number,
            "author": "maintainer",
            "author_type": "User",
            "author_association": "MEMBER",
            "url": f"https://example.test/issues/{number}#comment",
        },
    )


def _pull(hour: int, minute: int, number: int, text: str) -> Event:
    return Event(
        ts=DETECTION_START + timedelta(hours=hour, minutes=minute),
        entity="owner/train",
        kind="pull_request",
        text=text,
        meta={
            "action": "closed",
            "merged": True,
            "pull_request_number": number,
            "url": f"https://example.test/pull/{number}",
        },
    )


def test_v4_labels_single_event_hours_and_attributes_all_sources() -> None:
    events = [
        _issue(0, 5, 1),
        _issue(0, 10, 2),
        _issue(1, 5, 3),
        _issue(
            1,
            15,
            1,
            action="closed",
            actor="maintainer",
            labels=["type: bug"],
            state_reason="completed",
        ),
        _pull(1, 20, 90, "Fixes #2"),
        _comment(2, 15, 3, "Confirmed regression; fixed on main."),
    ]

    dataset = build_labeled_dataset(events, _protocol(), split="train")

    assert dataset["class_balance"]["candidate_hours"] == 3
    assert dataset["class_balance"]["positive_incidents"] == 3
    assert dataset["source_attribution"]["by_source"] == {
        "completed_bug_closure": 1,
        "merged_fixing_pr": 1,
        "substantive_maintainer_comment": 1,
    }
    assert dataset["source_attribution"]["outside_old_spike_population"] == 3
    assert all(
        datetime.fromisoformat(row["candidate_ts"])
        < datetime.fromisoformat(row["confirmation_ts"])
        for row in dataset["positive_incidents"]
    )


def test_v4_rejects_pre_candidate_confirmation_and_reporter_closure() -> None:
    events = [
        _issue(0, 5, 1),
        _issue(
            0,
            30,
            1,
            action="closed",
            actor="maintainer",
            labels=["bug"],
            state_reason="completed",
        ),
        _issue(1, 5, 2),
        _issue(
            2,
            5,
            2,
            action="closed",
            actor="reporter",
            labels=["bug"],
            state_reason="completed",
        ),
    ]

    dataset = build_labeled_dataset(events, _protocol(), split="train")

    assert dataset["class_balance"]["candidate_hours"] == 2
    assert dataset["class_balance"]["positive_incidents"] == 0
    assert all(row["label"] == 0 for row in dataset["rows"])


def test_v4_audit_sample_is_deterministic_and_source_stratified() -> None:
    events = [
        _issue(0, 5, 1),
        _issue(
            1,
            5,
            1,
            action="closed",
            actor="maintainer",
            labels=["bug"],
            state_reason="completed",
        ),
    ]
    dataset = build_labeled_dataset(events, _protocol(), split="train")

    left = build_audit_candidates(dataset, _protocol())
    right = build_audit_candidates(dataset, _protocol())

    assert left == right
    assert left["samples"][0]["label_source"] == "completed_bug_closure"
    assert left["samples"][0]["status"] == "unreviewed"
