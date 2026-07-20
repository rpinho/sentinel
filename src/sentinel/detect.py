"""Deterministic, source-agnostic trailing-baseline burst detection."""

from __future__ import annotations

import hashlib
import math
import statistics
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from sentinel.store import Firing
from sentinel.types import EVENT_KINDS, Event, EventKind, parse_timestamp

DetectorAlgorithm = Literal["count_z_v1", "robust_rate_v2"]
MetricDirection = Literal["spike", "drop"]
MetricSource = Literal[
    "issues_opened_per_active_participant",
    "comments_per_active_issue_thread",
    "first_time_participant_share",
    "issue_activity_volume",
]


@dataclass(frozen=True, slots=True)
class RateMetricConfig:
    metric_id: str
    source: MetricSource
    direction: MetricDirection
    minimum_exposure: float
    absolute_rate_floor: float
    minimum_absolute_change: float
    robust_z_threshold: float
    dispersion_floor_absolute: float
    dispersion_floor_fraction: float

    def __post_init__(self) -> None:
        if not self.metric_id.strip():
            raise ValueError("v2 metric_id cannot be empty")
        if self.minimum_exposure < 0:
            raise ValueError("v2 minimum_exposure cannot be negative")
        if self.absolute_rate_floor < 0:
            raise ValueError("v2 absolute_rate_floor cannot be negative")
        if self.minimum_absolute_change < 0:
            raise ValueError("v2 minimum_absolute_change cannot be negative")
        if self.robust_z_threshold <= 0:
            raise ValueError("v2 robust_z_threshold must be positive")
        if self.dispersion_floor_absolute <= 0:
            raise ValueError("v2 dispersion_floor_absolute must be positive")
        if self.dispersion_floor_fraction < 0:
            raise ValueError("v2 dispersion_floor_fraction cannot be negative")


@dataclass(frozen=True, slots=True)
class MetricEvaluation:
    entity: str
    bucket_start: datetime
    metric_id: str
    verdict: Literal[
        "immature",
        "insufficient_exposure",
        "quiet",
        "candidate",
        "automation_suppressed",
        "firing",
        "cooldown",
    ]
    raw: Mapping[str, Any]
    bot_excluded: Mapping[str, Any]
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DetectionRun:
    firings: tuple[Firing, ...]
    evaluations: tuple[MetricEvaluation, ...]

    @property
    def verdict_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for evaluation in self.evaluations:
            counts[evaluation.verdict] += 1
        return dict(sorted(counts.items()))


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    metric_id: str = "issue_activity_per_hour"
    kinds: tuple[EventKind, ...] = ("issue", "comment")
    bucket_minutes: int = 60
    baseline_buckets: int = 6
    minimum_baseline_buckets: int = 4
    z_score_threshold: float = 3.0
    standard_deviation_floor: float = 0.5
    volume_floor: int = 5
    minimum_absolute_increase: float = 4.0
    cooldown_buckets: int = 2
    meta_in: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    version: str = "unversioned"
    algorithm: DetectorAlgorithm = "count_z_v1"
    phase_history_days: int = 0
    minimum_phase_samples: int = 0
    mild_z_threshold: float = 0.0
    persistence_buckets: int = 1
    recent_release_hours: int = 0
    bot_min_signals: int = 2
    bot_name_patterns: tuple[str, ...] = ()
    bot_concentration_share: float = 1.0
    bot_concentration_min_events: int = 0
    bot_repeated_payload_min_events: int = 0
    bot_regular_timing_min_events: int = 0
    bot_regular_gap_mad_seconds: float = 0.0
    severity_digest_threshold: float = 0.0
    severity_interrupt_threshold: float = 1.0
    metrics: tuple[RateMetricConfig, ...] = ()

    def __post_init__(self) -> None:
        if not self.kinds or any(kind not in EVENT_KINDS for kind in self.kinds):
            raise ValueError(f"unknown or empty detector event kinds: {self.kinds}")
        if self.bucket_minutes < 1:
            raise ValueError("bucket_minutes must be positive")
        if self.algorithm == "count_z_v1":
            if self.baseline_buckets < 1:
                raise ValueError("baseline_buckets must be positive")
            if not 1 <= self.minimum_baseline_buckets <= self.baseline_buckets:
                raise ValueError(
                    "minimum_baseline_buckets must be within baseline_buckets"
                )
            if self.standard_deviation_floor <= 0:
                raise ValueError("standard_deviation_floor must be positive")
            if self.volume_floor < 1:
                raise ValueError("volume_floor must be positive")
        else:
            if self.phase_history_days < 2:
                raise ValueError("v2 phase_history_days must span multiple days")
            if self.minimum_phase_samples < 2:
                raise ValueError("v2 minimum_phase_samples must be at least two")
            if self.mild_z_threshold <= 0:
                raise ValueError("v2 mild_z_threshold must be positive")
            if self.persistence_buckets < 2:
                raise ValueError("v2 persistence_buckets must be at least two")
            if self.bot_min_signals < 2:
                raise ValueError("v2 bot classification requires multiple signals")
            if not self.metrics:
                raise ValueError("v2 requires registered metrics")
            if len({metric.metric_id for metric in self.metrics}) != len(self.metrics):
                raise ValueError("v2 metric IDs must be unique")
            if not 0 <= self.bot_concentration_share <= 1:
                raise ValueError("v2 bot_concentration_share must be in [0, 1]")
            if not 0 <= self.severity_digest_threshold <= 1:
                raise ValueError("v2 severity_digest_threshold must be in [0, 1]")
            if not 0 <= self.severity_interrupt_threshold <= 1:
                raise ValueError("v2 severity_interrupt_threshold must be in [0, 1]")
            if self.severity_digest_threshold > self.severity_interrupt_threshold:
                raise ValueError("v2 severity thresholds are reversed")
        if self.cooldown_buckets < 0:
            raise ValueError("cooldown_buckets cannot be negative")
        object.__setattr__(self, "kinds", tuple(self.kinds))
        object.__setattr__(
            self,
            "meta_in",
            {key: tuple(values) for key, values in self.meta_in.items()},
        )
        object.__setattr__(self, "bot_name_patterns", tuple(self.bot_name_patterns))
        object.__setattr__(self, "metrics", tuple(self.metrics))

    @property
    def bucket_size(self) -> timedelta:
        return timedelta(minutes=self.bucket_minutes)

    @classmethod
    def load(cls, path: Path | str | None = None) -> DetectorConfig:
        if path is None:
            raw_bytes = files("sentinel.config").joinpath("default.toml").read_bytes()
        else:
            raw_bytes = Path(path).read_bytes()
        document = tomllib.loads(raw_bytes.decode("utf-8"))
        raw = document.get("detector")
        if not isinstance(raw, dict):
            raise ValueError("detector config must contain a [detector] table")
        version = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()[:12]
        algorithm = cast(DetectorAlgorithm, raw.get("algorithm", "count_z_v1"))
        if algorithm not in {"count_z_v1", "robust_rate_v2"}:
            raise ValueError(f"unsupported detector algorithm: {algorithm}")
        raw_metrics = raw.get("metrics") or []
        if not isinstance(raw_metrics, list):
            raise ValueError("detector.metrics must be an array of tables")
        metrics = tuple(
            RateMetricConfig(
                metric_id=str(item["metric_id"]),
                source=cast(MetricSource, item["source"]),
                direction=cast(MetricDirection, item["direction"]),
                minimum_exposure=float(item["minimum_exposure"]),
                absolute_rate_floor=float(item["absolute_rate_floor"]),
                minimum_absolute_change=float(item["minimum_absolute_change"]),
                robust_z_threshold=float(item["robust_z_threshold"]),
                dispersion_floor_absolute=float(item["dispersion_floor_absolute"]),
                dispersion_floor_fraction=float(item["dispersion_floor_fraction"]),
            )
            for item in raw_metrics
        )
        return cls(
            metric_id=str(raw.get("metric_id", "issue_activity_per_hour")),
            kinds=tuple(cast(list[EventKind], raw.get("kinds", ["issue", "comment"]))),
            bucket_minutes=int(raw.get("bucket_minutes", 60)),
            baseline_buckets=int(raw.get("baseline_buckets", 6)),
            minimum_baseline_buckets=int(raw.get("minimum_baseline_buckets", 4)),
            z_score_threshold=float(raw.get("z_score_threshold", 3.0)),
            standard_deviation_floor=float(
                raw.get("standard_deviation_floor", 0.5)
            ),
            volume_floor=int(raw.get("volume_floor", 5)),
            minimum_absolute_increase=float(raw.get("minimum_absolute_increase", 4.0)),
            cooldown_buckets=int(raw.get("cooldown_buckets", 2)),
            meta_in=raw.get("meta_in") or {},
            version=version,
            algorithm=algorithm,
            phase_history_days=int(raw.get("phase_history_days", 0)),
            minimum_phase_samples=int(raw.get("minimum_phase_samples", 0)),
            mild_z_threshold=float(raw.get("mild_z_threshold", 0.0)),
            persistence_buckets=int(raw.get("persistence_buckets", 1)),
            recent_release_hours=int(raw.get("recent_release_hours", 0)),
            bot_min_signals=int(raw.get("bot_min_signals", 2)),
            bot_name_patterns=tuple(raw.get("bot_name_patterns") or ()),
            bot_concentration_share=float(raw.get("bot_concentration_share", 1.0)),
            bot_concentration_min_events=int(
                raw.get("bot_concentration_min_events", 0)
            ),
            bot_repeated_payload_min_events=int(
                raw.get("bot_repeated_payload_min_events", 0)
            ),
            bot_regular_timing_min_events=int(
                raw.get("bot_regular_timing_min_events", 0)
            ),
            bot_regular_gap_mad_seconds=float(
                raw.get("bot_regular_gap_mad_seconds", 0.0)
            ),
            severity_digest_threshold=float(
                raw.get("severity_digest_threshold", 0.0)
            ),
            severity_interrupt_threshold=float(
                raw.get("severity_interrupt_threshold", 1.0)
            ),
            metrics=metrics,
        )


def detect_bursts(
    events: Iterable[Event],
    config: DetectorConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Firing]:
    """Return surfaced firings while preserving the v1 public API."""

    return list(evaluate_detection(events, config, start=start, end=end).firings)


def evaluate_detection(
    events: Iterable[Event],
    config: DetectorConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> DetectionRun:
    materialized = tuple(events)
    if config.algorithm == "robust_rate_v2":
        from sentinel.detector_v2 import evaluate_v2

        return evaluate_v2(materialized, config, start=start, end=end)
    return DetectionRun(
        firings=tuple(_detect_v1_bursts(materialized, config, start=start, end=end)),
        evaluations=(),
    )


def _detect_v1_bursts(
    events: Iterable[Event],
    config: DetectorConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Firing]:
    """Detect per-entity bursts using completed prior buckets only."""

    all_events = sorted(events, key=lambda event: (event.entity, event.ts))
    if not all_events:
        return []
    start_utc = (
        parse_timestamp(start) if start else min(event.ts for event in all_events)
    )
    end_utc = (
        parse_timestamp(end)
        if end
        else max(event.ts for event in all_events) + config.bucket_size
    )
    if end_utc <= start_utc:
        raise ValueError("detection end must follow start")
    start_bucket = _floor_bucket(start_utc, config.bucket_minutes)
    end_bucket = _ceil_bucket(end_utc, config.bucket_minutes)

    entities = sorted({event.entity for event in all_events})
    matching: dict[str, dict[datetime, list[Event]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in all_events:
        if event.ts < start_utc or event.ts >= end_utc or not _matches(event, config):
            continue
        matching[event.entity][_floor_bucket(event.ts, config.bucket_minutes)].append(
            event
        )

    firings: list[Firing] = []
    for entity in entities:
        counts: list[int] = []
        cooldown_until: datetime | None = None
        bucket_start = start_bucket
        while bucket_start < end_bucket:
            bucket_events = sorted(
                matching[entity].get(bucket_start, []), key=lambda event: event.ts
            )
            observed = len(bucket_events)
            baseline = counts[-config.baseline_buckets :]
            if len(baseline) >= config.minimum_baseline_buckets:
                mean = statistics.fmean(baseline)
                stddev = statistics.pstdev(baseline)
                required = _required_count(mean, stddev, config)
                eligible = cooldown_until is None or bucket_start >= cooldown_until
                if eligible and observed >= required:
                    crossing_event = bucket_events[required - 1]
                    score = (required - mean) / max(
                        stddev, config.standard_deviation_floor
                    )
                    bucket_end = bucket_start + config.bucket_size
                    firing_id = _firing_id(entity, config, bucket_start)
                    firings.append(
                        Firing(
                            firing_id=firing_id,
                            ts=crossing_event.ts,
                            entity=entity,
                            metric_id=config.metric_id,
                            kind="metric",
                            window_start=bucket_start,
                            window_end=bucket_end,
                            observed=required,
                            baseline_mean=mean,
                            baseline_stddev=stddev,
                            score=score,
                            detector_version=config.version,
                            meta={
                                "bucket_total": observed,
                                "baseline_counts": baseline,
                                "threshold_count": required,
                                "z_score_threshold": config.z_score_threshold,
                                "volume_floor": config.volume_floor,
                                "minimum_absolute_increase": config.minimum_absolute_increase,
                                "source_event_kinds": list(config.kinds),
                            },
                        )
                    )
                    cooldown_until = bucket_end + (
                        config.bucket_size * config.cooldown_buckets
                    )
            counts.append(observed)
            bucket_start += config.bucket_size
    return firings


def _matches(event: Event, config: DetectorConfig) -> bool:
    return event.kind in config.kinds and all(
        event.meta.get(key) in values for key, values in config.meta_in.items()
    )


def _required_count(mean: float, stddev: float, config: DetectorConfig) -> int:
    statistical_threshold = mean + config.z_score_threshold * max(
        stddev, config.standard_deviation_floor
    )
    absolute_threshold = mean + config.minimum_absolute_increase
    return max(
        config.volume_floor,
        math.ceil(statistical_threshold),
        math.ceil(absolute_threshold),
    )


def _floor_bucket(value: datetime, bucket_minutes: int) -> datetime:
    value = parse_timestamp(value)
    epoch_minutes = int(value.timestamp()) // 60
    floored_minutes = epoch_minutes - (epoch_minutes % bucket_minutes)
    return datetime.fromtimestamp(floored_minutes * 60, tz=UTC)


def _ceil_bucket(value: datetime, bucket_minutes: int) -> datetime:
    floored = _floor_bucket(value, bucket_minutes)
    return floored if floored == value else floored + timedelta(minutes=bucket_minutes)


def _firing_id(entity: str, config: DetectorConfig, bucket_start: datetime) -> str:
    identity = "|".join(
        (entity, config.metric_id, bucket_start.isoformat(), config.version)
    )
    return "firing:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
