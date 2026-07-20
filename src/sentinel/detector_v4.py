"""Powered v4 population, labels, and source-attributed incidents.

V4 deliberately reuses the v3 feature families.  Its intervention is the
study design: every active repository-hour is eligible, confirmations come
from multiple deterministic maintainer actions, and train/test source bundles
are built separately so the whole-repository test split stays unopened until
the model and threshold have been frozen.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence, cast

from sentinel.detect import DetectorConfig
from sentinel.detector_v3 import (
    BURST_FEATURES,
    COHERENCE_FEATURES,
    MODEL_FEATURES,
    _base_features,
    _floor_hour,
    _is_ack_bot,
    _is_activity,
    _issue_number,
    _iter_hours,
    _phase_features,
    _release_features,
    _rolling_features,
    canonical_sha256,
)
from sentinel.holdout import (
    BREAKAGE_REPORT,
    MAINTAINER_ASSOCIATIONS,
    classify_acknowledgment,
)
from sentinel.types import Event, format_timestamp, parse_timestamp

DATASET_SCHEMA_VERSION = 1
LABEL_RULE_VERSION = "maintainer-confirmed-multisource-v4"
SOURCE_ORDER = {
    "completed_bug_closure": 0,
    "merged_fixing_pr": 1,
    "substantive_maintainer_comment": 2,
}


def build_labeled_dataset(
    events: Sequence[Event], protocol: Mapping[str, Any], *, split: str
) -> dict[str, Any]:
    """Build one split without reading or depending on the other split."""

    if split not in {"train", "test"}:
        raise ValueError("v4 split must be train or test")
    label_rule = cast(Mapping[str, Any], protocol["label_rule"])
    if label_rule.get("version") != LABEL_RULE_VERSION:
        raise ValueError("study protocol label rule does not match detector v4")
    minimum_activity = int(label_rule["minimum_raw_activity"])
    lookahead = timedelta(hours=int(label_rule["confirmation_lookahead_hours"]))
    feature_policy = cast(Mapping[str, Any], protocol["feature_policy"])
    history_days = int(feature_policy["history_days"])
    release_hours = int(feature_policy["release_window_hours"])
    repositories = tuple(
        sorted(
            cast(Mapping[str, Sequence[str]], protocol["repositories"])[split]
        )
    )
    allowed = set(repositories)
    ordered = tuple(
        sorted(
            (event for event in events if event.entity in allowed),
            key=lambda event: (event.entity, event.ts),
        )
    )
    rows: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for raw_window in cast(Sequence[Mapping[str, Any]], protocol["windows"]):
        window_id = str(raw_window["window_id"])
        archive_start = parse_timestamp(str(raw_window["archive_start"]))
        detection_start = parse_timestamp(str(raw_window["detection_start"]))
        detection_end = parse_timestamp(str(raw_window["detection_end"]))
        confirmation_end = parse_timestamp(str(raw_window["confirmation_end"]))
        if not archive_start < detection_start < detection_end < confirmation_end:
            raise ValueError(f"invalid v4 window ordering for {window_id}")
        window_events = tuple(
            event
            for event in ordered
            if archive_start <= event.ts < confirmation_end
        )
        window_rows, window_incidents = _build_window(
            window_id=window_id,
            events=window_events,
            repositories=repositories,
            split=split,
            archive_start=archive_start,
            detection_start=detection_start,
            detection_end=detection_end,
            confirmation_end=confirmation_end,
            history_days=history_days,
            release_hours=release_hours,
            minimum_activity=minimum_activity,
            lookahead=lookahead,
            label_rule=label_rule,
        )
        rows.extend(window_rows)
        incidents.extend(window_incidents)
        coverage.append(
            {
                "window_id": window_id,
                "archive_start": format_timestamp(archive_start),
                "detection_start": format_timestamp(detection_start),
                "detection_end": format_timestamp(detection_end),
                "confirmation_end": format_timestamp(confirmation_end),
                "repositories": len(repositories),
                "archive_hours_expected": int(
                    (confirmation_end - archive_start).total_seconds() / 3600
                ),
                "detection_hours": int(
                    (detection_end - detection_start).total_seconds() / 3600
                ),
                "candidate_hours": len(window_rows),
                "positive_incidents": len(window_incidents),
            }
        )

    rows.sort(key=lambda row: (row["candidate_ts"], row["entity"]))
    incidents.sort(
        key=lambda row: (
            row["candidate_ts"],
            row["entity"],
            row["issue_number"],
        )
    )
    class_balance = _class_balance(rows, incidents)
    source_attribution = _source_attribution(incidents)
    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "split": split,
        "label_rule_version": LABEL_RULE_VERSION,
        "feature_names": {
            "burst": list(BURST_FEATURES),
            "coherence": list(COHERENCE_FEATURES),
            "model": list(MODEL_FEATURES),
        },
        "coverage": coverage,
        "class_balance": class_balance,
        "source_attribution": source_attribution,
        "positive_incidents": incidents,
        "rows": rows,
        "hashes": {
            "protocol_sha256": canonical_sha256(protocol),
            "positive_incidents_sha256": canonical_sha256(incidents),
            "rows_sha256": canonical_sha256(rows),
        },
    }


def build_audit_candidates(
    dataset: Mapping[str, Any], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    """Select a deterministic, source-stratified sample for human review."""

    split = str(dataset["split"])
    sample_size = int(
        cast(Mapping[str, Any], protocol["power_targets"])[
            "audit_sample_per_source_per_split"
        ]
    )
    incidents = cast(Sequence[Mapping[str, Any]], dataset["positive_incidents"])
    samples: list[dict[str, Any]] = []
    for source in SOURCE_ORDER:
        source_rows = [row for row in incidents if row["label_source"] == source]
        source_rows.sort(
            key=lambda row: hashlib.sha256(
                f"v4-audit|{split}|{row['incident_id']}".encode()
            ).hexdigest()
        )
        for row in source_rows[:sample_size]:
            samples.append(
                {
                    "split": split,
                    "label_source": source,
                    "incident_id": row["incident_id"],
                    "entity": row["entity"],
                    "issue_number": row["issue_number"],
                    "report_url": row["report_url"],
                    "report_excerpt": row["report_excerpt"],
                    "confirmation_url": row["confirmation_url"],
                    "confirmation_excerpt": row["confirmation_excerpt"],
                    "status": "unreviewed",
                    "reason": "",
                }
            )
    return {
        "schema_version": 1,
        "study_id": dataset["study_id"],
        "split": split,
        "sampling": (
            "SHA-256 ordering within label source; first frozen N per source"
        ),
        "requested_per_source": sample_size,
        "samples": samples,
        "sample_sha256": canonical_sha256(samples),
    }


def _build_window(
    *,
    window_id: str,
    events: Sequence[Event],
    repositories: Sequence[str],
    split: str,
    archive_start: datetime,
    detection_start: datetime,
    detection_end: datetime,
    confirmation_end: datetime,
    history_days: int,
    release_hours: int,
    minimum_activity: int,
    lookahead: timedelta,
    label_rule: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_repo: dict[str, list[Event]] = {repo: [] for repo in repositories}
    for event in events:
        by_repo[event.entity].append(event)
    rows: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    for repo in repositories:
        repo_rows, repo_incidents = _build_repo_window(
            window_id=window_id,
            repo=repo,
            split=split,
            events=tuple(sorted(by_repo[repo], key=lambda event: event.ts)),
            archive_start=archive_start,
            detection_start=detection_start,
            detection_end=detection_end,
            confirmation_end=confirmation_end,
            history_days=history_days,
            release_hours=release_hours,
            minimum_activity=minimum_activity,
            lookahead=lookahead,
            label_rule=label_rule,
        )
        rows.extend(repo_rows)
        incidents.extend(repo_incidents)
    return rows, incidents


def _build_repo_window(
    *,
    window_id: str,
    repo: str,
    split: str,
    events: Sequence[Event],
    archive_start: datetime,
    detection_start: datetime,
    detection_end: datetime,
    confirmation_end: datetime,
    history_days: int,
    release_hours: int,
    minimum_activity: int,
    lookahead: timedelta,
    label_rule: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    activity_by_hour: dict[datetime, list[Event]] = defaultdict(list)
    issue_events: dict[int, list[Event]] = defaultdict(list)
    pull_requests: list[Event] = []
    releases: list[Event] = []
    first_seen: dict[str, datetime] = {}
    for event in events:
        if event.kind == "release":
            releases.append(event)
        elif event.kind == "pull_request":
            pull_requests.append(event)
        if _is_activity(event):
            activity_by_hour[_floor_hour(event.ts)].append(event)
            actor = _activity_actor(event)
            if actor is not None:
                first_seen.setdefault(actor, event.ts)
        if (number := _issue_number(event)) is not None:
            issue_events[number].append(event)

    config = DetectorConfig.load()
    hourly: dict[datetime, dict[str, float]] = {}
    hour_events: dict[datetime, tuple[Event, ...]] = {}
    for bucket in _iter_hours(archive_start, detection_end):
        bucket_events = tuple(
            sorted(activity_by_hour.get(bucket, ()), key=lambda event: event.ts)
        )
        hour_events[bucket] = bucket_events
        hourly[bucket] = _base_features(
            bucket_events, first_seen=first_seen, config=config
        )

    candidates: list[dict[str, Any]] = []
    source_events_by_row: dict[str, tuple[Event, ...]] = {}
    for bucket in _iter_hours(detection_start, detection_end):
        bucket_events = hour_events[bucket]
        if len(bucket_events) < minimum_activity:
            continue
        snapshot = bucket + timedelta(hours=1)
        features = dict(hourly[bucket])
        features.update(
            _phase_features(
                bucket=bucket, hourly=hourly, history_days=history_days
            )
        )
        features.update(
            _rolling_features(events=events, snapshot=snapshot, config=config)
        )
        features.update(
            _release_features(
                releases=releases,
                snapshot=snapshot,
                release_hours=release_hours,
                activity_events=bucket_events,
            )
        )
        row_id = _row_id(window_id, repo, bucket)
        candidates.append(
            {
                "row_id": row_id,
                "window_id": window_id,
                "split": split,
                "entity": repo,
                "bucket_start": format_timestamp(bucket),
                "bucket_end": format_timestamp(snapshot),
                "candidate_ts": format_timestamp(snapshot),
                "candidate_url": next(
                    (
                        event.meta.get("url")
                        for event in reversed(bucket_events)
                        if event.meta.get("url")
                    ),
                    None,
                ),
                "old_spike_eligible": len(bucket_events) >= 5,
                "label": 0,
                "incident_ids": [],
                "features": {
                    name: float(features.get(name, 0.0)) for name in MODEL_FEATURES
                },
            }
        )
        source_events_by_row[row_id] = bucket_events

    incidents: list[dict[str, Any]] = []
    assigned_issues: set[int] = set()
    bug_labels = re.compile(str(label_rule["bug_label_pattern"]), re.IGNORECASE)
    fixing_reference = re.compile(
        str(label_rule["fixing_reference_pattern"]), re.IGNORECASE
    )
    for row in candidates:
        snapshot = parse_timestamp(str(row["candidate_ts"]))
        reports = [
            event
            for event in source_events_by_row[str(row["row_id"])]
            if event.kind == "issue"
            and event.meta.get("action") == "opened"
            and event.ts < snapshot
        ]
        for report in sorted(reports, key=lambda event: event.ts):
            number = _issue_number(report)
            if number is None or number in assigned_issues:
                continue
            confirmation_limit = min(snapshot + lookahead, confirmation_end)
            evidence = _confirmation_evidence(
                report=report,
                issue_events=issue_events[number],
                pull_requests=pull_requests,
                snapshot=snapshot,
                confirmation_limit=confirmation_limit,
                bug_labels=bug_labels,
                fixing_reference=fixing_reference,
                issue_number=number,
            )
            if evidence is None:
                continue
            lead_minutes = (evidence.ts - snapshot).total_seconds() / 60
            source = str(evidence.meta["v4_label_source"])
            incident_id = _incident_id(
                window_id, repo, number, evidence.ts, source
            )
            incident = {
                "incident_id": incident_id,
                "row_id": row["row_id"],
                "window_id": window_id,
                "split": split,
                "entity": repo,
                "issue_number": number,
                "label_source": source,
                "old_spike_eligible": row["old_spike_eligible"],
                "candidate_ts": row["candidate_ts"],
                "report_ts": format_timestamp(report.ts),
                "report_url": report.meta.get("url"),
                "report_excerpt": _excerpt(report.text),
                "confirmation_ts": format_timestamp(evidence.ts),
                "confirmation_url": evidence.meta.get("url"),
                "confirmation_excerpt": _excerpt(evidence.text),
                "lead_minutes": round(lead_minutes, 3),
            }
            cast(list[str], row["incident_ids"]).append(incident_id)
            row["label"] = 1
            incidents.append(incident)
            assigned_issues.add(number)
    return candidates, incidents


def _confirmation_evidence(
    *,
    report: Event,
    issue_events: Sequence[Event],
    pull_requests: Sequence[Event],
    snapshot: datetime,
    confirmation_limit: datetime,
    bug_labels: re.Pattern[str],
    fixing_reference: re.Pattern[str],
    issue_number: int,
) -> Event | None:
    evidence: list[Event] = []
    reporter = str(report.meta.get("author") or "").casefold()
    for event in issue_events:
        if not snapshot < event.ts <= confirmation_limit:
            continue
        if event.kind == "issue" and event.meta.get("action") == "closed":
            labels = " ".join(str(item) for item in event.meta.get("labels") or ())
            closer = str(event.meta.get("actor") or "").casefold()
            if (
                event.meta.get("state_reason") == "completed"
                and bug_labels.search(labels)
                and closer
                and closer != reporter
                and not _actor_is_bot(event)
            ):
                evidence.append(_with_source(event, "completed_bug_closure"))
        elif (
            event.kind == "comment"
            and str(event.meta.get("author_association") or "").upper()
            in MAINTAINER_ASSOCIATIONS
            and not _is_ack_bot(event)
            and BREAKAGE_REPORT.search(report.text or "")
            and classify_acknowledgment(event.text) == "TRUE"
        ):
            evidence.append(
                _with_source(event, "substantive_maintainer_comment")
            )
    for event in pull_requests:
        if not snapshot < event.ts <= confirmation_limit:
            continue
        if (
            event.meta.get("action") == "closed"
            and event.meta.get("merged") is True
            and issue_number in _fixing_issue_numbers(event.text, fixing_reference)
        ):
            evidence.append(_with_source(event, "merged_fixing_pr"))
    if not evidence:
        return None
    return min(
        evidence,
        key=lambda event: (
            event.ts,
            SOURCE_ORDER[str(event.meta["v4_label_source"])],
        ),
    )


def _with_source(event: Event, source: str) -> Event:
    return Event(
        ts=event.ts,
        entity=event.entity,
        kind=event.kind,
        text=event.text,
        value=event.value,
        meta={**event.meta, "v4_label_source": source},
    )


def _fixing_issue_numbers(
    text: str | None, pattern: re.Pattern[str]
) -> set[int]:
    return {int(match.group(1)) for match in pattern.finditer(text or "")}


def _activity_actor(event: Event) -> str | None:
    value = event.meta.get("author") or event.meta.get("actor")
    normalized = str(value or "").strip()
    return normalized or None


def _actor_is_bot(event: Event) -> bool:
    actor = str(event.meta.get("actor") or "").casefold()
    actor_type = str(event.meta.get("actor_type") or "").casefold()
    return actor_type == "bot" or actor.endswith("[bot]") or actor.endswith("-bot")


def _class_balance(
    rows: Sequence[Mapping[str, Any]], incidents: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    positives = sum(int(row["label"]) for row in rows)
    by_repo: dict[str, dict[str, int]] = {}
    for repo in sorted({str(row["entity"]) for row in rows}):
        repo_rows = [row for row in rows if row["entity"] == repo]
        by_repo[repo] = {
            "candidate_hours": len(repo_rows),
            "positive_hours": sum(int(row["label"]) for row in repo_rows),
            "positive_incidents": sum(
                incident["entity"] == repo for incident in incidents
            ),
        }
    return {
        "candidate_hours": len(rows),
        "positive_hours": positives,
        "negative_hours": len(rows) - positives,
        "positive_incidents": len(incidents),
        "positive_hour_rate": positives / len(rows) if rows else 0.0,
        "by_repository": by_repo,
    }


def _source_attribution(
    incidents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_source = {
        source: sum(row["label_source"] == source for row in incidents)
        for source in SOURCE_ORDER
    }
    outside = sum(not bool(row["old_spike_eligible"]) for row in incidents)
    return {
        "by_source": by_source,
        "outside_old_spike_population": outside,
        "inside_old_spike_population": len(incidents) - outside,
        "outside_old_spike_share": outside / len(incidents) if incidents else 0.0,
    }


def _row_id(window_id: str, repo: str, bucket: datetime) -> str:
    identity = f"v4|{window_id}|{repo}|{format_timestamp(bucket)}"
    return "v4row:" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def _incident_id(
    window_id: str,
    repo: str,
    issue_number: int,
    confirmation_ts: datetime,
    source: str,
) -> str:
    identity = (
        f"v4|{window_id}|{repo}|{issue_number}|"
        f"{format_timestamp(confirmation_ts)}|{source}"
    )
    return "v4incident:" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def _excerpt(value: str | None, limit: int = 480) -> str | None:
    normalized = " ".join((value or "").split())
    if not normalized:
        return None
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"
