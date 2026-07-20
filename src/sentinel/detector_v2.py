"""Phase-matched robust-rate detector with explicit diagnostic verdicts."""

from __future__ import annotations

import hashlib
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sentinel.detect import (
    DetectionRun,
    DetectorConfig,
    MetricEvaluation,
    RateMetricConfig,
)
from sentinel.store import Firing
from sentinel.types import Event, parse_timestamp

MAD_NORMAL_SCALE = 1.4826


@dataclass(frozen=True, slots=True)
class _Point:
    bucket_start: datetime
    value: float | None
    numerator: float
    denominator: float


@dataclass(frozen=True, slots=True)
class _Score:
    state: str
    value: float | None
    numerator: float
    denominator: float
    center: float | None = None
    dispersion: float | None = None
    score: float | None = None
    absolute_change: float | None = None
    baseline_samples: int = 0
    locked_samples: int = 0
    thin_pool_fallback: bool = False
    exposure_override: str | None = None
    flagged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "baseline_center": self.center,
            "baseline_dispersion": self.dispersion,
            "robust_z": self.score,
            "absolute_change": self.absolute_change,
            "baseline_samples": self.baseline_samples,
            "locked_samples": self.locked_samples,
            "thin_pool_fallback": self.thin_pool_fallback,
            "exposure_override": self.exposure_override,
            "flagged": self.flagged,
        }


@dataclass(frozen=True, slots=True)
class _BucketContext:
    events: tuple[Event, ...]
    bot_excluded_events: tuple[Event, ...]
    raw_participants: frozenset[str]
    excluded_participants: frozenset[str]
    automated_actors: Mapping[str, tuple[str, ...]]


def evaluate_v2(
    events: Sequence[Event],
    config: DetectorConfig,
    *,
    start: datetime | None,
    end: datetime | None,
) -> DetectionRun:
    """Evaluate v2 over a complete time spine and surface admitted candidates."""

    ordered = tuple(sorted(events, key=lambda event: (event.entity, event.ts)))
    if not ordered:
        return DetectionRun(firings=(), evaluations=())
    start_at = (
        parse_timestamp(start)
        if start
        else min(event.ts for event in ordered)
        + timedelta(days=config.phase_history_days)
    )
    end_at = (
        parse_timestamp(end)
        if end
        else max(event.ts for event in ordered) + config.bucket_size
    )
    if end_at <= start_at:
        raise ValueError("detection end must follow start")
    history_start = start_at - timedelta(days=config.phase_history_days)
    start_bucket = _floor_bucket(start_at, config.bucket_minutes)
    history_bucket = _floor_bucket(history_start, config.bucket_minutes)
    end_bucket = _ceil_bucket(end_at, config.bucket_minutes)
    buckets = tuple(_iter_buckets(history_bucket, end_bucket, config.bucket_size))
    entities = sorted({event.entity for event in ordered})

    by_entity_bucket: dict[tuple[str, datetime], list[Event]] = defaultdict(list)
    releases: dict[str, list[Event]] = defaultdict(list)
    for event in ordered:
        if event.ts < history_start or event.ts >= end_at:
            continue
        if event.kind == "release":
            releases[event.entity].append(event)
        if _activity_event(event, config):
            by_entity_bucket[
                (event.entity, _floor_bucket(event.ts, config.bucket_minutes))
            ].append(event)

    points: dict[tuple[str, str, str], list[_Point]] = defaultdict(list)
    contexts: dict[tuple[str, datetime], _BucketContext] = {}
    for entity in entities:
        seen_raw: set[str] = set()
        seen_excluded: set[str] = set()
        for bucket in buckets:
            bucket_events = tuple(
                sorted(by_entity_bucket.get((entity, bucket), ()), key=lambda row: row.ts)
            )
            automated = _automated_actors(bucket_events, config)
            raw_participants = _participants(bucket_events)
            excluded_events = tuple(
                event
                for event in bucket_events
                if (_actor(event) or "") not in automated
            )
            excluded_participants = _participants(excluded_events)
            contexts[(entity, bucket)] = _BucketContext(
                events=bucket_events,
                bot_excluded_events=excluded_events,
                raw_participants=frozenset(raw_participants),
                excluded_participants=frozenset(excluded_participants),
                automated_actors=automated,
            )
            for metric in config.metrics:
                points[(entity, metric.metric_id, "raw")].append(
                    _metric_point(
                        metric,
                        bucket,
                        bucket_events,
                        first_time=raw_participants - seen_raw,
                    )
                )
                points[(entity, metric.metric_id, "bot_excluded")].append(
                    _metric_point(
                        metric,
                        bucket,
                        excluded_events,
                        first_time=excluded_participants - seen_excluded,
                    )
                )
            seen_raw.update(raw_participants)
            seen_excluded.update(excluded_participants)

    bootstrap_locked = _bootstrap_locks(points, config)
    scores = _score_all(points, bootstrap_locked, config)
    evaluations: list[MetricEvaluation] = []
    evaluation_index: dict[tuple[str, datetime, str], int] = {}
    candidates: dict[tuple[str, datetime], list[str]] = defaultdict(list)
    metric_by_id = {metric.metric_id: metric for metric in config.metrics}

    for entity in entities:
        for bucket in buckets:
            if bucket < start_bucket:
                continue
            context = contexts[(entity, bucket)]
            for metric in config.metrics:
                raw = scores[(entity, metric.metric_id, "raw", bucket)]
                excluded = scores[(entity, metric.metric_id, "bot_excluded", bucket)]
                verdict = _combined_verdict(raw, excluded)
                if verdict == "candidate":
                    candidates[(entity, bucket)].append(metric.metric_id)
                evaluation_index[(entity, bucket, metric.metric_id)] = len(evaluations)
                evaluations.append(
                    MetricEvaluation(
                        entity=entity,
                        bucket_start=bucket,
                        metric_id=metric.metric_id,
                        verdict=verdict,
                        raw=raw.to_dict(),
                        bot_excluded=excluded.to_dict(),
                        meta={
                            "phase": {
                                "hour_of_day": bucket.hour,
                                "day_class": _day_class(bucket),
                            },
                            "automated_actors": {
                                actor: list(signals)
                                for actor, signals in sorted(
                                    context.automated_actors.items()
                                )
                            },
                            "raw_participants": len(context.raw_participants),
                            "bot_excluded_participants": len(
                                context.excluded_participants
                            ),
                        },
                    )
                )

    firings: list[Firing] = []
    for entity in entities:
        streaks: dict[str, int] = defaultdict(int)
        previous_scores: dict[str, float] = {}
        cooldown_until: datetime | None = None
        for bucket in buckets:
            if bucket < start_bucket:
                continue
            candidate_ids = candidates.get((entity, bucket), [])
            candidate_set = set(candidate_ids)
            for metric in config.metrics:
                if metric.metric_id in candidate_set:
                    streaks[metric.metric_id] += 1
                else:
                    streaks[metric.metric_id] = 0
            persistent = sorted(
                metric_id
                for metric_id in candidate_ids
                if streaks[metric_id] >= config.persistence_buckets
            )
            recent_release = _recent_release(
                releases.get(entity, ()), bucket, config
            )
            multi_metric = len(candidate_ids) >= 2
            # Multiple ratios derived from the same GH Archive events add
            # breadth, but they are not an independent instrumentation path.
            independently_corroborated = bool(recent_release)
            admitted = bool(candidate_ids) and (
                bool(persistent) or independently_corroborated
            )
            eligible = cooldown_until is None or bucket >= cooldown_until

            if admitted and not eligible:
                for metric_id in candidate_ids:
                    index = evaluation_index[(entity, bucket, metric_id)]
                    evaluations[index] = _with_verdict(evaluations[index], "cooldown")
                continue
            if not admitted:
                for metric_id in candidate_ids:
                    score = scores[(entity, metric_id, "bot_excluded", bucket)].score
                    if score is not None:
                        previous_scores[metric_id] = score
                continue

            for metric_id in candidate_ids:
                index = evaluation_index[(entity, bucket, metric_id)]
                evaluations[index] = _with_verdict(evaluations[index], "firing")
            primary_id = max(
                candidate_ids,
                key=lambda metric_id: scores[
                    (entity, metric_id, "bot_excluded", bucket)
                ].score
                or -math.inf,
            )
            primary_metric = metric_by_id[primary_id]
            primary = scores[(entity, primary_id, "bot_excluded", bucket)]
            context = contexts[(entity, bucket)]
            severity = _severity(
                candidate_ids=candidate_ids,
                persistent=persistent,
                independently_corroborated=independently_corroborated,
                context=context,
                scores=scores,
                previous_scores=previous_scores,
                entity=entity,
                bucket=bucket,
                metrics=metric_by_id,
                config=config,
            )
            firing_ts = _firing_timestamp(context.events, bucket, config.bucket_size)
            firing_id = _firing_id(entity, primary_id, bucket, config.version)
            firings.append(
                Firing(
                    firing_id=firing_id,
                    ts=firing_ts,
                    entity=entity,
                    metric_id=primary_id,
                    kind="metric",
                    window_start=bucket,
                    window_end=bucket + config.bucket_size,
                    observed=float(primary.value or 0.0),
                    baseline_mean=float(primary.center or 0.0),
                    baseline_stddev=float(primary.dispersion or 0.0),
                    score=float(primary.score or 0.0),
                    detector_version=config.version,
                    meta={
                        "algorithm": config.algorithm,
                        "bucket_total": len(context.events),
                        "candidate_metrics": candidate_ids,
                        "persistent_metrics": persistent,
                        "independent_corroboration": {
                            "multiple_metrics": multi_metric,
                            "recent_release": (
                                recent_release.to_dict() if recent_release else None
                            ),
                        },
                        "primary_metric": primary_metric.metric_id,
                        "metric_evaluations": {
                            metric_id: {
                                "raw": scores[
                                    (entity, metric_id, "raw", bucket)
                                ].to_dict(),
                                "bot_excluded": scores[
                                    (entity, metric_id, "bot_excluded", bucket)
                                ].to_dict(),
                            }
                            for metric_id in candidate_ids
                        },
                        "automated_actors": {
                            actor: list(signals)
                            for actor, signals in sorted(
                                context.automated_actors.items()
                            )
                        },
                        "severity": severity,
                    },
                )
            )
            cooldown_until = (
                bucket
                + config.bucket_size
                + config.bucket_size * config.cooldown_buckets
            )
            for metric_id in candidate_ids:
                score = scores[(entity, metric_id, "bot_excluded", bucket)].score
                if score is not None:
                    previous_scores[metric_id] = score

    return DetectionRun(firings=tuple(firings), evaluations=tuple(evaluations))


def _activity_event(event: Event, config: DetectorConfig) -> bool:
    return event.kind in config.kinds and all(
        event.meta.get(key) in values for key, values in config.meta_in.items()
    )


def _actor(event: Event) -> str | None:
    raw = event.meta.get("author") or event.meta.get("actor")
    value = str(raw).strip() if raw is not None else ""
    return value or None


def _participants(events: Iterable[Event]) -> set[str]:
    return {
        actor
        for event in events
        if not _is_pull_request(event)
        if (actor := _actor(event)) is not None
    }


def _is_pull_request(event: Event) -> bool:
    if bool(event.meta.get("is_pull_request")):
        return True
    return "/pull/" in str(event.meta.get("url") or "")


def _metric_point(
    metric: RateMetricConfig,
    bucket: datetime,
    events: Sequence[Event],
    *,
    first_time: set[str],
) -> _Point:
    issue_events = [
        event
        for event in events
        if event.kind == "issue"
        and event.meta.get("action") == "opened"
        and not _is_pull_request(event)
    ]
    comments = [
        event
        for event in events
        if event.kind == "comment"
        and event.meta.get("action") == "created"
        and not _is_pull_request(event)
    ]
    participants = _participants((*issue_events, *comments))
    threads = {
        str(event.meta["issue_number"])
        for event in (*issue_events, *comments)
        if event.meta.get("issue_number") is not None
    }
    if metric.source == "issues_opened_per_active_participant":
        numerator = float(len(issue_events))
        denominator = float(len(participants))
    elif metric.source == "comments_per_active_issue_thread":
        numerator = float(len(comments))
        denominator = float(len(threads))
    elif metric.source == "first_time_participant_event_share":
        relevant_events = (*issue_events, *comments)
        numerator = float(
            sum(1 for event in relevant_events if _actor(event) in first_time)
        )
        denominator = float(len(relevant_events))
    else:
        numerator = float(len(events))
        denominator = float(len(events))
    value = numerator / denominator if denominator else None
    if metric.source == "issue_activity_volume":
        value = numerator
    return _Point(
        bucket_start=bucket,
        value=value,
        numerator=numerator,
        denominator=denominator,
    )


def _automated_actors(
    events: Sequence[Event], config: DetectorConfig
) -> dict[str, tuple[str, ...]]:
    by_actor: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if (actor := _actor(event)) is not None:
            by_actor[actor].append(event)
    total = len(events)
    automated: dict[str, tuple[str, ...]] = {}
    for actor, actor_events in by_actor.items():
        signals: list[str] = []
        actor_folded = actor.casefold()
        if any(re.search(pattern, actor_folded) for pattern in config.bot_name_patterns):
            signals.append("name_pattern")
        if any(
            str(
                event.meta.get("actor_type")
                or event.meta.get("author_type")
                or ""
            ).casefold()
            == "bot"
            for event in actor_events
        ):
            signals.append("account_type")
        if (
            len(actor_events) >= config.bot_concentration_min_events
            and total
            and len(actor_events) / total >= config.bot_concentration_share
        ):
            signals.append("single_actor_concentration")
        signatures = Counter(
            signature
            for event in actor_events
            if (signature := _payload_signature(event.text)) is not None
        )
        if (
            signatures
            and max(signatures.values()) >= config.bot_repeated_payload_min_events
        ):
            signals.append("repeated_payload")
        if _regular_timing(actor_events, config):
            signals.append("hyper_regular_timing")
        if len(signals) >= config.bot_min_signals:
            automated[actor] = tuple(signals)
    return automated


def _payload_signature(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        normalized = " ".join(line.casefold().split())
        if normalized:
            return normalized
    return None


def _regular_timing(events: Sequence[Event], config: DetectorConfig) -> bool:
    if len(events) < config.bot_regular_timing_min_events:
        return False
    ordered = sorted(event.ts for event in events)
    gaps = [
        (right - left).total_seconds() for left, right in zip(ordered, ordered[1:])
    ]
    if not gaps:
        return False
    center = statistics.median(gaps)
    gap_mad = statistics.median(abs(gap - center) for gap in gaps)
    return gap_mad <= config.bot_regular_gap_mad_seconds


def _bootstrap_locks(
    points: Mapping[tuple[str, str, str], list[_Point]], config: DetectorConfig
) -> set[tuple[str, str, str, datetime]]:
    locked: set[tuple[str, str, str, datetime]] = set()
    metrics = {metric.metric_id: metric for metric in config.metrics}
    for (entity, metric_id, variant), series in points.items():
        metric = metrics[metric_id]
        for index, point in enumerate(series):
            if point.value is None:
                continue
            pool = _phase_pool(series, index, point.bucket_start, config)
            values = [candidate.value for candidate in pool if candidate.value is not None]
            if len(values) < config.minimum_phase_samples:
                continue
            center, dispersion = _robust_baseline(
                [float(value) for value in values], metric
            )
            if _directional_score(point.value, center, dispersion, metric.direction) >= (
                config.mild_z_threshold
            ):
                locked.add((entity, metric_id, variant, point.bucket_start))
    return locked


def _score_all(
    points: Mapping[tuple[str, str, str], list[_Point]],
    locked: set[tuple[str, str, str, datetime]],
    config: DetectorConfig,
) -> dict[tuple[str, str, str, datetime], _Score]:
    result: dict[tuple[str, str, str, datetime], _Score] = {}
    metrics = {metric.metric_id: metric for metric in config.metrics}
    for (entity, metric_id, variant), series in points.items():
        metric = metrics[metric_id]
        for index, point in enumerate(series):
            pool = _phase_pool(series, index, point.bucket_start, config)
            usable = [candidate for candidate in pool if candidate.value is not None]
            if len(usable) < config.minimum_phase_samples:
                score = _Score(
                    state="immature",
                    value=point.value,
                    numerator=point.numerator,
                    denominator=point.denominator,
                    baseline_samples=len(usable),
                    locked_samples=len(usable),
                )
            else:
                clean = [
                    candidate
                    for candidate in usable
                    if (
                        entity,
                        metric_id,
                        variant,
                        candidate.bucket_start,
                    )
                    not in locked
                ]
                fallback = len(clean) < config.minimum_phase_samples
                baseline = usable if fallback else clean
                values = [float(candidate.value) for candidate in baseline]
                center, dispersion = _robust_baseline(values, metric)
                score = _score_point(
                    point,
                    metric,
                    center=center,
                    dispersion=dispersion,
                    baseline_samples=len(usable),
                    locked_samples=len(clean),
                    fallback=fallback,
                )
            result[(entity, metric_id, variant, point.bucket_start)] = score
    return result


def _phase_pool(
    series: Sequence[_Point],
    index: int,
    bucket: datetime,
    config: DetectorConfig,
) -> list[_Point]:
    oldest = bucket - timedelta(days=config.phase_history_days)
    return [
        point
        for point in series[:index]
        if point.bucket_start >= oldest
        and point.bucket_start.hour == bucket.hour
        and _day_class(point.bucket_start) == _day_class(bucket)
    ]


def _robust_baseline(
    values: Sequence[float], metric: RateMetricConfig
) -> tuple[float, float]:
    center = statistics.median(values)
    mad = statistics.median(abs(value - center) for value in values)
    dispersion = max(
        mad * MAD_NORMAL_SCALE,
        metric.dispersion_floor_absolute,
        abs(center) * metric.dispersion_floor_fraction,
    )
    return center, dispersion


def _score_point(
    point: _Point,
    metric: RateMetricConfig,
    *,
    center: float,
    dispersion: float,
    baseline_samples: int,
    locked_samples: int,
    fallback: bool,
) -> _Score:
    if point.value is None:
        return _Score(
            state="insufficient_exposure",
            value=None,
            numerator=point.numerator,
            denominator=point.denominator,
            center=center,
            dispersion=dispersion,
            baseline_samples=baseline_samples,
            locked_samples=locked_samples,
            thin_pool_fallback=fallback,
        )
    collapse_override = (
        metric.direction == "drop"
        and point.value == 0
        and center >= max(metric.minimum_exposure, metric.absolute_rate_floor)
    )
    if point.denominator < metric.minimum_exposure and not collapse_override:
        return _Score(
            state="insufficient_exposure",
            value=point.value,
            numerator=point.numerator,
            denominator=point.denominator,
            center=center,
            dispersion=dispersion,
            baseline_samples=baseline_samples,
            locked_samples=locked_samples,
            thin_pool_fallback=fallback,
        )
    clears_absolute_floor = (
        point.value >= metric.absolute_rate_floor
        if metric.direction == "spike"
        else center >= metric.absolute_rate_floor
    )
    if not clears_absolute_floor:
        return _Score(
            state="quiet",
            value=point.value,
            numerator=point.numerator,
            denominator=point.denominator,
            center=center,
            dispersion=dispersion,
            baseline_samples=baseline_samples,
            locked_samples=locked_samples,
            thin_pool_fallback=fallback,
        )
    absolute_change = (
        point.value - center
        if metric.direction == "spike"
        else center - point.value
    )
    if absolute_change < metric.minimum_absolute_change:
        return _Score(
            state="quiet",
            value=point.value,
            numerator=point.numerator,
            denominator=point.denominator,
            center=center,
            dispersion=dispersion,
            absolute_change=absolute_change,
            baseline_samples=baseline_samples,
            locked_samples=locked_samples,
            thin_pool_fallback=fallback,
            exposure_override="total_collapse" if collapse_override else None,
        )
    score = _directional_score(point.value, center, dispersion, metric.direction)
    flagged = score >= metric.robust_z_threshold
    return _Score(
        state="flagged" if flagged else "quiet",
        value=point.value,
        numerator=point.numerator,
        denominator=point.denominator,
        center=center,
        dispersion=dispersion,
        score=score,
        absolute_change=absolute_change,
        baseline_samples=baseline_samples,
        locked_samples=locked_samples,
        thin_pool_fallback=fallback,
        exposure_override="total_collapse" if collapse_override else None,
        flagged=flagged,
    )


def _directional_score(
    value: float, center: float, dispersion: float, direction: str
) -> float:
    return (
        (value - center) / dispersion
        if direction == "spike"
        else (center - value) / dispersion
    )


def _combined_verdict(raw: _Score, excluded: _Score) -> str:
    if raw.state == "immature" or excluded.state == "immature":
        return "immature"
    if raw.flagged and excluded.flagged:
        return "candidate"
    if raw.flagged and not excluded.flagged:
        return "automation_suppressed"
    if (
        raw.state == "insufficient_exposure"
        or excluded.state == "insufficient_exposure"
    ):
        return "insufficient_exposure"
    return "quiet"


def _with_verdict(
    evaluation: MetricEvaluation, verdict: str
) -> MetricEvaluation:
    return MetricEvaluation(
        entity=evaluation.entity,
        bucket_start=evaluation.bucket_start,
        metric_id=evaluation.metric_id,
        verdict=verdict,  # type: ignore[arg-type]
        raw=evaluation.raw,
        bot_excluded=evaluation.bot_excluded,
        meta=evaluation.meta,
    )


def _recent_release(
    releases: Iterable[Event], bucket: datetime, config: DetectorConfig
) -> Event | None:
    bucket_end = bucket + config.bucket_size
    oldest = bucket - timedelta(hours=config.recent_release_hours)
    candidates = [
        event for event in releases if oldest <= event.ts < bucket_end
    ]
    return max(candidates, key=lambda event: event.ts, default=None)


def _severity(
    *,
    candidate_ids: Sequence[str],
    persistent: Sequence[str],
    independently_corroborated: bool,
    context: _BucketContext,
    scores: Mapping[tuple[str, str, str, datetime], _Score],
    previous_scores: Mapping[str, float],
    entity: str,
    bucket: datetime,
    metrics: Mapping[str, RateMetricConfig],
    config: DetectorConfig,
) -> dict[str, Any]:
    breadth = max(
        _metric_breadth(
            context=context,
            score=scores[(entity, metric_id, "bot_excluded", bucket)],
            metric=metrics[metric_id],
        )
        for metric_id in candidate_ids
    )
    depth = max(
        min(
            1.0,
            float(scores[(entity, metric_id, "bot_excluded", bucket)].score or 0.0)
            / (
                config.severity_depth_saturation_multiplier
                * metrics[metric_id].robust_z_threshold
            ),
        )
        for metric_id in candidate_ids
    )
    velocity = max(
        min(
            1.0,
            max(
                0.0,
                float(scores[(entity, metric_id, "bot_excluded", bucket)].score or 0.0)
                - previous_scores.get(metric_id, 0.0),
            )
            / metrics[metric_id].robust_z_threshold,
        )
        for metric_id in candidate_ids
    )
    expected_phase_samples = _expected_phase_samples(bucket, config)
    phase_confidence = min(
        scores[(entity, metric_id, "bot_excluded", bucket)].baseline_samples
        / expected_phase_samples
        for metric_id in candidate_ids
    )
    thin_fallback = any(
        scores[(entity, metric_id, "bot_excluded", bucket)].thin_pool_fallback
        for metric_id in candidate_ids
    )
    structural_confidence = (
        1.0
        if persistent and len(candidate_ids) >= 2
        else config.severity_lower_confidence_cap
    )
    confidence = min(
        phase_confidence,
        structural_confidence,
        0.5 if thin_fallback else 1.0,
    )
    attention = breadth * depth * velocity * confidence
    interrupt_eligible = bool(persistent) and len(candidate_ids) >= 2 and not thin_fallback
    if interrupt_eligible and attention >= config.severity_interrupt_threshold:
        severity = "interrupt"
    elif attention >= config.severity_digest_threshold:
        severity = "digest"
    else:
        severity = "watch"
    return {
        "severity": severity,
        "attention_score": attention,
        "breadth": breadth,
        "depth": depth,
        "velocity": velocity,
        "confidence": confidence,
        "interrupt_eligible": interrupt_eligible,
        "independently_corroborated": independently_corroborated,
    }


def _metric_breadth(
    *,
    context: _BucketContext,
    score: _Score,
    metric: RateMetricConfig,
) -> float:
    events = tuple(
        event
        for event in context.bot_excluded_events
        if not _is_pull_request(event)
    )
    participants = _participants(events)
    threads = {
        str(event.meta["issue_number"])
        for event in events
        if event.meta.get("issue_number") is not None
    }
    if metric.source == "issues_opened_per_active_participant":
        affected = {
            actor
            for event in events
            if event.kind == "issue"
            and event.meta.get("action") == "opened"
            if (actor := _actor(event)) is not None
        }
        return len(affected) / len(participants) if participants else 0.0
    if metric.source == "comments_per_active_issue_thread":
        affected = {
            str(event.meta["issue_number"])
            for event in events
            if event.kind == "comment"
            and event.meta.get("action") == "created"
            and event.meta.get("issue_number") is not None
        }
        return len(affected) / len(threads) if threads else 0.0
    if metric.source == "first_time_participant_event_share":
        return min(1.0, max(0.0, float(score.value or 0.0)))
    # A volume collapse is repository-wide by construction.
    return 1.0


def _expected_phase_samples(bucket: datetime, config: DetectorConfig) -> int:
    return sum(
        1
        for days_ago in range(1, config.phase_history_days + 1)
        if _day_class(bucket - timedelta(days=days_ago)) == _day_class(bucket)
    )


def _firing_timestamp(
    events: Sequence[Event], bucket: datetime, bucket_size: timedelta
) -> datetime:
    return max((event.ts for event in events), default=bucket + bucket_size)


def _firing_id(
    entity: str, metric_id: str, bucket: datetime, detector_version: str
) -> str:
    identity = "|".join(
        (entity, metric_id, bucket.isoformat(), detector_version)
    )
    return "firing:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _day_class(value: datetime) -> str:
    return "weekend" if value.weekday() >= 5 else "weekday"


def _floor_bucket(value: datetime, bucket_minutes: int) -> datetime:
    value = parse_timestamp(value)
    epoch_minutes = int(value.timestamp()) // 60
    floored_minutes = epoch_minutes - (epoch_minutes % bucket_minutes)
    return datetime.fromtimestamp(floored_minutes * 60, tz=UTC)


def _ceil_bucket(value: datetime, bucket_minutes: int) -> datetime:
    floored = _floor_bucket(value, bucket_minutes)
    return floored if floored == value else floored + timedelta(minutes=bucket_minutes)


def _iter_buckets(
    start: datetime, end: datetime, size: timedelta
) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += size
