from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sentinel.detect import (
    DetectorConfig,
    RateMetricConfig,
    evaluate_detection,
)
from sentinel.types import Event

START = datetime(2025, 4, 7, 12, tzinfo=UTC)


def _metric() -> RateMetricConfig:
    return RateMetricConfig(
        metric_id="comments_per_active_issue_thread",
        source="comments_per_active_issue_thread",
        direction="spike",
        minimum_exposure=1,
        absolute_rate_floor=2,
        minimum_absolute_change=1,
        robust_z_threshold=3,
        dispersion_floor_absolute=0.25,
        dispersion_floor_fraction=0.1,
    )


def _config(*, history_days: int = 14) -> DetectorConfig:
    return DetectorConfig(
        algorithm="robust_rate_v2",
        metric_id="sentinel_v2",
        kinds=("issue", "comment"),
        bucket_minutes=60,
        cooldown_buckets=0,
        meta_in={"action": ("opened", "created")},
        version="test:v2",
        phase_history_days=history_days,
        minimum_phase_samples=4,
        mild_z_threshold=2,
        persistence_buckets=2,
        recent_release_hours=6,
        bot_min_signals=2,
        bot_name_patterns=(r"\[bot\]$", r"-bot$"),
        bot_concentration_share=0.8,
        bot_concentration_min_events=3,
        bot_repeated_payload_min_events=2,
        bot_regular_timing_min_events=3,
        bot_regular_gap_mad_seconds=0,
        severity_digest_threshold=0.1,
        severity_interrupt_threshold=0.8,
        metrics=(_metric(),),
    )


def _comment(
    ts: datetime,
    *,
    issue: int,
    actor: str,
    text: str = "human reply",
    actor_type: str = "User",
) -> Event:
    return Event(
        ts=ts,
        entity="webpack/webpack",
        kind="comment",
        text=text,
        meta={
            "action": "created",
            "issue_number": issue,
            "actor": actor,
            "author": actor,
            "actor_type": actor_type,
            "author_type": actor_type,
            "url": f"https://github.com/webpack/webpack/issues/{issue}#comment",
        },
    )


def _healthy_history(*, hours: tuple[int, ...] = (12, 13)) -> list[Event]:
    events: list[Event] = []
    for days_ago in range(14, 0, -1):
        day = START - timedelta(days=days_ago)
        if day.weekday() >= 5:
            continue
        for hour in hours:
            events.append(
                _comment(
                    day.replace(hour=hour, minute=10),
                    issue=days_ago * 100 + hour,
                    actor=f"human-{days_ago}-{hour}",
                )
            )
    return events


def _burst(hour: int, *, actor_prefix: str = "human") -> list[Event]:
    return [
        _comment(
            START.replace(hour=hour, minute=minute),
            issue=9000 + hour,
            actor=f"{actor_prefix}-{index}",
        )
        for index, minute in enumerate((2, 8, 14, 20, 26), start=1)
    ]


def test_v2_requires_persistence_and_uses_phase_matched_robust_baseline() -> None:
    events = [*_healthy_history(), *_burst(12), *_burst(13)]

    run = evaluate_detection(
        events,
        _config(),
        start=START,
        end=START + timedelta(hours=2),
    )

    assert len(run.firings) == 1
    firing = run.firings[0]
    assert firing.window_start == START + timedelta(hours=1)
    assert firing.meta["persistent_metrics"] == [
        "comments_per_active_issue_thread"
    ]
    assert firing.baseline_mean == 1
    assert firing.detector_version == "test:v2"
    assert firing.meta["severity"]["breadth"] == 1
    assert run.verdict_counts["candidate"] == 1
    assert run.verdict_counts["firing"] == 1


def test_v2_release_corroboration_admits_one_interval_but_caps_severity() -> None:
    events = [
        *_healthy_history(hours=(12,)),
        Event(
            START - timedelta(hours=5),
            "webpack/webpack",
            "release",
            text="v5.99.0",
            meta={"version": "v5.99.0"},
        ),
        *_burst(12),
    ]

    run = evaluate_detection(
        events,
        _config(),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert len(run.firings) == 1
    firing = run.firings[0]
    assert firing.meta["independent_corroboration"]["recent_release"]["meta"][
        "version"
    ] == "v5.99.0"
    assert firing.meta["severity"]["interrupt_eligible"] is False
    assert firing.meta["severity"]["severity"] != "interrupt"


def test_v2_same_source_metric_breadth_does_not_bypass_persistence() -> None:
    second_metric = replace(_metric(), metric_id="comment_rate_echo")
    config = replace(_config(), metrics=(_metric(), second_metric))
    events = [*_healthy_history(hours=(12,)), *_burst(12)]

    run = evaluate_detection(
        events,
        config,
        start=START,
        end=START + timedelta(hours=1),
    )

    assert run.firings == ()
    assert run.verdict_counts == {"candidate": 2}


def test_v2_suppresses_raw_move_when_concordant_bot_signals_flatten_exclusion() -> None:
    bot_burst = [
        _comment(
            START.replace(minute=minute),
            issue=9999,
            actor="ci[bot]",
            text="Generated build statistics",
            actor_type="Bot",
        )
        for minute in (2, 4, 6, 8, 10)
    ]
    events = [*_healthy_history(hours=(12,)), *bot_burst]

    run = evaluate_detection(
        events,
        _config(),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert run.firings == ()
    evaluation = run.evaluations[0]
    assert evaluation.verdict == "automation_suppressed"
    assert evaluation.raw["flagged"] is True
    assert evaluation.bot_excluded["state"] == "insufficient_exposure"
    assert set(evaluation.meta["automated_actors"]["ci[bot]"]) >= {
        "account_type",
        "name_pattern",
    }


def test_v2_emits_immature_instead_of_alerting_without_phase_pool() -> None:
    events = [
        _comment(
            START - timedelta(days=1),
            issue=1,
            actor="history",
        ),
        *_burst(12),
    ]

    run = evaluate_detection(
        events,
        _config(history_days=2),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert run.firings == ()
    assert run.evaluations[0].verdict == "immature"
    assert run.evaluations[0].raw["baseline_samples"] == 0


def test_v2_distinguishes_insufficient_exposure_from_quiet_absolute_floor() -> None:
    history = _healthy_history(hours=(12,))
    insufficient = evaluate_detection(
        history,
        _config(),
        start=START,
        end=START + timedelta(hours=1),
    )
    below_floor_events = [
        _comment(START.replace(minute=2), issue=1, actor="one"),
        _comment(START.replace(minute=4), issue=1, actor="two"),
        _comment(START.replace(minute=6), issue=2, actor="three"),
    ]
    below_floor = evaluate_detection(
        [*history, *below_floor_events],
        _config(),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert insufficient.evaluations[0].verdict == "insufficient_exposure"
    assert below_floor.evaluations[0].verdict == "quiet"
    assert below_floor.evaluations[0].raw["value"] == 1.5
    assert below_floor.evaluations[0].raw["robust_z"] is None


def test_v2_never_classifies_automation_from_one_weak_signal() -> None:
    events = [*_healthy_history(hours=(12,)), *_burst(12)]
    first = events[-5]
    first_meta = dict(first.meta)
    first_meta["actor"] = "helper-bot"
    first_meta["author"] = "helper-bot"
    events[-5] = Event(
        ts=first.ts,
        entity=first.entity,
        kind=first.kind,
        text=first.text,
        meta=first_meta,
    )

    run = evaluate_detection(
        events,
        _config(),
        start=START,
        end=START + timedelta(hours=1),
    )

    assert run.evaluations[0].verdict == "candidate"
    assert "helper-bot" not in run.evaluations[0].meta["automated_actors"]


def test_first_time_share_counts_events_not_people() -> None:
    metric = RateMetricConfig(
        metric_id="first_time_participant_event_share",
        source="first_time_participant_event_share",
        direction="spike",
        minimum_exposure=1,
        absolute_rate_floor=0,
        minimum_absolute_change=0,
        robust_z_threshold=3,
        dispersion_floor_absolute=0.1,
        dispersion_floor_fraction=0.1,
    )
    history = _healthy_history(hours=(12,))
    returning_actor = history[0].meta["actor"]
    target = [
        _comment(START.replace(minute=2), issue=1, actor=str(returning_actor)),
        _comment(START.replace(minute=4), issue=1, actor="newcomer"),
        _comment(START.replace(minute=6), issue=1, actor="newcomer"),
        _comment(START.replace(minute=8), issue=2, actor="newcomer"),
    ]
    config = replace(_config(), metrics=(metric,))

    run = evaluate_detection(
        [*history, *target],
        config,
        start=START,
        end=START + timedelta(hours=1),
    )

    assert run.evaluations[0].raw["numerator"] == 3
    assert run.evaluations[0].raw["denominator"] == 4
    assert run.evaluations[0].raw["value"] == 0.75
