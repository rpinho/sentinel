#!/usr/bin/env python3
"""Build the frozen v7 scoped-candidate census and compact screen requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json, write_gzip_json
from sentinel.stage2_v7 import REQUEST_BUNDLE_SCHEMA_VERSION, build_screen_requests, build_signal_candidates
from sentinel.types import Event, parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v7/protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_EVENTS = Path("holdout/v4/events-test.json.gz")
DEFAULT_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_FEEDBACK = Path("holdout/feedback-snapshot.json")
DEFAULT_OUTPUT = Path("holdout/v7/requests-screen.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--feedback", type=Path, default=DEFAULT_FEEDBACK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    protocol = _read(args.protocol)
    dataset = read_gzip_json(args.dataset)
    event_doc = read_gzip_json(args.events)
    scorecard = _read(args.scorecard)
    feedback = _read(args.feedback)
    _verify_inputs(protocol, dataset, event_doc, scorecard, feedback)
    rows = cast(Sequence[Mapping[str, Any]], dataset["rows"])
    predictions = cast(Sequence[Mapping[str, Any]], cast(Mapping[str, Any], scorecard["test"])["predictions"])
    events = [Event.from_dict(raw) for raw in cast(Sequence[Mapping[str, Any]], event_doc["events"])]
    candidates = build_signal_candidates(
        rows=rows,
        predictions=predictions,
        events=events,
        protocol=protocol,
        feedback_rows=cast(Sequence[Mapping[str, Any]], feedback["rows"]),
    )
    requests, batches = build_screen_requests(candidates, protocol=protocol)
    for candidate in candidates:
        if "label" in candidate or "incident_ids" in candidate:
            raise RuntimeError("v7 candidate leaked evaluation labels")
        cutoff = parse_timestamp(str(candidate["candidate_ts"]))
        if any(parse_timestamp(str(ref["ts"])) >= cutoff for ref in cast(Sequence[Mapping[str, Any]], candidate["event_refs"])):
            raise RuntimeError("v7 candidate leaked post-cutoff event refs")
    counts: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate["deterministic_disposition"])
        counts[key] = counts.get(key, 0) + 1
    base = {
        "schema_version": REQUEST_BUNDLE_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "phase": "screen",
        "protocol_sha256": canonical_sha256(protocol),
        "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"],
        "v4_test_events_sha256": cast(Mapping[str, Any], event_doc["hashes"])["events_sha256"],
        "generated_candidate_count": len(candidates),
        "deterministic_disposition_counts": counts,
        "request_count": len(requests),
        "invocation_count": len(batches),
        "candidate_census_sha256": canonical_sha256(candidates),
        "candidates": candidates,
        "requests": requests,
        "batches": batches,
    }
    bundle = {**base, "request_bundle_sha256": canonical_sha256(base)}
    write_gzip_json(args.output, bundle)
    chars = [len(str(cast(Mapping[str, Any], batch["identity"])["user_content"])) for batch in batches]
    print(
        f"V7_SCREEN_REQUESTS generated={len(candidates)} eligible={len(requests)} "
        f"batches={len(batches)} max_chars={max(chars, default=0)} "
        f"request_bundle_sha256={bundle['request_bundle_sha256']}"
    )


def _verify_inputs(protocol: Mapping[str, Any], dataset: Mapping[str, Any], events: Mapping[str, Any], scorecard: Mapping[str, Any], feedback: Mapping[str, Any]) -> None:
    frozen = cast(Mapping[str, Any], protocol["frozen_identities"])
    checks = {
        "dataset": cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] == frozen["v4_test_dataset_content_sha256"],
        "events": cast(Mapping[str, Any], events["hashes"])["events_sha256"] == frozen["v4_test_events_sha256"],
        "scorecard": scorecard["scorecard_sha256"] == frozen["v4_scorecard_sha256"],
        "feedback": canonical_sha256(feedback) == frozen["feedback_snapshot_sha256"],
    }
    if not all(checks.values()):
        raise RuntimeError(f"v7 frozen input mismatch: {checks}")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
