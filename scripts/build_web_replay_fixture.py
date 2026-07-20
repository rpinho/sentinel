#!/usr/bin/env python3
"""Build the deployable webpack replay input from the validated DuckDB cache."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sentinel.store import SentinelStore

START = datetime(2025, 3, 21, tzinfo=UTC)
END = datetime(2025, 4, 7, 23, tzinfo=UTC)
ENTITY = "webpack/webpack"
EXPECTED_EVENT_COUNT = 245
EXPECTED_ACK_COMMENT_ID = 2784517989
PUBLICATION_ARTIFACTS = {
    Path("holdout/scorecard.json"): Path("holdout-v1-scorecard.json"),
    Path("holdout/v2/scorecard.json"): Path("holdout-v2-scorecard.json"),
    Path(
        "tests/fixtures/triage/"
        "42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c.json"
    ): Path(
        "triage/42978ab2a88d53b077361e8749d54c3c5f63f3c54f9672be390c4308a10f3b4c.json"
    ),
}


def build(database: Path, output: Path) -> None:
    with SentinelStore(database) as store:
        events = store.list_events(entity=ENTITY, start=START, end=END)

    assert len(events) == EXPECTED_EVENT_COUNT
    assert any(
        event.meta.get("comment_id") == EXPECTED_ACK_COMMENT_ID for event in events
    )
    assert any(
        event.kind == "release" and event.meta.get("version") == "5.99.0"
        for event in events
    )

    rows = [event.to_dict() for event in events]
    canonical_events = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document = {
        "schema_version": 1,
        "source": {
            "entity": ENTITY,
            "start": START.isoformat().replace("+00:00", "Z"),
            "end": END.isoformat().replace("+00:00", "Z"),
            "event_count": len(rows),
            "normalized_events_sha256": hashlib.sha256(canonical_events).hexdigest(),
            "contract": (
                "Normalized GH Archive and corroborating npm source events only; "
                "no detector firing or triage conclusion is stored here."
            ),
        },
        "events": rows,
    }
    encoded = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))
    print(
        f"WROTE {output} events={len(rows)} "
        f"sha256={hashlib.sha256(encoded).hexdigest()}"
    )
    for source, relative_destination in PUBLICATION_ARTIFACTS.items():
        destination = output.parent / relative_destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        print(f"COPIED {source} -> {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(".cache/webpack-v2.duckdb"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/sentinel/web/data/webpack-v5.99.0.json.gz"),
    )
    args = parser.parse_args()
    build(args.db, args.output)


if __name__ == "__main__":
    main()
