"""Detector-v3 labeled-corpus and coherence-feature construction.

V3 deliberately separates the ground-truth enumerator from the model.  The
enumerator considers every permissive activity-spike hour in the frozen study
protocol; it never consumes v1/v2 firings or scores.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence, cast

from sentinel.detect import DetectorConfig
from sentinel.detector_v2 import _actor, _automated_actors, _is_pull_request
from sentinel.holdout import (
    AUTO_LABEL_RULE_VERSION,
    BREAKAGE_REPORT,
    MAINTAINER_ASSOCIATIONS,
    classify_acknowledgment,
)
from sentinel.types import Event, format_timestamp, parse_timestamp

DATASET_SCHEMA_VERSION = 1
MAD_NORMAL_SCALE = 1.4826

BURST_FEATURES = (
    "raw_activity_count",
    "raw_issue_count",
    "raw_comment_count",
    "raw_unique_participants",
    "raw_active_threads",
    "raw_activity_phase_z",
)

COHERENCE_FEATURES = (
    "bot_excluded_activity_count",
    "bot_excluded_fraction",
    "bot_excluded_issue_rate",
    "bot_excluded_comment_rate",
    "bot_excluded_activity_phase_z",
    "bot_excluded_issue_rate_phase_z",
    "bot_excluded_comment_rate_phase_z",
    "first_time_participant_share",
    "first_time_participant_phase_z",
    "cross_thread_tfidf_max",
    "cross_thread_tfidf_mean",
    "cross_thread_shingle_max",
    "breakage_thread_share",
    "npm_release_nearby",
    "github_release_nearby",
    "release_source_count",
    "minutes_since_nearest_release",
)

MODEL_FEATURES = BURST_FEATURES + COHERENCE_FEATURES

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.:/-]{1,}")
_SPACE = re.compile(r"\s+")
_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "been",
        "before",
        "being",
        "build",
        "could",
        "does",
        "from",
        "have",
        "here",
        "https",
        "into",
        "issue",
        "just",
        "more",
        "same",
        "should",
        "that",
        "their",
        "there",
        "these",
        "they",
        "this",
        "using",
        "version",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
)


def canonical_sha256(value: Any) -> str:
    """Hash a JSON-compatible value with the repository's canonical encoding."""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_labeled_dataset(
    events: Sequence[Event], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """Enumerate all frozen spike hours, label incidents, and compute features."""

    label_rule = cast(Mapping[str, Any], protocol["label_rule"])
    if label_rule.get("version") != AUTO_LABEL_RULE_VERSION:
        raise ValueError(
            "study protocol label rule does not match the committed classifier"
        )
    minimum_activity = int(label_rule["minimum_raw_activity"])
    lookahead = timedelta(hours=int(label_rule["acknowledgment_lookahead_hours"]))
    history_days = int(
        cast(Mapping[str, Any], protocol["feature_policy"])["history_days"]
    )
    release_hours = int(
        cast(Mapping[str, Any], protocol["feature_policy"])[
            "release_window_hours"
        ]
    )
    repositories = cast(Mapping[str, Sequence[str]], protocol["repositories"])
    split_by_repo = {
        repo: split for split, repos in repositories.items() for repo in repos
    }
    if len(split_by_repo) != sum(len(items) for items in repositories.values()):
        raise ValueError("repository train/test split contains duplicates")

    ordered = tuple(sorted(events, key=lambda event: (event.entity, event.ts)))
    rows: list[dict[str, Any]] = []
    all_incidents: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    for raw_window in cast(Sequence[Mapping[str, Any]], protocol["windows"]):
        window_id = str(raw_window["window_id"])
        archive_start = parse_timestamp(str(raw_window["archive_start"]))
        detection_start = parse_timestamp(str(raw_window["detection_start"]))
        detection_end = parse_timestamp(str(raw_window["detection_end"]))
        acknowledgment_end = parse_timestamp(
            str(raw_window["acknowledgment_end"])
        )
        if not archive_start < detection_start < detection_end < acknowledgment_end:
            raise ValueError(f"invalid v3 window ordering for {window_id}")
        window_events = tuple(
            event
            for event in ordered
            if event.entity in split_by_repo
            and archive_start <= event.ts < acknowledgment_end
        )
        window_rows, window_incidents, window_coverage = _build_window(
            window_id=window_id,
            events=window_events,
            repositories=tuple(sorted(split_by_repo)),
            split_by_repo=split_by_repo,
            archive_start=archive_start,
            detection_start=detection_start,
            detection_end=detection_end,
            acknowledgment_end=acknowledgment_end,
            history_days=history_days,
            release_hours=release_hours,
            minimum_activity=minimum_activity,
            lookahead=lookahead,
        )
        rows.extend(window_rows)
        all_incidents.extend(window_incidents)
        coverage.append(window_coverage)

    rows.sort(key=lambda row: (row["candidate_ts"], row["entity"]))
    all_incidents.sort(
        key=lambda row: (row["candidate_ts"], row["entity"], row["issue_number"])
    )
    class_balance = _class_balance(rows, all_incidents)
    row_payload = [
        {
            key: value
            for key, value in row.items()
            if key not in {"source_events"}
        }
        for row in rows
    ]
    positive_payload = list(all_incidents)
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "label_rule_version": AUTO_LABEL_RULE_VERSION,
        "feature_names": {
            "burst": list(BURST_FEATURES),
            "coherence": list(COHERENCE_FEATURES),
            "model": list(MODEL_FEATURES),
        },
        "coverage": coverage,
        "class_balance": class_balance,
        "positive_incidents": positive_payload,
        "rows": row_payload,
        "hashes": {
            "protocol_sha256": canonical_sha256(protocol),
            "positive_incidents_sha256": canonical_sha256(positive_payload),
            "rows_sha256": canonical_sha256(row_payload),
        },
    }


def _build_window(
    *,
    window_id: str,
    events: Sequence[Event],
    repositories: Sequence[str],
    split_by_repo: Mapping[str, str],
    archive_start: datetime,
    detection_start: datetime,
    detection_end: datetime,
    acknowledgment_end: datetime,
    history_days: int,
    release_hours: int,
    minimum_activity: int,
    lookahead: timedelta,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    by_repo: dict[str, list[Event]] = {repo: [] for repo in repositories}
    for event in events:
        by_repo[event.entity].append(event)

    rows: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    expected_hours = int((acknowledgment_end - archive_start).total_seconds() / 3600)
    source_hours = {
        str(event.meta.get("archive_hour"))
        for event in events
        if event.meta.get("source") == "gharchive" and event.meta.get("archive_hour")
    }

    for repo in repositories:
        repo_rows, repo_incidents = _build_repo_window(
            window_id=window_id,
            repo=repo,
            split=split_by_repo[repo],
            events=tuple(sorted(by_repo[repo], key=lambda event: event.ts)),
            archive_start=archive_start,
            detection_start=detection_start,
            detection_end=detection_end,
            acknowledgment_end=acknowledgment_end,
            history_days=history_days,
            release_hours=release_hours,
            minimum_activity=minimum_activity,
            lookahead=lookahead,
        )
        rows.extend(repo_rows)
        incidents.extend(repo_incidents)

    return rows, incidents, {
        "window_id": window_id,
        "archive_start": format_timestamp(archive_start),
        "detection_start": format_timestamp(detection_start),
        "detection_end": format_timestamp(detection_end),
        "acknowledgment_end": format_timestamp(acknowledgment_end),
        "repositories": len(repositories),
        "archive_hours_expected": expected_hours,
        "archive_hours_observed": len(source_hours),
        "candidate_hours": len(rows),
        "positive_incidents": len(incidents),
    }


def _build_repo_window(
    *,
    window_id: str,
    repo: str,
    split: str,
    events: Sequence[Event],
    archive_start: datetime,
    detection_start: datetime,
    detection_end: datetime,
    acknowledgment_end: datetime,
    history_days: int,
    release_hours: int,
    minimum_activity: int,
    lookahead: timedelta,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    activity_by_hour: dict[datetime, list[Event]] = defaultdict(list)
    issue_events: dict[int, list[Event]] = defaultdict(list)
    releases: list[Event] = []
    first_seen: dict[str, datetime] = {}
    for event in events:
        if event.kind == "release":
            releases.append(event)
        if _is_activity(event):
            bucket = _floor_hour(event.ts)
            activity_by_hour[bucket].append(event)
            if (actor := _actor(event)) is not None:
                first_seen.setdefault(actor, event.ts)
        number = _issue_number(event)
        if number is not None and not _is_pull_request(event):
            issue_events[number].append(event)

    config = DetectorConfig.load()
    hourly: dict[datetime, dict[str, float]] = {}
    hour_events: dict[datetime, tuple[Event, ...]] = {}
    current = archive_start
    while current < detection_end:
        bucket_events = tuple(
            sorted(activity_by_hour.get(current, ()), key=lambda event: event.ts)
        )
        hour_events[current] = bucket_events
        hourly[current] = _base_features(
            bucket_events,
            first_seen=first_seen,
            config=config,
        )
        current += timedelta(hours=1)

    candidates: list[dict[str, Any]] = []
    for bucket in _iter_hours(detection_start, detection_end):
        bucket_events = hour_events[bucket]
        if len(bucket_events) < minimum_activity:
            continue
        snapshot = max(event.ts for event in bucket_events) + timedelta(microseconds=1)
        features = dict(hourly[bucket])
        features.update(
            _phase_features(
                bucket=bucket,
                hourly=hourly,
                history_days=history_days,
            )
        )
        features.update(
            _release_features(
                releases=releases,
                snapshot=snapshot,
                release_hours=release_hours,
            )
        )
        candidates.append(
            {
                "row_id": _row_id(window_id, repo, bucket),
                "window_id": window_id,
                "split": split,
                "entity": repo,
                "bucket_start": format_timestamp(bucket),
                "bucket_end": format_timestamp(bucket + timedelta(hours=1)),
                "candidate_ts": format_timestamp(snapshot),
                "label": 0,
                "incident_ids": [],
                "features": {name: float(features.get(name, 0.0)) for name in MODEL_FEATURES},
                "source_events": bucket_events,
            }
        )

    assigned_issues: set[int] = set()
    incidents: list[dict[str, Any]] = []
    for row in candidates:
        snapshot = parse_timestamp(str(row["candidate_ts"]))
        row_events = cast(Sequence[Event], row["source_events"])
        numbers = sorted(
            number
            for event in row_events
            if event.ts < snapshot
            if (number := _issue_number(event)) is not None
        )
        for number in numbers:
            if number in assigned_issues:
                continue
            visible = [
                event for event in issue_events[number] if event.ts < snapshot
            ]
            report = next(
                (
                    event
                    for event in sorted(visible, key=lambda event: event.ts)
                    if event.kind == "issue"
                    and event.meta.get("action") == "opened"
                    and BREAKAGE_REPORT.search(event.text or "")
                ),
                None,
            )
            if report is None:
                continue
            ack_limit = min(snapshot + lookahead, acknowledgment_end)
            acknowledgments = [
                event
                for event in issue_events[number]
                if event.kind == "comment"
                and snapshot < event.ts <= ack_limit
                and str(event.meta.get("author_association") or "").upper()
                in MAINTAINER_ASSOCIATIONS
                and not _is_ack_bot(event)
                and classify_acknowledgment(event.text) == "TRUE"
            ]
            if not acknowledgments:
                continue
            ack = min(acknowledgments, key=lambda event: event.ts)
            incident_id = _incident_id(window_id, repo, number, ack.ts)
            lead_minutes = (ack.ts - snapshot).total_seconds() / 60
            incident = {
                "incident_id": incident_id,
                "row_id": row["row_id"],
                "window_id": window_id,
                "split": split,
                "entity": repo,
                "issue_number": number,
                "candidate_ts": row["candidate_ts"],
                "report_ts": format_timestamp(report.ts),
                "report_url": report.meta.get("url"),
                "report_excerpt": _excerpt(report.text),
                "ack_ts": format_timestamp(ack.ts),
                "ack_url": ack.meta.get("url"),
                "ack_excerpt": _excerpt(ack.text),
                "lead_minutes": round(lead_minutes, 3),
            }
            cast(list[str], row["incident_ids"]).append(incident_id)
            row["label"] = 1
            incidents.append(incident)
            assigned_issues.add(number)

    for row in candidates:
        row.pop("source_events", None)
    return candidates, incidents


def _base_features(
    events: Sequence[Event],
    *,
    first_seen: Mapping[str, datetime],
    config: DetectorConfig,
) -> dict[str, float]:
    automated = _automated_actors(events, config)
    excluded = tuple(
        event for event in events if (_actor(event) or "") not in automated
    )
    raw_issues = [event for event in events if event.kind == "issue"]
    raw_comments = [event for event in events if event.kind == "comment"]
    excluded_issues = [event for event in excluded if event.kind == "issue"]
    excluded_comments = [event for event in excluded if event.kind == "comment"]
    raw_participants = _actors(events)
    excluded_participants = _actors(excluded)
    raw_threads = _threads(events)
    excluded_threads = _threads(excluded)
    first_time_events = [
        event
        for event in excluded
        if (actor := _actor(event)) is not None
        and first_seen.get(actor) is not None
        and _floor_hour(cast(datetime, first_seen[actor]))
        == _floor_hour(event.ts)
    ]
    text_features = _text_features(excluded)
    raw_count = len(events)
    excluded_count = len(excluded)
    return {
        "raw_activity_count": float(raw_count),
        "raw_issue_count": float(len(raw_issues)),
        "raw_comment_count": float(len(raw_comments)),
        "raw_unique_participants": float(len(raw_participants)),
        "raw_active_threads": float(len(raw_threads)),
        "bot_excluded_activity_count": float(excluded_count),
        "bot_excluded_fraction": (
            excluded_count / raw_count if raw_count else 0.0
        ),
        "bot_excluded_issue_rate": (
            len(excluded_issues) / len(excluded_participants)
            if excluded_participants
            else 0.0
        ),
        "bot_excluded_comment_rate": (
            len(excluded_comments) / len(excluded_threads)
            if excluded_threads
            else 0.0
        ),
        "first_time_participant_share": (
            len(first_time_events) / excluded_count if excluded_count else 0.0
        ),
        **text_features,
    }


def _phase_features(
    *, bucket: datetime, hourly: Mapping[datetime, Mapping[str, float]], history_days: int
) -> dict[str, float]:
    sources = {
        "raw_activity_count": "raw_activity_phase_z",
        "bot_excluded_activity_count": "bot_excluded_activity_phase_z",
        "bot_excluded_issue_rate": "bot_excluded_issue_rate_phase_z",
        "bot_excluded_comment_rate": "bot_excluded_comment_rate_phase_z",
        "first_time_participant_share": "first_time_participant_phase_z",
    }
    oldest = bucket - timedelta(days=history_days)
    pool_hours = [
        hour
        for hour in hourly
        if oldest <= hour < bucket
        and hour.hour == bucket.hour
        and _day_class(hour) == _day_class(bucket)
    ]
    result: dict[str, float] = {}
    for source, destination in sources.items():
        values = [float(hourly[hour][source]) for hour in sorted(pool_hours)]
        current = float(hourly[bucket][source])
        result[destination] = _robust_z(current, values)
    return result


def _release_features(
    *, releases: Sequence[Event], snapshot: datetime, release_hours: int
) -> dict[str, float]:
    oldest = snapshot - timedelta(hours=release_hours)
    nearby = [event for event in releases if oldest <= event.ts < snapshot]
    sources = {str(event.meta.get("source") or "unknown") for event in nearby}
    distances = [(snapshot - event.ts).total_seconds() / 60 for event in nearby]
    return {
        "npm_release_nearby": float("npm_registry" in sources),
        "github_release_nearby": float("gharchive" in sources),
        "release_source_count": float(len(sources)),
        "minutes_since_nearest_release": (
            min(distances) if distances else float(release_hours * 60)
        ),
    }


def _text_features(events: Sequence[Event]) -> dict[str, float]:
    by_thread: dict[int, list[str]] = defaultdict(list)
    for event in events:
        number = _issue_number(event)
        if number is not None and event.text:
            by_thread[number].append(event.text)
    documents = ["\n".join(parts) for _, parts in sorted(by_thread.items())]
    token_docs = [_terms(document) for document in documents]
    vectors = _tfidf_vectors(token_docs)
    cosine = [_cosine(left, right) for left, right in combinations(vectors, 2)]
    shingle_sets = [_character_shingles(document) for document in documents]
    shingles = [
        _jaccard(left, right) for left, right in combinations(shingle_sets, 2)
    ]
    breakage_threads = sum(
        any(BREAKAGE_REPORT.search(part) for part in parts)
        for parts in by_thread.values()
    )
    return {
        "cross_thread_tfidf_max": max(cosine, default=0.0),
        "cross_thread_tfidf_mean": (
            statistics.fmean(cosine) if cosine else 0.0
        ),
        "cross_thread_shingle_max": max(shingles, default=0.0),
        "breakage_thread_share": (
            breakage_threads / len(by_thread) if by_thread else 0.0
        ),
    }


def _terms(text: str) -> Counter[str]:
    tokens = [
        token
        for token in _TOKEN.findall(text[:8_000].casefold())
        if token not in _STOP_WORDS and not token.startswith("http")
    ]
    terms = Counter(tokens)
    terms.update(f"{left}::{right}" for left, right in zip(tokens, tokens[1:]))
    return terms


def _tfidf_vectors(documents: Sequence[Counter[str]]) -> list[dict[str, float]]:
    document_frequency = Counter(
        term for document in documents for term in document.keys()
    )
    count = len(documents)
    vectors: list[dict[str, float]] = []
    for document in documents:
        vectors.append(
            {
                term: frequency * (math.log((1 + count) / (1 + document_frequency[term])) + 1)
                for term, frequency in document.items()
            }
        )
    return vectors


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(value * right.get(term, 0.0) for term, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _character_shingles(text: str, size: int = 5) -> set[str]:
    # Public issue bodies can contain multi-megabyte logs.  A deterministic
    # prefix cap keeps the cheap feature cheap and is recorded in code/hash.
    normalized = _SPACE.sub(" ", text[:4_000].casefold()).strip()
    return {
        normalized[index : index + size]
        for index in range(max(0, len(normalized) - size + 1))
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _robust_z(value: float, baseline: Sequence[float]) -> float:
    if len(baseline) < 2:
        return 0.0
    center = statistics.median(baseline)
    mad = statistics.median(abs(item - center) for item in baseline)
    dispersion = max(1.0, abs(center) * 0.1, MAD_NORMAL_SCALE * mad)
    return (value - center) / dispersion


def _class_balance(
    rows: Sequence[Mapping[str, Any]], incidents: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for split in ("all", "train", "test"):
        selected = [row for row in rows if split == "all" or row["split"] == split]
        positives = sum(int(row["label"]) for row in selected)
        split_incidents = sum(
            split == "all" or incident["split"] == split for incident in incidents
        )
        result[split] = {
            "candidate_hours": len(selected),
            "positive_hours": positives,
            "negative_hours": len(selected) - positives,
            "positive_incidents": split_incidents,
            "positive_hour_rate": positives / len(selected) if selected else 0.0,
        }
    by_repo: dict[str, dict[str, int]] = {}
    for repo in sorted({str(row["entity"]) for row in rows}):
        selected = [row for row in rows if row["entity"] == repo]
        by_repo[repo] = {
            "candidate_hours": len(selected),
            "positive_hours": sum(int(row["label"]) for row in selected),
            "positive_incidents": sum(
                incident["entity"] == repo for incident in incidents
            ),
        }
    result["by_repository"] = by_repo
    return result


def _is_activity(event: Event) -> bool:
    return (
        not _is_pull_request(event)
        and (
            (event.kind == "issue" and event.meta.get("action") == "opened")
            or (event.kind == "comment" and event.meta.get("action") == "created")
        )
    )


def _is_ack_bot(event: Event) -> bool:
    identity = str(event.meta.get("author") or event.meta.get("actor") or "").lower()
    return (
        identity.endswith("[bot]")
        or identity.endswith("-bot")
        or str(event.meta.get("author_type") or event.meta.get("actor_type") or "")
        .casefold()
        == "bot"
    )


def _actors(events: Iterable[Event]) -> set[str]:
    return {actor for event in events if (actor := _actor(event)) is not None}


def _threads(events: Iterable[Event]) -> set[int]:
    return {
        number for event in events if (number := _issue_number(event)) is not None
    }


def _issue_number(event: Event) -> int | None:
    value = event.meta.get("issue_number")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _floor_hour(value: datetime) -> datetime:
    return parse_timestamp(value).replace(minute=0, second=0, microsecond=0)


def _iter_hours(start: datetime, end: datetime) -> Iterable[datetime]:
    current = parse_timestamp(start)
    while current < end:
        yield current
        current += timedelta(hours=1)


def _day_class(value: datetime) -> str:
    return "weekend" if value.weekday() >= 5 else "weekday"


def _row_id(window_id: str, repo: str, bucket: datetime) -> str:
    identity = f"{window_id}|{repo}|{format_timestamp(bucket)}"
    return "v3row:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _incident_id(
    window_id: str, repo: str, issue_number: int, ack_ts: datetime
) -> str:
    identity = f"{window_id}|{repo}|{issue_number}|{format_timestamp(ack_ts)}"
    return "v3incident:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _excerpt(value: str | None, limit: int = 320) -> str | None:
    normalized = " ".join((value or "").split())
    if not normalized:
        return None
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
