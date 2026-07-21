import json
from datetime import UTC, datetime, timedelta

from sentinel.stage2_v7 import (
    _ground_investigation,
    build_investigation_requests,
    build_screen_requests,
    build_signal_candidates,
    candidate_id,
)
from sentinel.types import Event


CUTOFF = datetime(2025, 5, 5, 13, tzinfo=UTC)


def _protocol() -> dict:
    return {
        "study_id": "v7-test",
        "candidate_policy": {
            "event_type_absolute_floors": {"issue": 2, "comment": 4, "pull_request": 4},
            "minimum_rate_robust_z": 3.0,
            "release_lookback_hours": 6,
            "max_candidates_per_stage1_hour": 12,
            "thread_cooldown_hours": 6,
            "rate_cooldown_hours": 3,
        },
        "stage2": {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "transport": "subscription",
            "claude_cli_version": "2.1.215",
            "screen_effort": "low",
            "investigation_effort": "medium",
            "max_budget_usd_per_screen_batch": 3.0,
            "max_budget_usd_per_investigation_batch": 5.0,
        },
        "batching": {
            "screen_candidates_per_batch": 200,
            "investigation_candidates_per_batch": 20,
        },
        "evidence_policy": {
            "heavy_lookback_hours": 24,
            "max_heavy_events": 8,
            "max_heavy_text_chars": 1200,
        },
    }


def _row() -> dict:
    return {
        "row_id": "v4row:test",
        "entity": "example/repo",
        "bucket_start": (CUTOFF - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "candidate_ts": CUTOFF.isoformat().replace("+00:00", "Z"),
    }


def _issue(number: int, text: str, *, actor: str = "human", minutes: int = 30) -> Event:
    return Event(
        ts=CUTOFF - timedelta(minutes=minutes),
        entity="example/repo",
        kind="issue",
        text=text,
        meta={
            "action": "opened",
            "issue_number": number,
            "is_pull_request": False,
            "author": actor,
            "author_type": "User",
            "url": f"https://github.com/example/repo/issues/{number}",
        },
    )


def test_candidate_is_signal_scoped_and_automation_dies_before_screen() -> None:
    events = [
        _issue(12, "Regression: application crashes on startup"),
        _issue(13, "Automated issue mirror failure", actor="llvmbot"),
    ]
    candidates = build_signal_candidates(
        rows=[_row()],
        predictions=[{"row_id": "v4row:test", "predicted": True, "probability": 0.9}],
        events=events,
        protocol=_protocol(),
    )
    threads = [item for item in candidates if item["identity"]["series"] == "issue_thread_activity"]
    assert {item["identity"]["segment"] for item in threads} == {"issue:12", "issue:13"}
    human = next(item for item in threads if item["identity"]["segment"] == "issue:12")
    bot = next(item for item in threads if item["identity"]["segment"] == "issue:13")
    assert human["deterministic_disposition"] == "eligible"
    assert bot["deterministic_disposition"] == "benign_dismiss"
    assert bot["deterministic_reason_code"] == "automation"
    assert human["candidate_id"] == candidate_id(
        "issue_thread_activity", "example/repo:issue:12", human["bucket_start"]
    )

    requests, batches = build_screen_requests(candidates, protocol=_protocol())
    assert len(requests) == 1
    payload = json.loads(batches[0]["identity"]["user_content"])
    assert payload["run"]["candidate_count"] == 1
    assert "label" not in payload["candidates"][0]


def test_heavy_pack_is_selective_bounded_and_self_documenting() -> None:
    events = [_issue(12, "Regression: application crashes on startup")]
    candidates = build_signal_candidates(
        rows=[_row()],
        predictions=[{"row_id": "v4row:test", "predicted": True, "probability": 0.9}],
        events=events,
        protocol=_protocol(),
    )
    candidate = next(item for item in candidates if item["identity"]["segment"] == "issue:12")
    screen_records = [
        {
            "candidate_id": item["candidate_id"],
            "response": {
                "disposition": "investigate" if item is candidate else "benign_dismiss"
            },
        }
        for item in candidates
        if item["deterministic_disposition"] == "eligible"
    ]
    requests, batches = build_investigation_requests(
        candidates=candidates,
        screen_records=screen_records,
        events=events,
        protocol=_protocol(),
    )
    assert len(requests) == 1
    pack = requests[0]["packet"]
    assert "methodology" in pack
    assert pack["methodology"]["baseline_quality"]["comparison_windows"] == 24
    assert len(pack["evidence_excerpts"]) == 1
    assert "meta" not in pack["evidence_excerpts"][0]
    assert batches[0]["phase"] == "investigation"


def test_grounding_downgrades_unquoted_findings_and_unverified_attribution() -> None:
    request = {
        "packet": {
            "evidence_excerpts": [{"excerpt": "application crashes on startup"}],
            "attribution_drill": {"verified": False},
        }
    }
    response = {
        "disposition": "finding",
        "severity": "high",
        "reason_code": "confirmed_breakage",
        "attribution_status": "verified",
        "culprit_ref": "v9.9",
        "evidence_quotes": ["invented quote"],
    }
    grounded, audit = _ground_investigation(response, request)
    assert grounded["disposition"] == "insufficient_data"
    assert grounded["severity"] == "none"
    assert grounded["attribution_status"] == "correlation_only"
    assert grounded["culprit_ref"] is None
    assert {item["method"] for item in audit} == {
        "dropped_not_in_bounded_evidence",
        "downgraded_unverified_attribution",
        "downgraded_finding_without_exact_quote",
    }
