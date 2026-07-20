#!/usr/bin/env python3
"""Backfill the disclosed phase history needed by detector-v2 reproductions."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.store import IngestionWindow, SentinelStore
from sentinel.types import format_timestamp

HOLDOUT_ENTITIES = ("facebook/react", "vercel/next.js", "microsoft/vscode")
HOLDOUT_START = datetime(2025, 4, 28, tzinfo=UTC)
HOLDOUT_END = datetime(2025, 5, 20, tzinfo=UTC)
WEBPACK_HISTORY_START = datetime(2025, 3, 24, tzinfo=UTC)
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
        history_hours = tuple(
            hour
            for hour in iter_hours(WEBPACK_HISTORY_START, WEBPACK_DETECTION_START)
            if 12 <= hour.hour < 23
        )
        detection_hours = tuple(iter_hours(WEBPACK_DETECTION_START, WEBPACK_END))
        prepare(
            hours=history_hours + detection_hours,
            entities=("webpack/webpack",),
            database=Path(".cache/webpack-v2.duckdb"),
            cache=args.cache,
            workers=args.workers,
            target="webpack-v2",
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


if __name__ == "__main__":
    main()
