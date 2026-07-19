import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from sentinel.cli import app
from sentinel.store import Firing, SentinelStore
from sentinel.triage import TriageOutput, TriageRun
from sentinel.types import Event

runner = CliRunner()
FIRING_TS = datetime(2025, 4, 7, 18, 27, 36, tzinfo=UTC)


def _firing() -> Firing:
    return Firing(
        firing_id="firing:cli-test",
        ts=FIRING_TS,
        entity="webpack/webpack",
        metric_id="issue_activity_per_hour",
        kind="metric",
        window_start=FIRING_TS.replace(minute=0, second=0),
        window_end=FIRING_TS.replace(minute=0, second=0) + timedelta(hours=1),
        observed=6,
        baseline_mean=1,
        baseline_stddev=1.4,
        score=3.5,
        detector_version="sha256:cli-test",
        meta={"bucket_total": 9, "baseline_counts": [0, 1, 1, 0]},
    )


def _event(
    ts: datetime, *, entity: str = "webpack/webpack", text: str = "v5.99.0 broke parsing"
) -> Event:
    return Event(
        ts=ts,
        entity=entity,
        kind="issue",
        text=text,
        meta={"action": "opened", "issue_number": 19394},
    )


def _seed_triage_db(path: Path) -> None:
    with SentinelStore(path) as store:
        store.insert_events(
            [
                _event(FIRING_TS - timedelta(minutes=10)),
                _event(FIRING_TS, text="same-timestamp answer must not leak"),
                _event(FIRING_TS + timedelta(hours=2), text="maintainer named PR 19398"),
                _event(
                    FIRING_TS - timedelta(minutes=5),
                    entity="other/repo",
                    text="other entity must not leak",
                ),
            ]
        )
        store.insert_firings([_firing()])
        store.submit_scorecard_feedback(
            _firing().firing_id,
            "false_positive",
            corrected_cause="known migration",
            note="reviewer feedback sentinel",
        )


def test_generic_jsonl_ingestion_accepts_text_or_value_optional(tmp_path: Path) -> None:
    db = tmp_path / "sentinel.duckdb"
    rows = [
        {
            "ts": "2025-04-07T13:13:08Z",
            "entity": "webpack",
            "kind": "release",
            "text": None,
            "value": None,
            "meta": {"version": "5.99.0"},
        },
        {
            "ts": "2025-04-07T00:00:00Z",
            "entity": "webpack",
            "kind": "metric",
            "value": 5_500_000,
            "meta": {"metric": "downloads_per_day"},
        },
    ]
    input_text = "".join(json.dumps(row) + "\n" for row in rows)

    result = runner.invoke(app, ["ingest", "jsonl", "--db", str(db)], input=input_text)

    assert result.exit_code == 0, result.output
    assert "events=2 inserted=2" in result.output
    with SentinelStore(db) as store:
        events = store.list_events(entity="webpack")
    assert len(events) == 2
    assert events[0].value == 5_500_000
    assert events[1].text is None


def test_feedback_miss_is_available_as_next_run_context(tmp_path: Path) -> None:
    db = tmp_path / "sentinel.duckdb"
    result = runner.invoke(
        app,
        [
            "feedback",
            "miss",
            "--entity",
            "webpack/webpack",
            "--metric",
            "opened_issues_per_hour",
            "--ts",
            "2025-04-07T18:04:49Z",
            "--cause",
            "v5.99.0 runtime regression",
            "--db",
            str(db),
        ],
    )
    context = runner.invoke(
        app,
        [
            "feedback",
            "context",
            "--entity",
            "webpack/webpack",
            "--db",
            str(db),
        ],
    )

    assert result.exit_code == 0, result.output
    assert context.exit_code == 0, context.output
    assert "false_negative" in context.output
    assert "v5.99.0 runtime regression" in context.output


def test_triage_modes_are_real_isolated_and_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sentinel.duckdb"
    _seed_triage_db(db)
    calls: list[dict[str, Any]] = []

    def fake_triage(
        firing: Firing, events: list[Event], **kwargs: Any
    ) -> TriageRun:
        calls.append({"firing": firing, "events": events, **kwargs})
        no_feedback = bool(kwargs["no_feedback"])
        return TriageRun(
            output=TriageOutput(
                real=True,
                cause="v5.99.0 parser regression",
                culprit_ref="v5.99.0",
                confidence=0.9,
                evidence_quotes=("v5.99.0 broke parsing",),
            ),
            mode="no_feedback" if no_feedback else "full",
            requested_model=str(kwargs["model"]),
            model="gpt-5.6-sol",
            prompt_hash="no-feedback-hash" if no_feedback else "full-hash",
            cache_hit=no_feedback,
            replayed=no_feedback,
            cache_source="cache" if no_feedback else "live",
            input_max_ts=max(event.ts for event in events),
            response_id="resp_cli",
        )

    monkeypatch.setattr("sentinel.pipeline.triage_firing", fake_triage)

    full = runner.invoke(
        app,
        ["triage", _firing().firing_id, "--db", str(db), "--json"],
    )
    no_feedback = runner.invoke(
        app,
        [
            "triage",
            _firing().firing_id,
            "--db",
            str(db),
            "--no-feedback",
            "--json",
        ],
    )
    before_no_agent_calls = len(calls)
    no_agent = runner.invoke(
        app,
        [
            "triage",
            _firing().firing_id,
            "--db",
            str(db),
            "--no-agent",
            "--json",
        ],
    )

    assert full.exit_code == 0, full.output
    assert no_feedback.exit_code == 0, no_feedback.output
    assert no_agent.exit_code == 0, no_agent.output
    full_json = json.loads(full.output)
    no_feedback_json = json.loads(no_feedback.output)
    no_agent_json = json.loads(no_agent.output)

    assert [event.ts for event in calls[0]["events"]] == [
        FIRING_TS - timedelta(minutes=10)
    ]
    assert all(event.entity == _firing().entity for event in calls[0]["events"])
    assert all(event.ts < FIRING_TS for event in calls[0]["events"])
    assert "reviewer feedback sentinel" in calls[0]["feedback_context"]
    assert calls[0]["no_feedback"] is False
    assert calls[1]["feedback_context"] is None
    assert calls[1]["no_feedback"] is True
    assert len(calls) == before_no_agent_calls

    assert full_json["mode"] == "full"
    assert full_json["prompt_hash"] == "full-hash"
    assert full_json["input_max_ts"] == "2025-04-07T18:17:36Z"
    assert full_json["strict_pre_firing"] is True
    assert no_feedback_json["mode"] == "no_feedback"
    assert no_feedback_json["prompt_hash"] == "no-feedback-hash"
    assert no_feedback_json["prompt_hash"] != full_json["prompt_hash"]
    assert no_agent_json["mode"] == "no_agent"
    assert no_agent_json["cause"] is None
    assert no_agent_json["culprit_ref"] is None
    assert no_agent_json["prompt_hash"] is None

    with SentinelStore(db) as store:
        persisted = store.list_triage_results(firing_id=_firing().firing_id)
    assert {result.mode for result in persisted} == {
        "full",
        "no_feedback",
        "no_agent",
    }


def test_detect_no_agent_never_calls_triage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "sentinel.duckdb"
    with SentinelStore(db) as store:
        store.insert_events([_event(FIRING_TS - timedelta(minutes=10))])

    monkeypatch.setattr(
        "sentinel.cli.detect_bursts", lambda *args, **kwargs: [_firing()]
    )

    def unexpected_triage(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("--no-agent called triage")

    monkeypatch.setattr("sentinel.pipeline.triage_firing", unexpected_triage)
    result = runner.invoke(
        app, ["detect", "--db", str(db), "--no-agent", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.splitlines()[-1])
    assert payload["triage"]["mode"] == "no_agent"
    assert payload["triage"]["cause"] is None
    with SentinelStore(db) as store:
        persisted = store.latest_triage_result(
            _firing().firing_id, mode="no_agent"
        )
    assert persisted is not None
    assert persisted.cause is None


def test_ablation_flags_cannot_be_combined() -> None:
    result = runner.invoke(
        app,
        ["triage", "firing:any", "--no-agent", "--no-feedback"],
    )

    assert result.exit_code != 0
    assert "cannot be combined" in result.output
