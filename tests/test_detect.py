from datetime import UTC, datetime, timedelta

from sentinel.detect import DetectorConfig, detect_bursts
from sentinel.types import Event

START = datetime(2025, 4, 7, 12, tzinfo=UTC)


def _config() -> DetectorConfig:
    return DetectorConfig(
        metric_id="issue_activity_per_hour",
        kinds=("issue", "comment"),
        bucket_minutes=60,
        baseline_buckets=4,
        minimum_baseline_buckets=4,
        z_score_threshold=4,
        standard_deviation_floor=0.5,
        volume_floor=3,
        minimum_absolute_increase=2,
        cooldown_buckets=2,
        meta_in={"action": ("opened", "created")},
        version="test:v1",
    )


def test_detector_fires_at_threshold_crossing_against_prior_buckets() -> None:
    events = [Event(START, "webpack/webpack", "release", meta={"version": "v5.99.0"})]
    events.extend(
        Event(
            START + timedelta(hours=5, minutes=minute),
            "webpack/webpack",
            "issue",
            meta={"action": "opened"},
        )
        for minute in (4, 8, 12, 20, 30)
    )

    firings = detect_bursts(
        events, _config(), start=START, end=START + timedelta(hours=7)
    )

    assert len(firings) == 1
    assert firings[0].ts == START + timedelta(hours=5, minutes=12)
    assert firings[0].observed == 3
    assert firings[0].meta["bucket_total"] == 5
    assert firings[0].baseline_mean == 0
    assert firings[0].detector_version == "test:v1"


def test_detector_ignores_closed_issues_and_text_free_metrics() -> None:
    events = [Event(START, "webpack/webpack", "release")]
    events.extend(
        Event(
            START + timedelta(hours=5, minutes=minute),
            "webpack/webpack",
            kind,
            value=5_500_000 if kind == "metric" else None,
            meta={"action": action},
        )
        for minute, kind, action in (
            (1, "issue", "closed"),
            (2, "comment", "created"),
            (3, "metric", None),
        )
    )

    assert (
        detect_bursts(events, _config(), start=START, end=START + timedelta(hours=7))
        == []
    )


def test_default_config_is_versioned() -> None:
    config = DetectorConfig.load()

    assert config.metric_id == "issue_activity_per_hour"
    assert config.version.startswith("sha256:")
