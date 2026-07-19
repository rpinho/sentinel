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
from typing import Any, Iterable, Mapping, cast

from sentinel.store import Firing
from sentinel.types import EVENT_KINDS, Event, EventKind, parse_timestamp


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    metric_id: str
    kind: EventKind
    bucket_minutes: int
    baseline_buckets: int
    minimum_baseline_buckets: int
    z_score_threshold: float
    standard_deviation_floor: float
    volume_floor: int
    minimum_absolute_increase: float
    cooldown_buckets: int
    meta_equals: Mapping[str, Any] = field(default_factory=dict)
    version: str = "unversioned"

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown detector event kind: {self.kind}")
        if self.bucket_minutes < 1:
            raise ValueError("bucket_minutes must be positive")
        if self.baseline_buckets < 1:
            raise ValueError("baseline_buckets must be positive")
        if not 1 <= self.minimum_baseline_buckets <= self.baseline_buckets:
            raise ValueError("minimum_baseline_buckets must be within baseline_buckets")
        if self.standard_deviation_floor <= 0:
            raise ValueError("standard_deviation_floor must be positive")
        if self.volume_floor < 1:
            raise ValueError("volume_floor must be positive")
        if self.cooldown_buckets < 0:
            raise ValueError("cooldown_buckets cannot be negative")
        object.__setattr__(self, "meta_equals", dict(self.meta_equals))

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
        return cls(
            metric_id=str(raw["metric_id"]),
            kind=cast(EventKind, raw["kind"]),
            bucket_minutes=int(raw["bucket_minutes"]),
            baseline_buckets=int(raw["baseline_buckets"]),
            minimum_baseline_buckets=int(raw["minimum_baseline_buckets"]),
            z_score_threshold=float(raw["z_score_threshold"]),
            standard_deviation_floor=float(raw["standard_deviation_floor"]),
            volume_floor=int(raw["volume_floor"]),
            minimum_absolute_increase=float(raw["minimum_absolute_increase"]),
            cooldown_buckets=int(raw["cooldown_buckets"]),
            meta_equals=raw.get("meta_equals") or {},
            version=version,
        )


def detect_bursts(
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
    start_utc = parse_timestamp(start) if start else min(event.ts for event in all_events)
    end_utc = parse_timestamp(end) if end else max(event.ts for event in all_events) + config.bucket_size
    if end_utc <= start_utc:
        raise ValueError("detection end must follow start")
    start_bucket = _floor_bucket(start_utc, config.bucket_minutes)
    end_bucket = _ceil_bucket(end_utc, config.bucket_minutes)

    entities = sorted({event.entity for event in all_events})
    matching: dict[str, dict[datetime, list[Event]]] = defaultdict(lambda: defaultdict(list))
    for event in all_events:
        if event.ts < start_utc or event.ts >= end_utc or not _matches(event, config):
            continue
        matching[event.entity][_floor_bucket(event.ts, config.bucket_minutes)].append(event)

    firings: list[Firing] = []
    for entity in entities:
        counts: list[int] = []
        cooldown_until: datetime | None = None
        bucket_start = start_bucket
        while bucket_start < end_bucket:
            bucket_events = sorted(matching[entity].get(bucket_start, []), key=lambda event: event.ts)
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
                            kind=config.kind,
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
    return event.kind == config.kind and all(
        event.meta.get(key) == value for key, value in config.meta_equals.items()
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
