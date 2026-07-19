"""Assert Sentinel's public webpack replay against independently checkable facts."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sentinel.corroborate import corroborate_release
from sentinel.detect import DetectorConfig, detect_bursts
from sentinel.store import SentinelStore
from sentinel.types import format_timestamp

START = datetime(2025, 4, 7, 12, tzinfo=UTC)
END = datetime(2025, 4, 7, 23, tzinfo=UTC)
ISSUE_TS = datetime(2025, 4, 7, 18, 4, 49, tzinfo=UTC)
ACK_TS = datetime(2025, 4, 7, 20, 14, 4, tzinfo=UTC)


def verify(database: Path) -> None:
    config = DetectorConfig.load()
    with SentinelStore(database) as store:
        # Daily npm metrics are timestamped at midnight, before the hourly
        # archive replay begins at noon, so load the whole bounded database.
        events = store.list_events(end=datetime(2025, 4, 9, tzinfo=UTC))
        coverage = store.list_ingestion_windows(
            entity="webpack/webpack", source="gharchive", start=START, end=END
        )

    expected_hours = {START + timedelta(hours=offset) for offset in range(11)}
    complete_hours = {
        window.window_start for window in coverage if window.status == "complete"
    }
    assert complete_hours == expected_hours, (
        f"archive-hour coverage mismatch: expected {len(expected_hours)}, "
        f"found {len(complete_hours)}"
    )

    issue = next(
        event
        for event in events
        if event.kind == "issue"
        and event.meta.get("issue_number") == 19394
        and event.meta.get("action") == "opened"
    )
    assert issue.ts == ISSUE_TS
    assert issue.meta.get("url") == "https://github.com/webpack/webpack/issues/19394"

    acknowledgment = next(
        event for event in events if event.meta.get("comment_id") == 2784517989
    )
    assert acknowledgment.ts == ACK_TS
    assert acknowledgment.meta.get("author_association") == "MEMBER"
    assert "Found buggy places" in (acknowledgment.text or "")
    assert "19398" in (acknowledgment.text or "")

    culprit = corroborate_release(
        events, repo="webpack/webpack", package="webpack", version="5.99.0"
    )
    patch = corroborate_release(
        events, repo="webpack/webpack", package="webpack", version="5.99.1"
    )
    assert culprit is not None
    assert patch is not None
    assert culprit.github.ts == datetime(2025, 4, 7, 13, 12, 5, tzinfo=UTC)
    assert culprit.npm.ts.replace(microsecond=0) == datetime(
        2025, 4, 7, 13, 13, 8, tzinfo=UTC
    )
    assert culprit.difference_seconds <= 90
    assert patch.github.ts == datetime(2025, 4, 7, 22, 35, 51, tzinfo=UTC)
    assert patch.npm.ts.replace(microsecond=0) == datetime(
        2025, 4, 7, 22, 36, 24, tzinfo=UTC
    )
    assert patch.difference_seconds <= 90

    downloads = {
        event.ts.date(): int(event.value or 0)
        for event in events
        if event.kind == "metric"
        and event.meta.get("source") == "npm_downloads"
        and event.entity == "webpack"
    }
    assert downloads[datetime(2025, 4, 7, tzinfo=UTC).date()] == 5_356_274
    assert downloads[datetime(2025, 4, 8, tzinfo=UTC).date()] == 5_878_921

    repo_events = [event for event in events if event.entity == "webpack/webpack"]
    firings = detect_bursts(repo_events, config, start=START, end=END)
    assert len(firings) == 1
    firing = firings[0]
    assert firing.metric_id == "issue_activity_per_hour"
    assert firing.ts == datetime(2025, 4, 7, 18, 27, 36, tzinfo=UTC)
    assert firing.ts < acknowledgment.ts

    print(f"PASS archive hours: {len(complete_hours)}/{len(expected_hours)} complete")
    print(f"PASS issue #19394 observed: {format_timestamp(issue.ts)}")
    print(
        f"PASS detector fired: {format_timestamp(firing.ts)} "
        f"({int(firing.observed)} events at crossing; {firing.meta['bucket_total']} in hour)"
    )
    print(
        f"PASS substantive acknowledgment: {format_timestamp(acknowledgment.ts)} "
        f"({(acknowledgment.ts - firing.ts).total_seconds() / 60:.1f}m after firing)"
    )
    print(
        f"PASS v5.99.0 corroboration: GH {format_timestamp(culprit.github.ts)} / "
        f"npm {format_timestamp(culprit.npm.ts)} / {culprit.difference_seconds:.0f}s apart"
    )
    print(
        f"PASS v5.99.1 corroboration: GH {format_timestamp(patch.github.ts)} / "
        f"npm {format_timestamp(patch.npm.ts)} / {patch.difference_seconds:.0f}s apart"
    )
    print(
        "PASS npm severity: "
        f"{downloads[datetime(2025, 4, 7, tzinfo=UTC).date()]:,} and "
        f"{downloads[datetime(2025, 4, 8, tzinfo=UTC).date()]:,} downloads/day"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.db)


if __name__ == "__main__":
    main()
