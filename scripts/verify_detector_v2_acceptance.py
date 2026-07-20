#!/usr/bin/env python3
"""Assert detector-v2's raw webpack reproduction and healthy contrast."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.corroborate import corroborate_release
from sentinel.detect import DetectorConfig, evaluate_detection
from sentinel.store import SentinelStore
from sentinel.types import format_timestamp

HISTORY_START = datetime(2025, 3, 21, tzinfo=UTC)
HEALTHY_START = datetime(2025, 4, 4, 12, tzinfo=UTC)
HERO_START = datetime(2025, 4, 7, 12, tzinfo=UTC)
END = datetime(2025, 4, 7, 23, tzinfo=UTC)
ACK_TS = datetime(2025, 4, 7, 20, 14, 4, tzinfo=UTC)
FIRING_TS = datetime(2025, 4, 7, 18, 55, 2, tzinfo=UTC)


def verify(database: Path, config_path: Path) -> None:
    config = DetectorConfig.load(config_path)
    assert config.algorithm == "robust_rate_v2"
    with SentinelStore(database) as store:
        all_events = store.list_events(end=datetime(2025, 4, 9, tzinfo=UTC))
        repo_events = store.list_events(
            entity="webpack/webpack", start=HISTORY_START, end=END
        )
        coverage = store.list_ingestion_windows(
            entity="webpack/webpack",
            source="gharchive",
            start=HISTORY_START,
            end=END,
        )

    expected_hours = {
        HISTORY_START + timedelta(hours=offset)
        for offset in range(int((END - HISTORY_START).total_seconds() // 3600))
    }
    complete_hours = {
        window.window_start for window in coverage if window.status == "complete"
    }
    assert complete_hours == expected_hours

    healthy = evaluate_detection(
        repo_events,
        config,
        start=HEALTHY_START,
        end=HERO_START,
    )
    assert healthy.firings == ()

    hero = evaluate_detection(repo_events, config, start=HERO_START, end=END)
    assert len(hero.firings) == 1
    firing = hero.firings[0]
    assert firing.ts == FIRING_TS
    assert firing.ts < ACK_TS
    assert firing.metric_id == "comments_per_active_issue_thread"
    assert firing.observed == 5.5
    assert firing.baseline_mean == 1.0
    assert firing.score == 18.0
    assert firing.meta["candidate_metrics"] == [
        "issues_opened_per_active_participant",
        "comments_per_active_issue_thread",
    ]
    assert firing.meta["metric_evaluations"][
        "comments_per_active_issue_thread"
    ]["bot_excluded"]["baseline_samples"] == 5
    release = firing.meta["independent_corroboration"]["recent_release"]
    assert release["version"] == "5.99.0"
    assert release["sources"] == ["gharchive", "npm_registry"]
    assert firing.meta["severity"]["severity"] == "digest"
    assert firing.meta["severity"]["interrupt_policy_enabled"] is False

    automated = {
        actor
        for evaluation in hero.evaluations
        for actor in evaluation.meta["automated_actors"]
    }
    assert automated == {"dependabot[bot]"}

    issue = next(
        event
        for event in repo_events
        if event.kind == "issue"
        and event.meta.get("issue_number") == 19394
        and event.meta.get("action") == "opened"
    )
    assert issue.ts == datetime(2025, 4, 7, 18, 4, 49, tzinfo=UTC)
    assert issue.ts < firing.ts

    culprit = corroborate_release(
        all_events, repo="webpack/webpack", package="webpack", version="5.99.0"
    )
    assert culprit is not None
    assert culprit.github.ts == datetime(2025, 4, 7, 13, 12, 5, tzinfo=UTC)
    assert culprit.npm.ts.replace(microsecond=0) == datetime(
        2025, 4, 7, 13, 13, 8, tzinfo=UTC
    )
    assert culprit.difference_seconds <= 90

    print(f"PASS continuous archive hours: {len(complete_hours)} complete")
    print(f"PASS healthy contrast: firings=0 diagnostics={healthy.verdict_counts}")
    print(
        f"PASS detector v2 firing: {format_timestamp(firing.ts)} "
        f"metric={firing.metric_id} value={firing.observed} "
        f"median={firing.baseline_mean} robust_z={firing.score}"
    )
    print(
        "PASS cross-source release: "
        f"GH={format_timestamp(culprit.github.ts)} "
        f"npm={format_timestamp(culprit.npm.ts)} "
        f"agreement={culprit.difference_seconds:.0f}s"
    )
    print(f"PASS automation exclusions: {sorted(automated)}")
    print(
        f"PASS pre-ack lead: {(ACK_TS - firing.ts).total_seconds() / 60:.1f} minutes"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("src/sentinel/config/detector-v2.toml"),
    )
    args = parser.parse_args()
    verify(args.db, args.config)


if __name__ == "__main__":
    main()
