"""DuckDB persistence for normalized events, firings, and learning feedback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence, cast
from uuid import uuid4

import duckdb

from sentinel.types import Event, EventKind, parse_timestamp

FeedbackOutcome = Literal[
    "true_positive", "false_positive", "false_negative", "needs_review"
]
FeedbackVerdict = Literal["TRUE", "FALSE", "UNRESOLVED"]
FEEDBACK_OUTCOMES = frozenset(
    {"true_positive", "false_positive", "false_negative", "needs_review"}
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def event_id(event: Event) -> str:
    """Return a stable ID so re-ingesting cached source data is idempotent."""

    source = event.meta.get("source")
    source_id = event.meta.get("source_event_id")
    if source and source_id:
        return f"{source}:{source_id}"
    canonical = json.dumps(
        event.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "event:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Firing:
    firing_id: str
    ts: datetime
    entity: str
    metric_id: str
    kind: EventKind
    window_start: datetime
    window_end: datetime
    observed: float
    baseline_mean: float
    baseline_stddev: float
    score: float
    detector_version: str
    hypothesis: str | None = None
    confidence: float | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", parse_timestamp(self.ts))
        object.__setattr__(self, "window_start", parse_timestamp(self.window_start))
        object.__setattr__(self, "window_end", parse_timestamp(self.window_end))
        object.__setattr__(self, "meta", dict(self.meta))
        if not self.firing_id or not self.entity or not self.metric_id:
            raise ValueError("firing_id, entity, and metric_id are required")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("firing confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Feedback:
    feedback_id: str
    firing_id: str | None
    ts: datetime
    entity: str
    metric_id: str
    outcome: FeedbackOutcome
    verdict: FeedbackVerdict | None
    hypothesis: str | None
    confidence: float | None
    corrected_cause: str | None
    reason_code: str | None
    note: str | None
    source: str
    submitted_by: str | None
    detector_version: str | None
    sentinel_run_id: str | None
    created_at: datetime
    supersedes_feedback_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", parse_timestamp(self.ts))
        object.__setattr__(self, "created_at", parse_timestamp(self.created_at))


@dataclass(frozen=True, slots=True)
class IngestionWindow:
    source: str
    entity: str
    window_start: datetime
    window_end: datetime
    status: Literal["complete", "failed"]
    normalized_events: int
    source_bytes: int | None = None
    fetched_at: datetime = field(default_factory=_utc_now)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "window_start", parse_timestamp(self.window_start))
        object.__setattr__(self, "window_end", parse_timestamp(self.window_end))
        object.__setattr__(self, "fetched_at", parse_timestamp(self.fetched_at))
        object.__setattr__(self, "meta", dict(self.meta))
        if self.window_end <= self.window_start:
            raise ValueError("ingestion window end must follow its start")


class SentinelStore:
    """Small persistence boundary; callers never update feedback in place."""

    def __init__(self, path: Path | str = Path("sentinel.duckdb")) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(self.path)
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SentinelStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id VARCHAR PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                entity VARCHAR NOT NULL,
                kind VARCHAR NOT NULL CHECK (kind IN ('issue', 'comment', 'release', 'metric')),
                text VARCHAR,
                value DOUBLE,
                meta JSON NOT NULL,
                ingested_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS firings (
                firing_id VARCHAR PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL,
                entity VARCHAR NOT NULL,
                metric_id VARCHAR NOT NULL,
                kind VARCHAR NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,
                observed DOUBLE NOT NULL,
                baseline_mean DOUBLE NOT NULL,
                baseline_stddev DOUBLE NOT NULL,
                score DOUBLE NOT NULL,
                detector_version VARCHAR NOT NULL,
                hypothesis VARCHAR,
                confidence DOUBLE,
                meta JSON NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id VARCHAR PRIMARY KEY,
                firing_id VARCHAR,
                ts TIMESTAMPTZ NOT NULL,
                entity VARCHAR NOT NULL,
                metric_id VARCHAR NOT NULL,
                outcome VARCHAR NOT NULL CHECK (
                    outcome IN ('true_positive', 'false_positive', 'false_negative', 'needs_review')
                ),
                verdict VARCHAR CHECK (verdict IN ('TRUE', 'FALSE', 'UNRESOLVED')),
                hypothesis VARCHAR,
                confidence DOUBLE,
                corrected_cause VARCHAR,
                reason_code VARCHAR,
                note VARCHAR,
                source VARCHAR NOT NULL,
                submitted_by VARCHAR,
                detector_version VARCHAR,
                sentinel_run_id VARCHAR,
                created_at TIMESTAMPTZ NOT NULL,
                supersedes_feedback_id VARCHAR
            );

            CREATE TABLE IF NOT EXISTS ingestion_windows (
                source VARCHAR NOT NULL,
                entity VARCHAR NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,
                status VARCHAR NOT NULL CHECK (status IN ('complete', 'failed')),
                normalized_events BIGINT NOT NULL,
                source_bytes BIGINT,
                fetched_at TIMESTAMPTZ NOT NULL,
                meta JSON NOT NULL,
                PRIMARY KEY (source, entity, window_start, window_end)
            );

            CREATE INDEX IF NOT EXISTS events_entity_ts_idx ON events(entity, ts);
            CREATE INDEX IF NOT EXISTS firings_entity_ts_idx ON firings(entity, ts);
            CREATE INDEX IF NOT EXISTS feedback_entity_created_idx
                ON feedback(entity, created_at);
            CREATE INDEX IF NOT EXISTS ingestion_entity_window_idx
                ON ingestion_windows(entity, window_start, window_end);
            """
        )

    def insert_events(self, events: Iterable[Event]) -> int:
        """Insert events idempotently and return the number newly persisted."""

        batch = list(events)
        if not batch:
            return 0
        before = self.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        now = _utc_now()
        self.connection.executemany(
            """
            INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (event_id) DO NOTHING
            """,
            [
                (
                    event_id(event),
                    event.ts,
                    event.entity,
                    event.kind,
                    event.text,
                    event.value,
                    json.dumps(dict(event.meta), ensure_ascii=False, sort_keys=True),
                    now,
                )
                for event in batch
            ],
        )
        after = self.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        return int(after - before)

    def list_events(
        self,
        *,
        entity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        kinds: Sequence[EventKind] | None = None,
    ) -> list[Event]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity is not None:
            clauses.append("entity = ?")
            parameters.append(entity)
        if start is not None:
            clauses.append("ts >= ?")
            parameters.append(parse_timestamp(start))
        if end is not None:
            clauses.append("ts < ?")
            parameters.append(parse_timestamp(end))
        if kinds:
            clauses.append("kind IN (" + ", ".join("?" for _ in kinds) + ")")
            parameters.extend(kinds)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            "SELECT ts, entity, kind, text, value, meta FROM events"
            + where
            + " ORDER BY ts, event_id",
            parameters,
        ).fetchall()
        return [
            Event(
                ts=row[0],
                entity=row[1],
                kind=cast(EventKind, row[2]),
                text=row[3],
                value=row[4],
                meta=_json_object(row[5]),
            )
            for row in rows
        ]

    def insert_firings(self, firings: Iterable[Firing]) -> int:
        batch = list(firings)
        if not batch:
            return 0
        before = self.connection.execute("SELECT count(*) FROM firings").fetchone()[0]
        now = _utc_now()
        self.connection.executemany(
            """
            INSERT INTO firings VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT (firing_id) DO NOTHING
            """,
            [
                (
                    firing.firing_id,
                    firing.ts,
                    firing.entity,
                    firing.metric_id,
                    firing.kind,
                    firing.window_start,
                    firing.window_end,
                    firing.observed,
                    firing.baseline_mean,
                    firing.baseline_stddev,
                    firing.score,
                    firing.detector_version,
                    firing.hypothesis,
                    firing.confidence,
                    json.dumps(dict(firing.meta), ensure_ascii=False, sort_keys=True),
                    now,
                )
                for firing in batch
            ],
        )
        after = self.connection.execute("SELECT count(*) FROM firings").fetchone()[0]
        return int(after - before)

    def get_firing(self, firing_id: str) -> Firing | None:
        row = self.connection.execute(
            """
            SELECT firing_id, ts, entity, metric_id, kind, window_start, window_end,
                   observed, baseline_mean, baseline_stddev, score, detector_version,
                   hypothesis, confidence, meta
            FROM firings WHERE firing_id = ?
            """,
            [firing_id],
        ).fetchone()
        return _row_to_firing(row) if row else None

    def list_firings(
        self,
        *,
        entity: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Firing]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity:
            clauses.append("entity = ?")
            parameters.append(entity)
        if start:
            clauses.append("ts >= ?")
            parameters.append(parse_timestamp(start))
        if end:
            clauses.append("ts < ?")
            parameters.append(parse_timestamp(end))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            """
            SELECT firing_id, ts, entity, metric_id, kind, window_start, window_end,
                   observed, baseline_mean, baseline_stddev, score, detector_version,
                   hypothesis, confidence, meta
            FROM firings
            """
            + where
            + " ORDER BY ts, firing_id",
            parameters,
        ).fetchall()
        return [_row_to_firing(row) for row in rows]

    def record_ingestion_window(self, window: IngestionWindow) -> None:
        """Record source coverage; a repeat fetch replaces only this status record."""

        self.connection.execute(
            """
            INSERT INTO ingestion_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, entity, window_start, window_end) DO UPDATE SET
                status = excluded.status,
                normalized_events = excluded.normalized_events,
                source_bytes = excluded.source_bytes,
                fetched_at = excluded.fetched_at,
                meta = excluded.meta
            """,
            [
                window.source,
                window.entity,
                window.window_start,
                window.window_end,
                window.status,
                window.normalized_events,
                window.source_bytes,
                window.fetched_at,
                json.dumps(dict(window.meta), ensure_ascii=False, sort_keys=True),
            ],
        )

    def list_ingestion_windows(
        self,
        *,
        entity: str | None = None,
        source: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[IngestionWindow]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("entity", entity), ("source", source)):
            if value:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if start:
            clauses.append("window_start >= ?")
            parameters.append(parse_timestamp(start))
        if end:
            clauses.append("window_end <= ?")
            parameters.append(parse_timestamp(end))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            """
            SELECT source, entity, window_start, window_end, status,
                   normalized_events, source_bytes, fetched_at, meta
            FROM ingestion_windows
            """
            + where
            + " ORDER BY window_start, source",
            parameters,
        ).fetchall()
        return [
            IngestionWindow(
                source=row[0],
                entity=row[1],
                window_start=row[2],
                window_end=row[3],
                status=row[4],
                normalized_events=row[5],
                source_bytes=row[6],
                fetched_at=row[7],
                meta=_json_object(row[8]),
            )
            for row in rows
        ]

    def submit_scorecard_feedback(
        self,
        firing_id: str,
        outcome: Literal["true_positive", "false_positive", "needs_review"],
        *,
        corrected_cause: str | None = None,
        reason_code: str | None = None,
        note: str | None = None,
        source: str = "human",
        submitted_by: str | None = None,
        sentinel_run_id: str | None = None,
        created_at: datetime | None = None,
        supersedes_feedback_id: str | None = None,
    ) -> Feedback:
        firing = self.get_firing(firing_id)
        if firing is None:
            raise ValueError(f"unknown firing_id: {firing_id}")
        feedback = Feedback(
            feedback_id=str(uuid4()),
            firing_id=firing.firing_id,
            ts=firing.ts,
            entity=firing.entity,
            metric_id=firing.metric_id,
            outcome=outcome,
            verdict=_verdict_for(outcome),
            hypothesis=firing.hypothesis,
            confidence=firing.confidence,
            corrected_cause=corrected_cause,
            reason_code=reason_code,
            note=note,
            source=source,
            submitted_by=submitted_by,
            detector_version=firing.detector_version,
            sentinel_run_id=sentinel_run_id,
            created_at=created_at or _utc_now(),
            supersedes_feedback_id=supersedes_feedback_id,
        )
        self._append_feedback(feedback)
        return feedback

    def report_missed_anomaly(
        self,
        *,
        entity: str,
        metric_id: str,
        ts: datetime,
        corrected_cause: str,
        reason_code: str | None = None,
        note: str | None = None,
        source: str = "human",
        submitted_by: str | None = None,
        detector_version: str | None = None,
        sentinel_run_id: str | None = None,
        created_at: datetime | None = None,
        supersedes_feedback_id: str | None = None,
    ) -> Feedback:
        if not corrected_cause.strip():
            raise ValueError("a missed anomaly requires a corrected cause")
        feedback = Feedback(
            feedback_id=str(uuid4()),
            firing_id=None,
            ts=parse_timestamp(ts),
            entity=entity,
            metric_id=metric_id,
            outcome="false_negative",
            verdict=None,
            hypothesis=None,
            confidence=None,
            corrected_cause=corrected_cause,
            reason_code=reason_code,
            note=note,
            source=source,
            submitted_by=submitted_by,
            detector_version=detector_version,
            sentinel_run_id=sentinel_run_id,
            created_at=created_at or _utc_now(),
            supersedes_feedback_id=supersedes_feedback_id,
        )
        self._append_feedback(feedback)
        return feedback

    def correct_feedback(
        self,
        feedback_id: str,
        *,
        outcome: FeedbackOutcome,
        corrected_cause: str | None = None,
        reason_code: str | None = None,
        note: str | None = None,
        source: str = "human",
        submitted_by: str | None = None,
        created_at: datetime | None = None,
    ) -> Feedback:
        original = self.get_feedback(feedback_id)
        if original is None:
            raise ValueError(f"unknown feedback_id: {feedback_id}")
        if self._is_superseded(feedback_id):
            raise ValueError("only the active feedback row can be corrected")
        if outcome == "false_negative":
            return self.report_missed_anomaly(
                entity=original.entity,
                metric_id=original.metric_id,
                ts=original.ts,
                corrected_cause=corrected_cause or original.corrected_cause or "missed anomaly",
                reason_code=reason_code,
                note=note,
                source=source,
                submitted_by=submitted_by,
                detector_version=original.detector_version,
                sentinel_run_id=original.sentinel_run_id,
                created_at=created_at,
                supersedes_feedback_id=feedback_id,
            )
        if original.firing_id is None:
            raise ValueError("a missed-anomaly report can only be corrected as false_negative")
        return self.submit_scorecard_feedback(
            original.firing_id,
            cast(Literal["true_positive", "false_positive", "needs_review"], outcome),
            corrected_cause=corrected_cause,
            reason_code=reason_code,
            note=note,
            source=source,
            submitted_by=submitted_by,
            sentinel_run_id=original.sentinel_run_id,
            created_at=created_at,
            supersedes_feedback_id=feedback_id,
        )

    def _append_feedback(self, feedback: Feedback) -> None:
        if feedback.outcome not in FEEDBACK_OUTCOMES:
            raise ValueError(f"unsupported feedback outcome: {feedback.outcome}")
        if feedback.supersedes_feedback_id:
            original = self.get_feedback(feedback.supersedes_feedback_id)
            if original is None:
                raise ValueError(
                    f"unknown supersedes_feedback_id: {feedback.supersedes_feedback_id}"
                )
            if original.entity != feedback.entity or original.metric_id != feedback.metric_id:
                raise ValueError("a correction must preserve entity and metric scope")
        self.connection.execute(
            """
            INSERT INTO feedback VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                feedback.feedback_id,
                feedback.firing_id,
                feedback.ts,
                feedback.entity,
                feedback.metric_id,
                feedback.outcome,
                feedback.verdict,
                feedback.hypothesis,
                feedback.confidence,
                feedback.corrected_cause,
                feedback.reason_code,
                feedback.note,
                feedback.source,
                feedback.submitted_by,
                feedback.detector_version,
                feedback.sentinel_run_id,
                feedback.created_at,
                feedback.supersedes_feedback_id,
            ],
        )

    def get_feedback(self, feedback_id: str) -> Feedback | None:
        row = self.connection.execute(
            _FEEDBACK_SELECT + " WHERE feedback_id = ?", [feedback_id]
        ).fetchone()
        return _row_to_feedback(row) if row else None

    def list_feedback(
        self,
        *,
        entity: str | None = None,
        metric_id: str | None = None,
        active_only: bool = False,
        limit: int | None = None,
    ) -> list[Feedback]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity:
            clauses.append("f.entity = ?")
            parameters.append(entity)
        if metric_id:
            clauses.append("f.metric_id = ?")
            parameters.append(metric_id)
        if active_only:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM feedback newer "
                "WHERE newer.supersedes_feedback_id = f.feedback_id)"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            if limit < 1:
                raise ValueError("feedback limit must be positive")
            parameters.append(limit)
        rows = self.connection.execute(
            _FEEDBACK_SELECT.replace("FROM feedback", "FROM feedback f")
            + where
            + " ORDER BY f.created_at DESC, f.feedback_id DESC"
            + limit_sql,
            parameters,
        ).fetchall()
        return [_row_to_feedback(row) for row in rows]

    def relevant_feedback(
        self, entity: str, *, metric_id: str | None = None, limit: int = 10
    ) -> list[Feedback]:
        """Return the bounded, active context a later triage call should read."""

        return self.list_feedback(
            entity=entity, metric_id=metric_id, active_only=True, limit=limit
        )

    def _is_superseded(self, feedback_id: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT count(*) FROM feedback WHERE supersedes_feedback_id = ?",
                [feedback_id],
            ).fetchone()[0]
        )


_FEEDBACK_SELECT = """
SELECT feedback_id, firing_id, ts, entity, metric_id, outcome, verdict,
       hypothesis, confidence, corrected_cause, reason_code, note, source,
       submitted_by, detector_version, sentinel_run_id, created_at,
       supersedes_feedback_id
FROM feedback
"""


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise ValueError("stored meta must be a JSON object")
    return decoded


def _row_to_firing(row: Sequence[Any]) -> Firing:
    return Firing(
        firing_id=row[0],
        ts=row[1],
        entity=row[2],
        metric_id=row[3],
        kind=cast(EventKind, row[4]),
        window_start=row[5],
        window_end=row[6],
        observed=row[7],
        baseline_mean=row[8],
        baseline_stddev=row[9],
        score=row[10],
        detector_version=row[11],
        hypothesis=row[12],
        confidence=row[13],
        meta=_json_object(row[14]),
    )


def _row_to_feedback(row: Sequence[Any]) -> Feedback:
    return Feedback(
        feedback_id=row[0],
        firing_id=row[1],
        ts=row[2],
        entity=row[3],
        metric_id=row[4],
        outcome=cast(FeedbackOutcome, row[5]),
        verdict=cast(FeedbackVerdict | None, row[6]),
        hypothesis=row[7],
        confidence=row[8],
        corrected_cause=row[9],
        reason_code=row[10],
        note=row[11],
        source=row[12],
        submitted_by=row[13],
        detector_version=row[14],
        sentinel_run_id=row[15],
        created_at=row[16],
        supersedes_feedback_id=row[17],
    )


def _verdict_for(
    outcome: Literal["true_positive", "false_positive", "needs_review"],
) -> FeedbackVerdict:
    return {
        "true_positive": "TRUE",
        "false_positive": "FALSE",
        "needs_review": "UNRESOLVED",
    }[outcome]
