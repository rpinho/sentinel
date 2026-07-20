#!/usr/bin/env python3
"""Extract one frozen v4 split and build its active-hour labeled dataset."""

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
from sentinel.detector_v3 import canonical_sha256
from sentinel.detector_v4 import build_audit_candidates, build_labeled_dataset
from sentinel.types import Event, format_timestamp, parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v4/study-protocol.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train", "test"), required=True)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--audit-candidates", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".cache/gharchive"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--extract",
        action="store_true",
        help="scan the frozen local GH Archive cache for this split",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    events_path = args.events or Path(f"holdout/v4/events-{args.split}.json.gz")
    dataset_path = args.dataset or Path(
        f"holdout/v4/dataset-{args.split}.json.gz"
    )
    audit_path = args.audit_candidates or Path(
        f".cache/v4-audit-candidates-{args.split}.json"
    )

    protocol = _read_object(args.protocol)
    protocol_hash = canonical_sha256(protocol)
    if args.extract:
        source = extract_source_bundle(
            protocol=protocol,
            protocol_hash=protocol_hash,
            split=args.split,
            cache=args.cache,
            workers=args.workers,
        )
        _write_gzip_json(events_path, source)
    else:
        if not events_path.exists():
            parser.error(f"{events_path} is absent; use --extract")
        source = _read_gzip_object(events_path)
    _validate_source(source, protocol_hash=protocol_hash, split=args.split)

    event_rows = cast(Sequence[Mapping[str, Any]], source["events"])
    dataset = build_labeled_dataset(
        [Event.from_dict(row) for row in event_rows],
        protocol,
        split=args.split,
    )
    dataset["source"] = {
        "events_path": str(events_path),
        "events_sha256": source["hashes"]["events_sha256"],
        "archive_manifest_sha256": source["hashes"][
            "archive_manifest_sha256"
        ],
        "archive_files": len(source["archive_files"]),
        "normalized_events": len(event_rows),
    }
    dataset["hashes"]["dataset_content_sha256"] = canonical_sha256(
        {
            "split": dataset["split"],
            "rows": dataset["rows"],
            "positive_incidents": dataset["positive_incidents"],
            "source": dataset["source"],
        }
    )
    _write_gzip_json(dataset_path, dataset)
    _write_json(audit_path, build_audit_candidates(dataset, protocol))

    balance = dataset["class_balance"]
    attribution = dataset["source_attribution"]
    print(
        "V4_DATASET "
        f"split={args.split} "
        f"candidates={balance['candidate_hours']} "
        f"positive_hours={balance['positive_hours']} "
        f"positive_incidents={balance['positive_incidents']} "
        f"outside_old_spike={attribution['outside_old_spike_population']} "
        f"dataset_sha256={dataset['hashes']['dataset_content_sha256']}",
        flush=True,
    )


def extract_source_bundle(
    *,
    protocol: Mapping[str, Any],
    protocol_hash: str,
    split: str,
    cache: Path,
    workers: int,
) -> dict[str, Any]:
    repositories = tuple(
        sorted(
            cast(Mapping[str, Sequence[str]], protocol["repositories"])[split]
        )
    )
    hours = sorted(
        {
            hour
            for window in cast(Sequence[Mapping[str, Any]], protocol["windows"])
            for hour in _iter_hours(
                parse_timestamp(str(window["archive_start"])),
                parse_timestamp(str(window["confirmation_end"])),
            )
        }
    )
    adapter = GHArchiveAdapter(cache_dir=cache)
    missing = [hour for hour in hours if not adapter.cache_path(hour).exists()]
    if missing:
        rendered = ", ".join(adapter.archive_name(hour) for hour in missing[:8])
        raise FileNotFoundError(
            f"{len(missing)} frozen archive hours are absent ({rendered}); "
            "v4 extraction never downloads implicitly"
        )

    rows: list[dict[str, Any]] = []
    archive_files: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _extract_hour,
                str(cache),
                format_timestamp(hour),
                repositories,
            ): hour
            for hour in hours
        }
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            rows.extend(cast(list[dict[str, Any]], result["events"]))
            archive_files.append(cast(dict[str, Any], result["archive"]))
            if index == 1 or index % 24 == 0 or index == len(hours):
                print(
                    f"EXTRACT split={split} {index}/{len(hours)} "
                    f"normalized_events={len(rows)}",
                    flush=True,
                )
    rows.sort(
        key=lambda row: (
            str(row["entity"]),
            str(row["ts"]),
            str(cast(Mapping[str, Any], row.get("meta") or {}).get("source_event_id") or ""),
        )
    )
    archive_files.sort(key=lambda row: str(row["name"]))
    return {
        "schema_version": 1,
        "study_id": protocol["study_id"],
        "split": split,
        "protocol_sha256": protocol_hash,
        "repositories": list(repositories),
        "archive_files": archive_files,
        "events": rows,
        "hashes": {
            "archive_manifest_sha256": canonical_sha256(archive_files),
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
        event.to_dict()
        for event in adapter.iter_events(hour, entities=repositories)
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


def _validate_source(
    source: Mapping[str, Any], *, protocol_hash: str, split: str
) -> None:
    if source.get("schema_version") != 1:
        raise ValueError("unsupported v4 source schema")
    if source.get("protocol_sha256") != protocol_hash:
        raise ValueError("v4 source bundle does not match the frozen protocol")
    if source.get("split") != split:
        raise ValueError("v4 source bundle split mismatch")
    rows = source.get("events")
    archive_files = source.get("archive_files")
    hashes = cast(Mapping[str, Any], source.get("hashes") or {})
    if not isinstance(rows, list) or canonical_sha256(rows) != hashes.get(
        "events_sha256"
    ):
        raise ValueError("v4 source event hash mismatch")
    if not isinstance(archive_files, list) or canonical_sha256(
        archive_files
    ) != hashes.get("archive_manifest_sha256"):
        raise ValueError("v4 archive manifest hash mismatch")


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
