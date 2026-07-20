#!/usr/bin/env python3
"""Freeze the v3 source subset and build its detector-independent labels."""

from __future__ import annotations

import argparse
import gzip
import json
import struct
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.adapters.npm import NpmAdapter
from sentinel.detector_v3 import (
    apply_label_audit,
    build_labeled_dataset,
    canonical_sha256,
)
from sentinel.types import Event, format_timestamp, parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v3/study-protocol.json")
DEFAULT_EVENTS = Path("holdout/v3/events.json.gz")
DEFAULT_DATASET = Path("holdout/v3/dataset.json")
DEFAULT_POSITIVES = Path("holdout/v3/positives.json")
DEFAULT_AUDIT = Path("holdout/v3/label-audit.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--positives", type=Path, default=DEFAULT_POSITIVES)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--cache", type=Path, default=Path(".cache/gharchive"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--extract",
        action="store_true",
        help="scan frozen GH Archive files and refresh the committed source subset",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")

    protocol = _read_object(args.protocol)
    protocol_hash = canonical_sha256(protocol)
    if args.extract:
        source = extract_source_bundle(
            protocol=protocol,
            protocol_hash=protocol_hash,
            cache=args.cache,
            workers=args.workers,
        )
        _write_gzip_json(args.events, source)
    else:
        if not args.events.exists():
            parser.error("committed v3 events are absent; run once with --extract")
        source = _read_gzip_object(args.events)

    _validate_source(source, protocol_hash=protocol_hash)
    rows = cast(Sequence[Mapping[str, Any]], source["events"])
    events = [Event.from_dict(row) for row in rows]
    dataset = apply_label_audit(
        build_labeled_dataset(events, protocol), _read_object(args.audit)
    )
    dataset["source"] = {
        "events_path": str(args.events),
        "events_sha256": source["hashes"]["events_sha256"],
        "archive_manifest_sha256": source["hashes"][
            "archive_manifest_sha256"
        ],
        "npm_releases_sha256": source["hashes"]["npm_releases_sha256"],
        "archive_files": len(source["archive_files"]),
        "normalized_events": len(rows),
    }
    dataset["hashes"]["dataset_content_sha256"] = canonical_sha256(
        {
            "rows": dataset["rows"],
            "positive_incidents": dataset["positive_incidents"],
            "source": dataset["source"],
        }
    )
    positives = {
        "schema_version": dataset["schema_version"],
        "study_id": dataset["study_id"],
        "label_rule_version": dataset["label_rule_version"],
        "class_balance": dataset["class_balance"],
        "label_audit": dataset["label_audit"],
        "positive_incidents": dataset["positive_incidents"],
        "positive_incidents_sha256": dataset["hashes"][
            "positive_incidents_sha256"
        ],
    }
    _write_json(args.dataset, dataset)
    _write_json(args.positives, positives)

    balance = dataset["class_balance"]
    print(
        "V3_DATASET "
        f"candidates={balance['all']['candidate_hours']} "
        f"positives={balance['all']['positive_incidents']} "
        f"train_positives={balance['train']['positive_incidents']} "
        f"test_positives={balance['test']['positive_incidents']} "
        f"dataset_sha256={dataset['hashes']['dataset_content_sha256']}",
        flush=True,
    )
    stop = cast(Mapping[str, Any], protocol["stop_conditions"])
    failures: list[str] = []
    if balance["all"]["positive_incidents"] < int(
        stop["minimum_positive_incidents_total"]
    ):
        failures.append("total positive incidents below frozen minimum")
    per_split = int(stop["minimum_positive_incidents_per_split"])
    for split in ("train", "test"):
        if balance[split]["positive_incidents"] < per_split:
            failures.append(f"{split} positive incidents below frozen minimum")
    if failures:
        raise SystemExit("STOP: " + "; ".join(failures))


def extract_source_bundle(
    *,
    protocol: Mapping[str, Any],
    protocol_hash: str,
    cache: Path,
    workers: int,
) -> dict[str, Any]:
    repositories = sorted(
        repo
        for repos in cast(Mapping[str, Sequence[str]], protocol["repositories"]).values()
        for repo in repos
    )
    hours = sorted(
        {
            hour
            for window in cast(Sequence[Mapping[str, Any]], protocol["windows"])
            for hour in _iter_hours(
                parse_timestamp(str(window["archive_start"])),
                parse_timestamp(str(window["acknowledgment_end"])),
            )
        }
    )
    adapter = GHArchiveAdapter(cache_dir=cache)
    missing = [hour for hour in hours if not adapter.cache_path(hour).exists()]
    if missing:
        rendered = ", ".join(adapter.archive_name(hour) for hour in missing[:8])
        raise FileNotFoundError(
            f"{len(missing)} frozen archive hours are absent ({rendered}); "
            "download them before extraction"
        )

    rows: list[dict[str, Any]] = []
    archive_files: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _extract_hour,
                str(cache),
                format_timestamp(hour),
                tuple(repositories),
            ): hour
            for hour in hours
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            rows.extend(cast(list[dict[str, Any]], result["events"]))
            archive_files.append(cast(dict[str, Any], result["archive"]))
            if index == 1 or index % 24 == 0 or index == len(hours):
                print(
                    f"EXTRACT {index}/{len(hours)} normalized_events={len(rows)}",
                    flush=True,
                )

    npm_rows = _npm_releases(protocol)
    rows.extend(event.to_dict() for event in npm_rows)
    rows.sort(
        key=lambda row: (
            str(row["entity"]),
            str(row["ts"]),
            str(cast(Mapping[str, Any], row.get("meta") or {}).get("source_event_id") or ""),
            str(cast(Mapping[str, Any], row.get("meta") or {}).get("source") or ""),
        )
    )
    archive_files.sort(key=lambda row: str(row["name"]))
    npm_payload = [event.to_dict() for event in npm_rows]
    return {
        "schema_version": 1,
        "study_id": protocol["study_id"],
        "protocol_sha256": protocol_hash,
        "archive_files": archive_files,
        "npm_releases": npm_payload,
        "events": rows,
        "hashes": {
            "archive_manifest_sha256": canonical_sha256(archive_files),
            "npm_releases_sha256": canonical_sha256(npm_payload),
            "events_sha256": canonical_sha256(rows),
        },
    }


def _extract_hour(
    cache: str, hour_text: str, repositories: tuple[str, ...]
) -> dict[str, Any]:
    hour = parse_timestamp(hour_text)
    adapter = GHArchiveAdapter(cache_dir=cache)
    path = adapter.cache_path(hour)
    with path.open("rb") as source:
        source.seek(-8, 2)
        crc32, uncompressed_size_mod32 = struct.unpack("<II", source.read(8))
    events = [
        event.to_dict() for event in adapter.iter_events(hour, entities=repositories)
    ]
    return {
        "archive": {
            "name": path.name,
            "url": adapter.archive_url(hour),
            "bytes": path.stat().st_size,
            "gzip_crc32": f"{crc32:08x}",
            "uncompressed_size_mod32": uncompressed_size_mod32,
        },
        "events": events,
    }


def _npm_releases(protocol: Mapping[str, Any]) -> list[Event]:
    feature_policy = cast(Mapping[str, Any], protocol["feature_policy"])
    packages = cast(Mapping[str, str], feature_policy["npm_packages"])
    windows = cast(Sequence[Mapping[str, Any]], protocol["windows"])
    result: list[Event] = []
    adapter = NpmAdapter()
    for repository, package in sorted(packages.items()):
        package_events: dict[tuple[str, str], Event] = {}
        for window in windows:
            start = parse_timestamp(str(window["archive_start"]))
            end = parse_timestamp(str(window["acknowledgment_end"]))
            for event in adapter.iter_releases(package, start=start, end=end):
                attached = Event(
                    ts=event.ts,
                    entity=repository,
                    kind=event.kind,
                    text=event.text,
                    value=event.value,
                    meta={
                        **event.meta,
                        "corroborates_package_entity": package,
                    },
                )
                package_events[(format_timestamp(attached.ts), str(attached.meta["version"]))] = attached
        rows = sorted(package_events.values(), key=lambda event: event.ts)
        result.extend(rows)
        print(
            f"NPM repository={repository} package={package} releases={len(rows)}",
            flush=True,
        )
    return sorted(result, key=lambda event: (event.entity, event.ts))


def _validate_source(source: Mapping[str, Any], *, protocol_hash: str) -> None:
    if source.get("schema_version") != 1:
        raise ValueError("unsupported v3 source schema")
    if source.get("protocol_sha256") != protocol_hash:
        raise ValueError("v3 source bundle does not match the frozen protocol")
    rows = source.get("events")
    if not isinstance(rows, list):
        raise ValueError("v3 source bundle has no event list")
    hashes = cast(Mapping[str, Any], source.get("hashes") or {})
    if canonical_sha256(rows) != hashes.get("events_sha256"):
        raise ValueError("v3 source event hash mismatch")
    archive_files = source.get("archive_files")
    if not isinstance(archive_files, list) or canonical_sha256(
        archive_files
    ) != hashes.get("archive_manifest_sha256"):
        raise ValueError("v3 archive manifest hash mismatch")
    npm = source.get("npm_releases")
    if not isinstance(npm, list) or canonical_sha256(npm) != hashes.get(
        "npm_releases_sha256"
    ):
        raise ValueError("v3 npm release hash mismatch")


def _iter_hours(start: datetime, end: datetime) -> Iterable[datetime]:
    current = start
    while current < end:
        yield current
        current += timedelta(hours=1)


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


def _read_gzip_object(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_gzip_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(gzip.compress(encoded, compresslevel=9, mtime=0))


if __name__ == "__main__":
    main()
