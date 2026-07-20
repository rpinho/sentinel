#!/usr/bin/env python3
"""Freeze every exact pre-candidate v5 request before executing Claude."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import (
    REQUEST_BUNDLE_SCHEMA_VERSION,
    build_batch_request,
    build_stage2_request,
    read_gzip_json,
    write_gzip_json,
)
from sentinel.types import Event, parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v5/protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_EVENTS = Path("holdout/v4/events-test.json.gz")
DEFAULT_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_OUTPUT = Path("holdout/v5/requests.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    dataset = read_gzip_json(args.dataset)
    event_document = read_gzip_json(args.events)
    scorecard = _read_object(args.scorecard)
    _verify_inputs(protocol, dataset, event_document, scorecard)
    rows = {str(row["row_id"]): row for row in cast(Sequence[Mapping[str, Any]], dataset["rows"])}
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
    total_user_chars = 0
    for sample in sample_rows:
        row_id = str(sample["row_id"])
        row = rows[row_id]
        request = build_stage2_request(
            row=row,
            prediction=predictions[row_id],
            events=events_by_repo[str(row["entity"])],
            protocol=protocol,
        )
        document = request.to_dict()
        _verify_request_isolation(document)
        total_user_chars += len(
            str(cast(Mapping[str, Any], document["identity"])["user_content"])
        )
        requests.append(document)
    requests.sort(key=lambda row: str(row["row_id"]))
    batch_request = build_batch_request(requests, protocol=protocol)
    batch_identity = cast(Mapping[str, Any], batch_request["identity"])
    if canonical_sha256(
        cast(Mapping[str, Any], batch_identity["json_schema"])
    ) != cast(Mapping[str, Any], protocol["stage2"])["structured_schema_sha256"]:
        raise RuntimeError("v5 batch schema differs from frozen protocol")
    batch_user_chars = len(
        str(batch_identity["user_content"])
    )
    base = {
        "schema_version": REQUEST_BUNDLE_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "protocol_sha256": canonical_sha256(protocol),
        "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])[
            "dataset_content_sha256"
        ],
        "v4_events_content_sha256": cast(Mapping[str, Any], event_document["hashes"])[
            "events_sha256"
        ],
        "request_count": len(requests),
        "invocation_count": 1,
        "total_user_content_chars": total_user_chars,
        "batch_user_content_chars": batch_user_chars,
        "batch_request": batch_request,
        "requests": requests,
    }
    bundle = {**base, "request_bundle_sha256": canonical_sha256(base)}
    write_gzip_json(args.output, bundle)
    print(
        "V5_REQUESTS "
        f"candidates={len(requests)} invocations=1 "
        f"batch_chars={batch_user_chars} "
        f"approx_batch_tokens={round(batch_user_chars / 4)} "
        f"request_bundle_sha256={bundle['request_bundle_sha256']}"
    )


def _verify_inputs(
    protocol: Mapping[str, Any],
    dataset: Mapping[str, Any],
    events: Mapping[str, Any],
    scorecard: Mapping[str, Any],
) -> None:
    identities = cast(Mapping[str, Any], protocol["v4_frozen_identities"])
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != identities[
        "test_dataset_content_sha256"
    ]:
        raise RuntimeError("v5 request dataset identity mismatch")
    if scorecard.get("scorecard_sha256") != identities["v4_scorecard_sha256"]:
        raise RuntimeError("v5 request scorecard identity mismatch")
    if events.get("split") != "test" or dataset.get("split") != "test":
        raise RuntimeError("v5 requests must use v4 test inputs")


def _verify_request_isolation(request: Mapping[str, Any]) -> None:
    identity = cast(Mapping[str, Any], request["identity"])
    payload = json.loads(str(identity["user_content"]))
    candidate = cast(Mapping[str, Any], payload["candidate"])
    if "label" in candidate or "incident_ids" in candidate:
        raise RuntimeError("v5 request leaked an evaluation label")
    cutoff = parse_timestamp(str(candidate["candidate_ts"]))
    for raw in cast(Sequence[Mapping[str, Any]], payload["pre_candidate_events"]):
        if Event.from_dict(raw).ts >= cutoff:
            raise RuntimeError("v5 request leaked post-candidate evidence")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


if __name__ == "__main__":
    main()
