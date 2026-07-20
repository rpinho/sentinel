import json
from datetime import UTC, datetime, timedelta

from sentinel.stage2_v6 import (
    SYSTEM_PROMPT,
    automation_suspect,
    build_batches,
    build_candidate_request,
)
from sentinel.types import Event


CUTOFF = datetime(2025, 5, 5, 13, tzinfo=UTC)


def _protocol() -> dict:
    return {
        "study_id": "v6-test",
        "stage2": {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "transport": "subscription",
            "claude_cli_version": "2.1.215",
            "effort": "medium",
            "max_budget_usd_per_batch": 5.0,
            "stage1_threshold": 0.5,
            "stage1_detector_version": "sha256:test",
        },
        "evidence_policy": {
            "source_lookback_hours": 24,
            "recent_context_hours": 6,
            "event_budget_chars": 25_000,
            "max_event_text_chars": 2_500,
            "max_evidence_events": 10,
        },
        "batching": {"candidates_per_batch": 2},
    }


def _request(row_id: str, minute: int, events: list[Event]):
    return build_candidate_request(
        row={
            "row_id": row_id,
            "candidate_ts": (CUTOFF + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z"),
            "bucket_start": (CUTOFF + timedelta(minutes=minute - 60)).isoformat().replace("+00:00", "Z"),
            "entity": "example/repo",
            "features": {
                "breakage_issue_count": 1.0,
                "repeated_payload_share": 0.0,
                "top_actor_activity_share": 0.2,
            },
        },
        prediction={"probability": 0.8},
        events=events,
        protocol=_protocol(),
    ).to_dict()


def test_rich_request_has_multiple_events_and_strict_cutoff() -> None:
    events = [
        Event(
            ts=CUTOFF - timedelta(minutes=50 - index),
            entity="example/repo",
            kind="issue",
            text=f"Regression report {index}: application crashes repeatedly",
            meta={"issue_number": index, "author": f"human-{index}"},
        )
        for index in range(6)
    ] + [
        Event(
            ts=CUTOFF,
            entity="example/repo",
            kind="issue",
            text="post-cutoff",
        )
    ]

    request = _request("row:rich", 0, events)
    payload = json.loads(request["identity"]["user_content"])

    assert len(payload["pre_candidate_events"]) == 6
    assert all(event["ts"] < payload["candidate"]["candidate_ts"] for event in payload["pre_candidate_events"])
    assert "automation floods" in SYSTEM_PROMPT
    assert "llvmbot-style" in SYSTEM_PROMPT


def test_batches_are_chronological_and_automation_is_predeclared() -> None:
    bot_event = Event(
        ts=CUTOFF - timedelta(minutes=10),
        entity="example/repo",
        kind="comment",
        text="Automated issue mirror from llvmbot",
        meta={"author": "llvmbot", "author_type": "User"},
    )
    late = _request("row:late", 20, [bot_event])
    early = _request("row:early", 0, [bot_event])
    early["automation_suspect"] = automation_suspect(early)
    late["automation_suspect"] = automation_suspect(late)

    batches = build_batches([late, early], protocol=_protocol())

    assert len(batches) == 1
    assert batches[0]["row_ids"] == ["row:early", "row:late"]
    assert early["automation_suspect"]["suspect"] is True
    payload = json.loads(batches[0]["identity"]["user_content"])
    assert len(payload["candidates"]) == 2
    assert "row_id" not in payload["candidates"][0]["candidate"]
