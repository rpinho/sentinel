import json
from pathlib import Path

from typer.testing import CliRunner

from sentinel.cli import app
from sentinel.store import SentinelStore

runner = CliRunner()


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
