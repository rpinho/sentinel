#!/usr/bin/env python3
"""Fetch only elected v7 evidence packs and freeze investigation requests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json, write_gzip_json
from sentinel.stage2_v7 import REQUEST_BUNDLE_SCHEMA_VERSION, build_investigation_requests
from sentinel.types import Event, parse_timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=Path("holdout/v7/protocol.json"))
    parser.add_argument("--screen-requests", type=Path, default=Path("holdout/v7/requests-screen.json.gz"))
    parser.add_argument("--screen-verdicts", type=Path, default=Path("holdout/v7/verdicts-screen.json.gz"))
    parser.add_argument("--events", type=Path, default=Path("holdout/v4/events-test.json.gz"))
    parser.add_argument("--output", type=Path, default=Path("holdout/v7/requests-investigation.json.gz"))
    args = parser.parse_args()
    protocol = _read(args.protocol)
    screen_requests = read_gzip_json(args.screen_requests)
    screen_verdicts = read_gzip_json(args.screen_verdicts)
    event_doc = read_gzip_json(args.events)
    _verify(protocol, screen_requests, screen_verdicts, event_doc)
    candidates = cast(Sequence[Mapping[str, Any]], screen_requests["candidates"])
    screen_records = cast(Sequence[Mapping[str, Any]], screen_verdicts["records"])
    events = [Event.from_dict(raw) for raw in cast(Sequence[Mapping[str, Any]], event_doc["events"])]
    requests, batches = build_investigation_requests(
        candidates=candidates,
        screen_records=screen_records,
        events=events,
        protocol=protocol,
    )
    for request in requests:
        packet = cast(Mapping[str, Any], request["packet"])
        candidate = cast(Mapping[str, Any], packet["candidate"])
        cutoff = parse_timestamp(str(candidate["candidate_ts"]))
        for evidence in cast(Sequence[Mapping[str, Any]], packet["evidence_excerpts"]):
            if parse_timestamp(str(evidence["ts"])) >= cutoff:
                raise RuntimeError("v7 investigation leaked post-cutoff evidence")
    base = {
        "schema_version": REQUEST_BUNDLE_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "phase": "investigation",
        "protocol_sha256": canonical_sha256(protocol),
        "screen_request_bundle_sha256": screen_requests["request_bundle_sha256"],
        "screen_verdict_bundle_sha256": screen_verdicts["verdict_bundle_sha256"],
        "request_count": len(requests),
        "invocation_count": len(batches),
        "requests": requests,
        "batches": batches,
    }
    bundle = {**base, "request_bundle_sha256": canonical_sha256(base)}
    write_gzip_json(args.output, bundle)
    chars = [len(str(cast(Mapping[str, Any], batch["identity"])["user_content"])) for batch in batches]
    print(
        f"V7_INVESTIGATION_REQUESTS elected={len(requests)} batches={len(batches)} "
        f"max_chars={max(chars, default=0)} request_bundle_sha256={bundle['request_bundle_sha256']}"
    )


def _verify(protocol: Mapping[str, Any], requests: Mapping[str, Any], verdicts: Mapping[str, Any], events: Mapping[str, Any]) -> None:
    protocol_hash = canonical_sha256(protocol)
    if requests.get("protocol_sha256") != protocol_hash or verdicts.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("v7 screen artifacts differ from protocol")
    if verdicts.get("request_bundle_sha256") != requests.get("request_bundle_sha256"):
        raise RuntimeError("v7 screen verdicts differ from screen requests")
    if cast(Mapping[str, Any], events["hashes"])["events_sha256"] != cast(Mapping[str, Any], protocol["frozen_identities"])["v4_test_events_sha256"]:
        raise RuntimeError("v7 events differ from frozen input")
    for document, stamp in [(requests, "request_bundle_sha256"), (verdicts, "verdict_bundle_sha256")]:
        if document.get(stamp) != canonical_sha256({key: value for key, value in document.items() if key != stamp}):
            raise RuntimeError(f"invalid {stamp}")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
