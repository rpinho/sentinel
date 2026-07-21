#!/usr/bin/env python3
"""Freeze v7.1 top-decile investigation requests from exact v7 screening."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json, write_gzip_json
from sentinel.stage2_v7 import REQUEST_BUNDLE_SCHEMA_VERSION, build_investigation_requests
from sentinel.stage2_v71 import apply_election_manifest, elect_for_investigation
from sentinel.types import Event, parse_timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=Path("holdout/v7.1/protocol.json"))
    parser.add_argument("--screen-requests", type=Path, default=Path("holdout/v7/requests-screen.json.gz"))
    parser.add_argument("--screen-verdicts", type=Path, default=Path("holdout/v7/verdicts-screen.json.gz"))
    parser.add_argument("--events", type=Path, default=Path("holdout/v4/events-test.json.gz"))
    parser.add_argument("--output", type=Path, default=Path("holdout/v7.1/requests-investigation.json.gz"))
    args = parser.parse_args()

    protocol = _read(args.protocol)
    screen_requests = read_gzip_json(args.screen_requests)
    screen_verdicts = read_gzip_json(args.screen_verdicts)
    event_doc = read_gzip_json(args.events)
    _verify(protocol, screen_requests, screen_verdicts, event_doc)
    candidates = cast(Sequence[Mapping[str, Any]], screen_requests["candidates"])
    records = cast(Sequence[Mapping[str, Any]], screen_verdicts["records"])
    manifest = elect_for_investigation(candidates=candidates, screen_records=records)
    adapted_records = apply_election_manifest(records, manifest)
    events = [
        Event.from_dict(raw)
        for raw in cast(Sequence[Mapping[str, Any]], event_doc["events"])
    ]
    requests, batches = build_investigation_requests(
        candidates=candidates,
        screen_records=adapted_records,
        events=events,
        protocol=protocol,
    )
    selected_ids = {
        str(item["candidate_id"])
        for item in cast(Sequence[Mapping[str, Any]], manifest["selected"])
    }
    if {str(item["candidate_id"]) for item in requests} != selected_ids:
        raise RuntimeError("v7.1 investigation requests differ from frozen election")
    for request in requests:
        packet = cast(Mapping[str, Any], request["packet"])
        candidate = cast(Mapping[str, Any], packet["candidate"])
        cutoff = parse_timestamp(str(candidate["candidate_ts"]))
        for evidence in cast(
            Sequence[Mapping[str, Any]], packet["evidence_excerpts"]
        ):
            if parse_timestamp(str(evidence["ts"])) >= cutoff:
                raise RuntimeError("v7.1 investigation leaked post-cutoff evidence")

    parent = cast(Mapping[str, Any], protocol["parent_v7"])
    base = {
        "schema_version": REQUEST_BUNDLE_SCHEMA_VERSION,
        "study_id": protocol["study_id"],
        "phase": "investigation",
        "protocol_sha256": canonical_sha256(protocol),
        "parent_v7_protocol_sha256": parent["protocol_sha256"],
        "screen_request_bundle_sha256": screen_requests["request_bundle_sha256"],
        "screen_verdict_bundle_sha256": screen_verdicts["verdict_bundle_sha256"],
        "election_manifest": manifest,
        "request_count": len(requests),
        "invocation_count": len(batches),
        "requests": requests,
        "batches": batches,
    }
    bundle = {**base, "request_bundle_sha256": canonical_sha256(base)}
    write_gzip_json(args.output, bundle)
    chars = [
        len(str(cast(Mapping[str, Any], batch["identity"])["user_content"]))
        for batch in batches
    ]
    print(
        f"V71_INVESTIGATION_REQUESTS elected={len(requests)} "
        f"batches={len(batches)} max_chars={max(chars, default=0)} "
        f"election_manifest_sha256={manifest['election_manifest_sha256']} "
        f"request_bundle_sha256={bundle['request_bundle_sha256']}"
    )


def _verify(
    protocol: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
    events: Mapping[str, Any],
) -> None:
    parent = cast(Mapping[str, Any], protocol["parent_v7"])
    if requests.get("request_bundle_sha256") != parent["screen_request_bundle_sha256"]:
        raise RuntimeError("v7.1 screen requests differ from frozen v7")
    if verdicts.get("verdict_bundle_sha256") != parent["screen_verdict_bundle_sha256"]:
        raise RuntimeError("v7.1 screen verdicts differ from frozen v7")
    if verdicts.get("request_bundle_sha256") != requests.get("request_bundle_sha256"):
        raise RuntimeError("v7.1 inherited screen linkage mismatch")
    frozen = cast(Mapping[str, Any], protocol["frozen_identities"])
    if cast(Mapping[str, Any], events["hashes"])["events_sha256"] != frozen["v4_test_events_sha256"]:
        raise RuntimeError("v7.1 events differ from frozen input")
    for document, stamp in (
        (requests, "request_bundle_sha256"),
        (verdicts, "verdict_bundle_sha256"),
    ):
        if document.get(stamp) != canonical_sha256(
            {key: value for key, value in document.items() if key != stamp}
        ):
            raise RuntimeError(f"invalid inherited {stamp}")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
