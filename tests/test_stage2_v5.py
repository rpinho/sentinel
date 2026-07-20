import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sentinel.stage2_v5 import (
    DEFAULT_EVENT_BUDGET_CHARS,
    DEFAULT_MODEL,
    build_sample,
    build_stage2_request,
    run_claude_subscription_request,
    select_evidence,
)
from sentinel.types import Event


CUTOFF = datetime(2025, 5, 5, 13, tzinfo=UTC)


def _event(
    minutes: int,
    *,
    kind: str = "issue",
    text: str = "Regression: application crashes after upgrade",
    author: str = "person",
) -> Event:
    return Event(
        ts=CUTOFF + timedelta(minutes=minutes),
        entity="example/repo",
        kind=kind,  # type: ignore[arg-type]
        text=text,
        meta={"author": author},
    )


def _protocol() -> dict[str, Any]:
    return {
        "stage2": {
            "provider": "anthropic",
            "model": DEFAULT_MODEL,
            "transport": "claude-code-subscription-cli-v1",
            "claude_cli_version": "2.1.215",
            "stage1_threshold": 0.4,
            "stage1_detector_version": "sha256:test",
            "max_budget_usd_per_call": 0.5,
        },
        "evidence_policy": {
            "source_lookback_hours": 24,
            "recent_context_hours": 3,
            "event_budget_chars": DEFAULT_EVENT_BUDGET_CHARS,
            "max_event_text_chars": 1_000,
            "max_evidence_events": 8,
        },
    }


def test_sample_is_deterministic_and_censuses_positive_alerts() -> None:
    rows = []
    predictions = []
    for index in range(20):
        row_id = f"row:{index}"
        rows.append(
            {
                "row_id": row_id,
                "entity": "example/repo",
                "label": 1 if index < 3 else 0,
            }
        )
        predictions.append(
            {
                "row_id": row_id,
                "predicted": index != 19,
                "probability": index / 20,
            }
        )

    left = build_sample(rows, predictions, negatives_per_quartile=2)
    right = build_sample(rows, predictions, negatives_per_quartile=2)

    assert left == right
    assert left["positive_alert_census"] == 3
    assert left["negative_alert_population"] == 16
    assert left["negative_alert_sample"] == 8
    assert {row["row_id"] for row in left["rows"] if row["stratum"] == "positive_census"} == {
        "row:0",
        "row:1",
        "row:2",
    }
    assert all("label" not in row for row in left["rows"])


def test_evidence_is_strictly_pre_cutoff_and_compact() -> None:
    events = [
        _event(-25 * 60),
        _event(-120, kind="release", text="Release 2.0"),
        _event(-50, text="x" * 3_000),
        *[_event(-index, kind="comment", text=f"context {index}") for index in range(1, 20)],
        _event(0, text="not allowed at cutoff"),
        _event(5, text="future"),
    ]

    selected, manifest = select_evidence(
        events,
        candidate_ts=CUTOFF,
        bucket_start=CUTOFF - timedelta(hours=1),
    )

    assert len(selected) <= 8
    assert manifest["supplied_event_count"] == len(selected)
    assert manifest["supplied_event_json_chars"] <= DEFAULT_EVENT_BUDGET_CHARS
    assert all(event.ts < CUTOFF for event in selected)
    assert all(event.text not in {"not allowed at cutoff", "future"} for event in selected)
    assert any(event.kind == "release" for event in selected)
    truncated = next(event for event in selected if event.text and event.text.startswith("x"))
    assert len(truncated.text or "") == 1_000
    assert truncated.meta["v5_text"]["truncated"] is True


def test_request_omits_labels_and_unselected_history() -> None:
    row = {
        "row_id": "row:test",
        "candidate_ts": "2025-05-05T13:00:00Z",
        "bucket_start": "2025-05-05T12:00:00Z",
        "entity": "example/repo",
        "features": {"activity_z": 4.2},
        "label": 1,
        "incident_ids": ["secret-evaluation-id"],
    }
    request = build_stage2_request(
        row=row,
        prediction={"probability": 0.8},
        events=[_event(-5), _event(-24 * 60 - 1)],
        protocol=_protocol(),
    )
    payload = json.loads(str(request.identity["user_content"]))

    assert request.identity["model"] == "claude-opus-4-8"
    assert "label" not in payload["candidate"]
    assert "incident_ids" not in payload["candidate"]
    assert "secret-evaluation-id" not in request.identity["user_content"]
    assert len(payload["pre_candidate_events"]) == 1


def test_subscription_runner_filters_api_keys_and_replays_exact_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = build_stage2_request(
        row={
            "row_id": "row:test",
            "candidate_ts": "2025-05-05T13:00:00Z",
            "bucket_start": "2025-05-05T12:00:00Z",
            "entity": "example/repo",
            "features": {"activity_z": 4.2},
        },
        prediction={"probability": 0.8},
        events=[_event(-5)],
        protocol=_protocol(),
    ).to_dict()
    calls: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": {
                        "real": True,
                        "cause": "The supplied report describes a crash.",
                        "culprit_ref": None,
                        "confidence": 0.8,
                        "evidence_quotes": ["application crashes after upgrade"],
                    },
                    "duration_ms": 10,
                    "duration_api_ms": 8,
                    "total_cost_usd": 0.01,
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                    "modelUsage": {"claude-opus-4-8": {"inputTokens": 100}},
                    "num_turns": 1,
                }
            ),
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setattr("sentinel.stage2_v5.subprocess.run", fake_run)

    live = run_claude_subscription_request(
        request, cache_dir=tmp_path, allow_live=True
    )
    replay = run_claude_subscription_request(
        request, cache_dir=tmp_path, allow_live=False
    )

    assert live == replay
    assert len(calls) == 1
    assert calls[0]["env"].get("ANTHROPIC_API_KEY") is None
    assert calls[0]["env"].get("ANTHROPIC_AUTH_TOKEN") is None
    assert calls[0]["env"].get("OPENAI_API_KEY") is None
    assert "--model" in calls[0]["command"]
    assert DEFAULT_MODEL in calls[0]["command"]
