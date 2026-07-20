from datetime import UTC, datetime, timedelta

from sentinel.detector_v3 import (
    MODEL_FEATURES,
    apply_label_audit,
    build_labeled_dataset,
)
from sentinel.types import Event

ARCHIVE_START = datetime(2025, 1, 1, tzinfo=UTC)
DETECTION_START = ARCHIVE_START + timedelta(days=7)


def _protocol() -> dict[str, object]:
    return {
        "study_id": "synthetic-v3",
        "repositories": {
            "train": ["owner/train"],
            "test": ["owner/test"],
        },
        "windows": [
            {
                "window_id": "synthetic",
                "archive_start": ARCHIVE_START.isoformat(),
                "detection_start": DETECTION_START.isoformat(),
                "detection_end": (DETECTION_START + timedelta(hours=2)).isoformat(),
                "acknowledgment_end": (
                    DETECTION_START + timedelta(days=3, hours=2)
                ).isoformat(),
            }
        ],
        "label_rule": {
            "version": "maintainer-substantive-v3",
            "minimum_raw_activity": 5,
            "acknowledgment_lookahead_hours": 72,
        },
        "feature_policy": {
            "history_days": 7,
            "release_window_hours": 6,
        },
    }


def _event(
    minute: int,
    *,
    kind: str,
    issue: int,
    text: str,
    actor: str,
    entity: str = "owner/test",
    offset_hours: int = 0,
    association: str | None = None,
) -> Event:
    meta: dict[str, object] = {
        "action": "opened" if kind == "issue" else "created",
        "issue_number": issue,
        "author": actor,
        "actor": actor,
        "author_type": "User",
        "actor_type": "User",
        "url": f"https://github.com/{entity}/issues/{issue}",
    }
    if association:
        meta["author_association"] = association
    return Event(
        ts=DETECTION_START + timedelta(hours=offset_hours, minutes=minute),
        entity=entity,
        kind=kind,  # type: ignore[arg-type]
        text=text,
        meta=meta,
    )


def test_v3_enumerates_incidents_without_detector_firings_and_is_temporal() -> None:
    events = [
        _event(
            2,
            kind="issue",
            issue=7,
            text="Regression: build crashes with module parse error",
            actor="reporter-a",
        ),
        _event(
            8,
            kind="issue",
            issue=8,
            text="Build crashes with the same module parse error",
            actor="reporter-b",
        ),
        _event(14, kind="comment", issue=7, text="same crash", actor="user-c"),
        _event(20, kind="comment", issue=8, text="same stack", actor="user-d"),
        _event(26, kind="comment", issue=7, text="reproduces here", actor="user-e"),
        _event(
            5,
            kind="comment",
            issue=7,
            text="Confirmed regression; fixed in #9",
            actor="maintainer",
            association="MEMBER",
            offset_hours=1,
        ),
    ]

    dataset = build_labeled_dataset(events, _protocol())

    assert dataset["class_balance"]["all"]["candidate_hours"] == 1
    assert dataset["class_balance"]["all"]["positive_incidents"] == 1
    row = dataset["rows"][0]
    incident = dataset["positive_incidents"][0]
    assert row["label"] == 1
    assert set(row["features"]) == set(MODEL_FEATURES)
    assert row["features"]["cross_thread_tfidf_max"] > 0
    assert incident["issue_number"] == 7
    assert datetime.fromisoformat(incident["candidate_ts"]) < datetime.fromisoformat(
        incident["ack_ts"]
    )


def test_v3_treats_unacknowledged_spike_as_negative() -> None:
    events = [
        _event(
            minute,
            kind="comment",
            issue=100 + minute,
            text="routine maintenance",
            actor=f"human-{minute}",
            entity="owner/train",
        )
        for minute in (1, 2, 3, 4, 5)
    ]

    dataset = build_labeled_dataset(events, _protocol())

    assert dataset["class_balance"]["train"]["candidate_hours"] == 1
    assert dataset["class_balance"]["train"]["positive_incidents"] == 0
    assert dataset["rows"][0]["label"] == 0


def test_v3_audit_preserves_auto_label_but_rejects_false_match() -> None:
    events = [
        _event(
            2,
            kind="issue",
            issue=7,
            text="Regression: build crashes",
            actor="reporter-a",
        ),
        _event(8, kind="comment", issue=7, text="same crash", actor="user-b"),
        _event(14, kind="comment", issue=8, text="routine", actor="user-c"),
        _event(20, kind="comment", issue=9, text="routine", actor="user-d"),
        _event(26, kind="comment", issue=10, text="routine", actor="user-e"),
        _event(
            5,
            kind="comment",
            issue=7,
            text="Can you see if Insiders fixed this?",
            actor="maintainer",
            association="MEMBER",
            offset_hours=1,
        ),
    ]
    raw = build_labeled_dataset(events, _protocol())
    audit = {
        "schema_version": 1,
        "audit_version": "test-audit",
        "policy": "test",
        "counts": {"accepted": 0, "ambiguous": 0, "rejected": 1},
        "obvious_label_noise_rate": 1.0,
        "reviews": [
            {
                "entity": "owner/test",
                "issue_number": 7,
                "status": "rejected",
                "reason": "question, not acknowledgment",
            }
        ],
    }

    audited = apply_label_audit(raw, audit)

    assert audited["rows"][0]["auto_label"] == 1
    assert audited["rows"][0]["label"] == 0
    assert audited["positive_incidents"] == []
    assert audited["auto_label_candidates"][0]["audit"]["status"] == "rejected"
