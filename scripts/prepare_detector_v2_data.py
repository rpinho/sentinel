#!/usr/bin/env python3
"""Backfill the disclosed phase history needed by detector-v2 reproductions."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.adapters.npm import NpmAdapter
from sentinel.store import IngestionWindow, SentinelStore
from sentinel.types import Event, format_timestamp

HOLDOUT_ENTITIES = ("facebook/react", "vercel/next.js", "microsoft/vscode")
HOLDOUT_START = datetime(2025, 4, 28, tzinfo=UTC)
HOLDOUT_END = datetime(2025, 5, 20, tzinfo=UTC)
# Seventeen continuous days covers the two-week phase pool for the Apr 4-7
# healthy contrast and incident windows. Continuity is required even though
# baselines are hour-matched: the first-observed-participant metric must see
# actors who appeared in any intervening hour.
WEBPACK_HISTORY_START = datetime(2025, 3, 21, tzinfo=UTC)
WEBPACK_DETECTION_START = datetime(2025, 4, 7, 12, tzinfo=UTC)
WEBPACK_END = datetime(2025, 4, 7, 23, tzinfo=UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("holdout", "webpack", "all"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/gharchive"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    if args.target in {"holdout", "all"}:
        prepare(
            hours=tuple(iter_hours(HOLDOUT_START, HOLDOUT_END)),
            entities=HOLDOUT_ENTITIES,
            database=Path(".cache/holdout-v2.duckdb"),
            cache=args.cache,
            workers=args.workers,
            target="holdout-v2",
        )
    if args.target in {"webpack", "all"}:
        prepare(
            hours=tuple(iter_hours(WEBPACK_HISTORY_START, WEBPACK_END)),
            entities=("webpack/webpack",),
            database=Path(".cache/webpack-v2.duckdb"),
            cache=args.cache,
            workers=args.workers,
            target="webpack-v2",
        )
        prepare_npm_corroboration(
            database=Path(".cache/webpack-v2.duckdb"),
            package="webpack",
            repository="webpack/webpack",
        )


def prepare(
    *,
    hours: tuple[datetime, ...],
    entities: tuple[str, ...],
    database: Path,
    cache: Path,
    workers: int,
    target: str,
) -> None:
    adapter = GHArchiveAdapter(cache_dir=cache)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(adapter.download_hour, hour): hour for hour in hours}
        for index, future in enumerate(as_completed(futures), start=1):
            path = future.result()
            print(
                f"DOWNLOAD target={target} {index}/{len(hours)} "
                f"archive={path.name} bytes={path.stat().st_size}",
                flush=True,
            )

    with SentinelStore(database) as store:
        for index, hour in enumerate(hours, start=1):
            existing = {
                window.entity
                for window in store.list_ingestion_windows(
                    source="gharchive", start=hour, end=hour + timedelta(hours=1)
                )
                if window.status == "complete"
            }
            if set(entities) <= existing:
                continue
            rows = list(adapter.iter_events(hour, entities=entities))
            source_bytes = adapter.cache_path(hour).stat().st_size
            for entity in entities:
                entity_rows = [row for row in rows if row.entity == entity]
                store.insert_events(entity_rows)
                store.record_ingestion_window(
                    IngestionWindow(
                        source="gharchive",
                        entity=entity,
                        window_start=hour,
                        window_end=hour + timedelta(hours=1),
                        status="complete",
                        normalized_events=len(entity_rows),
                        source_bytes=source_bytes,
                        meta={
                            "archive": adapter.archive_name(hour),
                            "url": adapter.archive_url(hour),
                            "detector_v2_phase_history": hour
                            < (
                                datetime(2025, 5, 12, tzinfo=UTC)
                                if target == "holdout-v2"
                                else WEBPACK_DETECTION_START
                            ),
                        },
                    )
                )
            print(
                f"INGEST target={target} {index}/{len(hours)} "
                f"hour={format_timestamp(hour)} events={len(rows)}",
                flush=True,
            )


def iter_hours(start: datetime, end: datetime) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(hours=1)


def prepare_npm_corroboration(
    *, database: Path, package: str, repository: str
) -> None:
    start = datetime(2025, 4, 7, tzinfo=UTC)
    end = datetime(2025, 4, 9, tzinfo=UTC)
    releases = list(NpmAdapter().iter_releases(package, start=start, end=end))
    attached = [
        Event(
            ts=event.ts,
            entity=repository,
            kind=event.kind,
            text=event.text,
            value=event.value,
            meta={**event.meta, "corroborates_package_entity": package},
        )
        for event in releases
    ]
    with SentinelStore(database) as store:
        inserted = store.insert_events([*releases, *attached])
        store.record_ingestion_window(
            IngestionWindow(
                source="npm_registry",
                entity=repository,
                window_start=start,
                window_end=end,
                status="complete",
                normalized_events=len(attached),
                meta={"package": package, "corroboration_attachment": True},
            )
        )
    print(
        f"INGEST target=webpack-v2 npm_releases={len(releases)} "
        f"attached={len(attached)} inserted={inserted}",
        flush=True,
    )


if __name__ == "__main__":
    main()
