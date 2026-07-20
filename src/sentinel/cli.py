"""Command-line interface for ingestion, detection, corroboration, and feedback."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator, Literal, cast

import typer

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.adapters.npm import NpmAdapter
from sentinel.corroborate import corroborate_release
from sentinel.detect import DetectorConfig, detect_bursts, evaluate_detection
from sentinel.feedback import render_feedback_context
from sentinel.pipeline import execute_triage
from sentinel.store import Firing, IngestionWindow, SentinelStore, TriageRecord
from sentinel.triage import (
    DEFAULT_CACHE_DIR,
    DEFAULT_FIXTURE_DIR,
    DEFAULT_MODEL,
)
from sentinel.types import Event, format_timestamp, parse_timestamp

app = typer.Typer(
    name="sentinel",
    help="An anomaly detector that publishes and learns from its own scorecard.",
    no_args_is_help=True,
)
ingest_app = typer.Typer(
    help="Ingest normalized events from a source.", no_args_is_help=True
)
feedback_app = typer.Typer(
    help="Append scorecard labels, corrections, and missed anomalies.",
    no_args_is_help=True,
)
app.add_typer(ingest_app, name="ingest")
app.add_typer(feedback_app, name="feedback")


@ingest_app.command("gharchive")
def ingest_gharchive(
    repo: str = typer.Option(..., "--repo", help="GitHub owner/repo to retain."),
    start: str = typer.Option(..., help="Inclusive ISO-8601 UTC timestamp."),
    end: str = typer.Option(..., help="Exclusive ISO-8601 UTC timestamp."),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
    cache_dir: Path = typer.Option(
        Path(".cache/gharchive"), help="Raw archive cache directory."
    ),
    force: bool = typer.Option(False, help="Re-download cached archive hours."),
) -> None:
    """Fetch consecutive GH Archive hours and retain one repository's events."""

    start_at = parse_timestamp(start)
    end_at = parse_timestamp(end)
    hours = list(_hours(start_at, end_at))
    if not hours:
        raise typer.BadParameter("end must be later than start")
    adapter = GHArchiveAdapter(cache_dir=cache_dir)
    with SentinelStore(db) as store:
        for hour in hours:
            try:
                events = list(adapter.iter_events(hour, entity=repo, force=force))
            except Exception as exc:
                store.record_ingestion_window(
                    IngestionWindow(
                        source="gharchive",
                        entity=repo,
                        window_start=hour,
                        window_end=hour + timedelta(hours=1),
                        status="failed",
                        normalized_events=0,
                        meta={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )
                raise
            inserted = store.insert_events(events)
            archive_path = adapter.cache_path(hour)
            source_bytes = archive_path.stat().st_size
            store.record_ingestion_window(
                IngestionWindow(
                    source="gharchive",
                    entity=repo,
                    window_start=hour,
                    window_end=hour + timedelta(hours=1),
                    status="complete",
                    normalized_events=len(events),
                    source_bytes=source_bytes,
                    meta={
                        "archive": archive_path.name,
                        "url": adapter.archive_url(hour),
                    },
                )
            )
            typer.echo(
                f"GHARCHIVE {format_timestamp(hour)} repo={repo} "
                f"events={len(events)} inserted={inserted} bytes={source_bytes}"
            )


@ingest_app.command("npm")
def ingest_npm(
    package: str = typer.Option(..., "--package", help="npm package name."),
    start: str = typer.Option(..., help="Inclusive UTC date (YYYY-MM-DD)."),
    end: str = typer.Option(..., help="Exclusive UTC date (YYYY-MM-DD)."),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Ingest registry releases and daily download counts from npm."""

    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise typer.BadParameter("start and end must use YYYY-MM-DD") from exc
    if end_date <= start_date:
        raise typer.BadParameter("end must be later than start")
    start_at = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_at = datetime.combine(end_date, time.min, tzinfo=UTC)
    adapter = NpmAdapter()
    releases = list(adapter.iter_releases(package, start=start_at, end=end_at))
    downloads = list(
        adapter.iter_downloads(package, start_date, end_date - timedelta(days=1))
    )
    with SentinelStore(db) as store:
        release_inserted = store.insert_events(releases)
        download_inserted = store.insert_events(downloads)
        store.record_ingestion_window(
            IngestionWindow(
                source="npm_registry",
                entity=package,
                window_start=start_at,
                window_end=end_at,
                status="complete",
                normalized_events=len(releases),
            )
        )
        store.record_ingestion_window(
            IngestionWindow(
                source="npm_downloads",
                entity=package,
                window_start=start_at,
                window_end=end_at,
                status="complete",
                normalized_events=len(downloads),
            )
        )
    typer.echo(
        f"NPM package={package} releases={len(releases)} inserted={release_inserted} "
        f"download_days={len(downloads)} inserted={download_inserted}"
    )


@ingest_app.command("jsonl")
def ingest_jsonl(
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Read generic Event objects as JSONL from standard input."""

    events: list[Event] = []
    for line_number, line in enumerate(sys.stdin, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("each JSONL row must be an object")
            events.append(Event.from_dict(raw))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise typer.BadParameter(
                f"invalid event on stdin line {line_number}: {exc}"
            ) from exc
    with SentinelStore(db) as store:
        inserted = store.insert_events(events)
    typer.echo(f"JSONL events={len(events)} inserted={inserted}")


@app.command("detect")
def detect(
    entity: str | None = typer.Option(None, help="Limit detection to one entity."),
    start: str | None = typer.Option(None, help="Inclusive ISO-8601 UTC timestamp."),
    end: str | None = typer.Option(None, help="Exclusive ISO-8601 UTC timestamp."),
    config_path: Path | None = typer.Option(
        None, "--config", help="Versioned detector TOML; defaults to packaged config."
    ),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
    json_output: bool = typer.Option(False, "--json", help="Emit firing JSONL."),
    no_agent: bool = typer.Option(
        False, "--no-agent", help="Detector-only ablation; do not call GPT triage."
    ),
    no_feedback: bool = typer.Option(
        False,
        "--no-feedback",
        help="Run GPT triage without prior scorecard feedback.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, help="OpenAI model for causal triage."),
    triage_cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR, "--triage-cache-dir", help="Exact-response cache directory."
    ),
    triage_fixture_dir: Path = typer.Option(
        DEFAULT_FIXTURE_DIR,
        "--triage-fixture-dir",
        help="Committed triage replay fixture directory.",
    ),
) -> None:
    """Run deterministic detection, then causal triage unless ablated."""

    _validate_ablation_flags(no_agent=no_agent, no_feedback=no_feedback)
    start_at = parse_timestamp(start) if start else None
    end_at = parse_timestamp(end) if end else None
    config = DetectorConfig.load(config_path)
    with SentinelStore(db) as store:
        history_start = (
            start_at - timedelta(days=config.phase_history_days)
            if start_at and config.algorithm == "robust_rate_v2"
            else start_at
        )
        events = store.list_events(entity=entity, start=history_start, end=end_at)
        if config.algorithm == "robust_rate_v2":
            run = evaluate_detection(events, config, start=start_at, end=end_at)
            firings = list(run.firings)
            diagnostics = run.verdict_counts
        else:
            firings = detect_bursts(events, config, start=start_at, end=end_at)
            diagnostics = {}
        inserted = store.insert_firings(firings)
        if entity and start_at and end_at:
            _print_gharchive_coverage(store, entity, start_at, end_at)
        typer.echo(
            f"DETECTOR config={config.version} events={len(events)} "
            f"firings={len(firings)} inserted={inserted} "
            f"diagnostics={json.dumps(diagnostics, sort_keys=True)}"
        )
        for firing in firings:
            triage, triage_inserted = execute_triage(
                store,
                firing,
                no_agent=no_agent,
                no_feedback=no_feedback,
                model=model,
                cache_dir=triage_cache_dir,
                fixture_dir=triage_fixture_dir,
            )
            _print_firing(
                firing,
                triage,
                triage_inserted=triage_inserted,
                json_output=json_output,
            )


@app.command("triage")
def triage_command(
    firing_id: str = typer.Argument(..., help="Persisted firing to triage."),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
    no_agent: bool = typer.Option(
        False, "--no-agent", help="Detector-only ablation; do not call GPT triage."
    ),
    no_feedback: bool = typer.Option(
        False,
        "--no-feedback",
        help="Run GPT triage without prior scorecard feedback.",
    ),
    model: str = typer.Option(DEFAULT_MODEL, help="OpenAI model for causal triage."),
    cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR, help="Exact-response cache directory."
    ),
    fixture_dir: Path = typer.Option(
        DEFAULT_FIXTURE_DIR, help="Committed triage replay fixture directory."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit result as JSON."),
) -> None:
    """Triage one persisted firing using strictly earlier same-entity events."""

    _validate_ablation_flags(no_agent=no_agent, no_feedback=no_feedback)
    with SentinelStore(db) as store:
        firing = store.get_firing(firing_id)
        if firing is None:
            raise typer.BadParameter(f"unknown firing_id: {firing_id}")
        triage, inserted = execute_triage(
            store,
            firing,
            no_agent=no_agent,
            no_feedback=no_feedback,
            model=model,
            cache_dir=cache_dir,
            fixture_dir=fixture_dir,
        )
    _print_triage(
        firing, triage, inserted=inserted, json_output=json_output
    )


@app.command("corroborate-release")
def corroborate_release_command(
    repo: str = typer.Option(..., "--repo"),
    package: str = typer.Option(..., "--package"),
    version: str = typer.Option(..., "--version"),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Print independent GitHub/npm publish timestamps side by side."""

    with SentinelStore(db) as store:
        events = store.list_events(kinds=["release"])
    result = corroborate_release(events, repo=repo, package=package, version=version)
    if result is None:
        raise typer.BadParameter(
            "matching GitHub and npm release events were not both found"
        )
    typer.echo(f"RELEASE {result.version}")
    typer.echo(f"  GH Archive  {format_timestamp(result.github.ts)}")
    typer.echo(f"  npm registry {format_timestamp(result.npm.ts)}")
    typer.echo(f"  agreement    {result.difference_seconds:.0f}s apart")


@feedback_app.command("scorecard")
def feedback_scorecard(
    firing_id: str = typer.Argument(...),
    outcome: str = typer.Option(
        ..., help="true_positive, false_positive, or needs_review."
    ),
    reason_code: str | None = typer.Option(None),
    corrected_cause: str | None = typer.Option(None),
    note: str | None = typer.Option(None),
    submitted_by: str | None = typer.Option(None),
    source: str = typer.Option("human"),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Append a human or automatic verdict to an existing firing."""

    allowed = {"true_positive", "false_positive", "needs_review"}
    if outcome not in allowed:
        raise typer.BadParameter(
            "outcome must be true_positive, false_positive, or needs_review"
        )
    with SentinelStore(db) as store:
        feedback = store.submit_scorecard_feedback(
            firing_id,
            cast(Literal["true_positive", "false_positive", "needs_review"], outcome),
            corrected_cause=corrected_cause,
            reason_code=reason_code,
            note=note,
            submitted_by=submitted_by,
            source=source,
        )
    typer.echo(f"FEEDBACK {feedback.feedback_id} outcome={feedback.outcome}")


@feedback_app.command("miss")
def feedback_miss(
    entity: str = typer.Option(...),
    metric_id: str = typer.Option(..., "--metric"),
    ts: str = typer.Option(..., help="When the missed incident began."),
    corrected_cause: str = typer.Option(..., "--cause"),
    reason_code: str | None = typer.Option(None),
    note: str | None = typer.Option(None),
    submitted_by: str | None = typer.Option(None),
    source: str = typer.Option("human"),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Append a false negative even though no firing exists to annotate."""

    with SentinelStore(db) as store:
        feedback = store.report_missed_anomaly(
            entity=entity,
            metric_id=metric_id,
            ts=parse_timestamp(ts),
            corrected_cause=corrected_cause,
            reason_code=reason_code,
            note=note,
            submitted_by=submitted_by,
            source=source,
        )
    typer.echo(f"FEEDBACK {feedback.feedback_id} outcome=false_negative")


@feedback_app.command("context")
def feedback_context(
    entity: str = typer.Option(...),
    metric_id: str | None = typer.Option(None, "--metric"),
    limit: int = typer.Option(10, min=1, max=100),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Render exactly the active feedback context a triage call will consume."""

    with SentinelStore(db) as store:
        feedback = store.relevant_feedback(entity, metric_id=metric_id, limit=limit)
    typer.echo(render_feedback_context(feedback))


@app.command("coverage")
def coverage(
    entity: str = typer.Option(...),
    source: str | None = typer.Option(None),
    db: Path = typer.Option(Path("sentinel.duckdb"), help="DuckDB database path."),
) -> None:
    """Show persisted source-window coverage rather than assuming completeness."""

    with SentinelStore(db) as store:
        windows = store.list_ingestion_windows(entity=entity, source=source)
    for window in windows:
        typer.echo(
            f"{window.source} {format_timestamp(window.window_start)}.."
            f"{format_timestamp(window.window_end)} status={window.status} "
            f"events={window.normalized_events} bytes={window.source_bytes or 0}"
        )


def _validate_ablation_flags(*, no_agent: bool, no_feedback: bool) -> None:
    if no_agent and no_feedback:
        raise typer.BadParameter(
            "--no-agent and --no-feedback are separate ablations and cannot be combined"
        )


def _print_firing(
    firing: Firing,
    triage: TriageRecord,
    *,
    triage_inserted: bool,
    json_output: bool,
) -> None:
    if json_output:
        payload = {
            "firing_id": firing.firing_id,
            "ts": format_timestamp(firing.ts),
            "entity": firing.entity,
            "metric_id": firing.metric_id,
            "observed_at_fire": firing.observed,
            "bucket_total": firing.meta["bucket_total"],
            "baseline_mean": firing.baseline_mean,
            "baseline_stddev": firing.baseline_stddev,
            "z_score": firing.score,
            "detector_version": firing.detector_version,
            "triage": _triage_payload(
                firing, triage, inserted=triage_inserted
            ),
        }
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    typer.echo(
        f"FIRE {format_timestamp(firing.ts)} entity={firing.entity} "
        f"metric={firing.metric_id} observed={int(firing.observed)} "
        f"bucket_total={firing.meta['bucket_total']} "
        f"baseline={firing.baseline_mean:.2f} z={firing.score:.2f} "
        f"id={firing.firing_id}"
    )
    _print_triage(firing, triage, inserted=triage_inserted, json_output=False)


def _print_triage(
    firing: Firing,
    triage: TriageRecord,
    *,
    inserted: bool,
    json_output: bool,
) -> None:
    payload = _triage_payload(firing, triage, inserted=inserted)
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(
        f"TRIAGE firing={triage.firing_id} mode={triage.mode} "
        f"model={triage.model or 'none'} cache_hit={str(triage.cache_hit).lower()} "
        f"replayed={str(triage.replayed).lower()} "
        f"prompt_hash={triage.prompt_hash or 'none'} "
        f"persisted={str(inserted).lower()}"
    )
    typer.echo(
        f"INPUT firing_ts={payload['firing_ts']} "
        f"max_event_ts={payload['input_max_ts'] or 'none'} "
        f"strict_before={str(payload['strict_pre_firing']).lower()}"
    )
    typer.echo(
        f"RESULT real={_display(triage.real)} "
        f"culprit_ref={triage.culprit_ref or 'none'} "
        f"confidence={_display(triage.confidence)} "
        f"cause={triage.cause or 'none'}"
    )
    for quote in triage.evidence_quotes:
        typer.echo(f"EVIDENCE {json.dumps(quote, ensure_ascii=False)}")


def _triage_payload(
    firing: Firing, triage: TriageRecord, *, inserted: bool
) -> dict[str, object]:
    strict_pre_firing = (
        triage.input_max_ts is None or triage.input_max_ts < firing.ts
    )
    return {
        "triage_id": triage.triage_id,
        "firing_id": triage.firing_id,
        "firing_ts": format_timestamp(firing.ts),
        "mode": triage.mode,
        "real": triage.real,
        "cause": triage.cause,
        "culprit_ref": triage.culprit_ref,
        "confidence": triage.confidence,
        "evidence_quotes": list(triage.evidence_quotes),
        "model": triage.model,
        "requested_model": triage.requested_model,
        "prompt_hash": triage.prompt_hash,
        "cache_hit": triage.cache_hit,
        "replayed": triage.replayed,
        "input_max_ts": (
            format_timestamp(triage.input_max_ts) if triage.input_max_ts else None
        ),
        "strict_pre_firing": strict_pre_firing,
        "response_id": triage.response_id,
        "persisted": inserted,
    }


def _display(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _hours(start: datetime, end: datetime) -> Iterator[datetime]:
    current = start.replace(minute=0, second=0, microsecond=0)
    while current < end:
        yield current
        current += timedelta(hours=1)


def _print_gharchive_coverage(
    store: SentinelStore, entity: str, start: datetime, end: datetime
) -> None:
    expected = set(_hours(start, end))
    windows = store.list_ingestion_windows(
        entity=entity,
        source="gharchive",
        start=min(expected),
        end=max(expected) + timedelta(hours=1),
    )
    complete = {
        window.window_start for window in windows if window.status == "complete"
    }
    missing = sorted(expected - complete)
    if missing:
        typer.echo(
            "COVERAGE WARNING "
            f"complete={len(complete)}/{len(expected)} missing="
            + ",".join(format_timestamp(hour) for hour in missing),
            err=True,
        )
    else:
        typer.echo(f"COVERAGE gharchive complete={len(complete)}/{len(expected)} hours")


if __name__ == "__main__":
    app()
