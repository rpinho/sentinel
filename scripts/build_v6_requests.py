#!/usr/bin/env python3
"""Freeze rich pre-candidate v6 requests and scheduled-size batches."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json, write_gzip_json
from sentinel.stage2_v6 import (
    REQUEST_BUNDLE_SCHEMA_VERSION,
    automation_suspect,
    batch_schema,
    build_batches,
    build_candidate_request,
)
from sentinel.types import Event, parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v6/protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_EVENTS = Path("holdout/v4/events-test.json.gz")
DEFAULT_V4_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_OUTPUT = Path("holdout/v6/requests.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--v4-scorecard", type=Path, default=DEFAULT_V4_SCORECARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    dataset = read_gzip_json(args.dataset)
    event_document = read_gzip_json(args.events)
    scorecard = _read_object(args.v4_scorecard)
    _verify_inputs(protocol, dataset, event_document, scorecard)
    rows = {
        str(row["row_id"]): row
        for row in cast(Sequence[Mapping[str, Any]], dataset["rows"])
    }
    predictions = {
        str(row["row_id"]): row
        for row in cast(
            Sequence[Mapping[str, Any]],
            cast(Mapping[str, Any], scorecard["test"])["predictions"],
        )
    }
    events_by_repo: dict[str, list[Event]] = defaultdict(list)
    for raw in cast(Sequence[Mapping[str, Any]], event_document["events"]):
        event = Event.from_dict(raw)
        events_by_repo[event.entity].append(event)
    for events in events_by_repo.values():
        events.sort(key=lambda event: event.ts)

    sample_rows = cast(
        Sequence[Mapping[str, Any]],
        cast(Mapping[str, Any], protocol["sampling"])["rows"],
    )
    requests = []
    for sample in sample_rows:
        row_id = str(sample["row_id"])
        row = rows[row_id]
        request = build_candidate_request(
            row=row,
            prediction=predictions[row_id],
            events=events_by_repo[str(row["entity"])],
            protocol=protocol,
        )
        document = request.to_dict()
        document["automation_suspect"] = automation_suspect(document)
        _verify_request(document)
        requests.append(document)
    requests.sort(key=lambda request: str(request["row_id"]))
    batches = build_batches(requests, protocol=protocol)
    schema_hashes = cast(
        Mapping[str, str],
        cast(Mapping[str, Any], protocol["stage2"])[
            "structured_schema_sha256_by_candidate_count"
        ],
    )
    for batch in batches:
        count = int(batch["candidate_count"])
        identity = cast(Mapping[str, Any], batch["identity"])
        if canonical_sha256(cast(Mapping[str, Any], identity["json_schema"])) != schema_hashes[str(count)]:
            raise RuntimeError("v6 batch schema differs from frozen protocol")
    batch_chars = [
        len(str(cast(Mapping[str, Any], batch["identity"])["user_content"]))
        for batch in batches
    ]
    base = {
        "schema_version": REQUEST_BUNDLE_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "protocol_sha256": canonical_sha256(protocol),
        "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"],
        "v4_events_content_sha256": cast(Mapping[str, Any], event_document["hashes"])["events_sha256"],
        "candidate_count": len(requests),
        "invocation_count": len(batches),
        "batch_user_content_chars_total": sum(batch_chars),
        "batch_user_content_chars_max": max(batch_chars),
        "requests": requests,
        "batches": batches,
    }
    bundle = {**base, "request_bundle_sha256": canonical_sha256(base)}
    write_gzip_json(args.output, bundle)
    event_counts = [
        int(cast(Mapping[str, Any], request["evidence_manifest"])["supplied_event_count"])
        for request in requests
    ]
    print(
        "V6_REQUESTS "
        f"candidates={len(requests)} batches={len(batches)} "
        f"median_events={sorted(event_counts)[len(event_counts) // 2]} "
        f"batch_chars_max={max(batch_chars)} "
        f"request_bundle_sha256={bundle['request_bundle_sha256']}"
    )


def _verify_inputs(
    protocol: Mapping[str, Any],
    dataset: Mapping[str, Any],
    events: Mapping[str, Any],
    scorecard: Mapping[str, Any],
) -> None:
    identities = cast(Mapping[str, Any], protocol["frozen_identities"])
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != identities["v4_test_dataset_content_sha256"]:
        raise RuntimeError("v6 dataset identity mismatch")
    if scorecard.get("scorecard_sha256") != identities["v4_scorecard_sha256"]:
        raise RuntimeError("v6 v4 scorecard identity mismatch")
    if dataset.get("split") != "test" or events.get("split") != "test":
        raise RuntimeError("v6 requires frozen v4 test inputs")


def _verify_request(request: Mapping[str, Any]) -> None:
    payload = cast(
        Mapping[str, Any],
        json.loads(str(cast(Mapping[str, Any], request["identity"])["user_content"])),
    )
    candidate = cast(Mapping[str, Any], payload["candidate"])
    if "label" in candidate or "incident_ids" in candidate:
        raise RuntimeError("v6 request leaked evaluation labels")
    cutoff = parse_timestamp(str(candidate["candidate_ts"]))
    for raw in cast(Sequence[Mapping[str, Any]], payload["pre_candidate_events"]):
        if Event.from_dict(raw).ts >= cutoff:
            raise RuntimeError("v6 request leaked post-candidate evidence")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
