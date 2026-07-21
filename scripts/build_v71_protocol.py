#!/usr/bin/env python3
"""Freeze v7.1 before scoring, changing only the v7 election policy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json
from sentinel.stage2_v71 import (
    ELECTION_POLICY_VERSION,
    TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION,
    election_count,
)

PUBLISHED_V7_PROTOCOL_SHA256 = "dd5cf502cc62108d82792690be0d19a961e85307063c251f7e3c439e0f5f06e9"
PUBLISHED_V7_SCREEN_REQUESTS_SHA256 = "ea880012f9c4a23cabfce53bc1020a832e8cb7085d2d3fbeb8fd41fa74b97082"
PUBLISHED_V7_SCREEN_VERDICTS_SHA256 = "05e486df639c302ede86e5ef7fc63960de468109e82863b4eeccd530866dc839"
PUBLISHED_V7_SCORECARD_SHA256 = "c2097f6ed1039f4295b1d26db2c71cc24472a0a6d98bda68fe68959e0578e19f"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v7-protocol", type=Path, default=Path("holdout/v7/protocol.json"))
    parser.add_argument("--screen-requests", type=Path, default=Path("holdout/v7/requests-screen.json.gz"))
    parser.add_argument("--screen-verdicts", type=Path, default=Path("holdout/v7/verdicts-screen.json.gz"))
    parser.add_argument("--architecture", type=Path, default=Path("docs/triage-architecture.md"))
    parser.add_argument("--output", type=Path, default=Path("holdout/v7.1/protocol.json"))
    args = parser.parse_args()

    parent = _read(args.v7_protocol)
    screen_requests = read_gzip_json(args.screen_requests)
    screen_verdicts = read_gzip_json(args.screen_verdicts)
    _verify_parent(parent, screen_requests, screen_verdicts)
    screen_count = int(screen_requests["request_count"])
    target = election_count(screen_count)

    protocol = copy.deepcopy(parent)
    protocol["study_id"] = "sentinel-signal-scoped-stage2-v7.1"
    protocol["version"] = "v7.1"
    protocol["objective"] = (
        "Correct the v7 election-policy spec violation by reusing its exact "
        "candidate census and light-screen verdicts, then investigating the "
        "label-blind top decile on the unchanged v4 test."
    )
    protocol["integrity"] = {
        **dict(cast(Mapping[str, Any], parent["integrity"])),
        "v7_candidate_census_reused_exactly": True,
        "v7_screen_requests_reused_exactly": True,
        "v7_screen_verdicts_reused_exactly": True,
        "only_behavioral_delta_from_v7": "election_policy",
        "election_policy_selected_without_labels": True,
        "v7_1_protocol_and_requests_frozen_before_investigation_scoring": True,
        "limitation": (
            "V7.1 is a one-knob correction on the already published v7/v4 "
            "test, not a new untouched holdout."
        ),
    }
    protocol["frozen_identities"] = {
        **dict(cast(Mapping[str, Any], parent["frozen_identities"])),
        "parent_v7_protocol_sha256": PUBLISHED_V7_PROTOCOL_SHA256,
        "parent_v7_screen_request_bundle_sha256": PUBLISHED_V7_SCREEN_REQUESTS_SHA256,
        "parent_v7_screen_verdict_bundle_sha256": PUBLISHED_V7_SCREEN_VERDICTS_SHA256,
        "parent_v7_scorecard_sha256": PUBLISHED_V7_SCORECARD_SHA256,
        "architecture_brief_file_sha256": hashlib.sha256(args.architecture.read_bytes()).hexdigest(),
    }
    stage2 = dict(cast(Mapping[str, Any], protocol["stage2"]))
    stage2["election_rule"] = (
        "Rank the frozen v7 light-screen rows without labels and investigate "
        "ceil(screened candidates / 10), preserving roughly one order of "
        "magnitude of compression at this funnel stage."
    )
    protocol["stage2"] = stage2
    protocol["election_policy"] = {
        "version": ELECTION_POLICY_VERSION,
        "source": "The committed architecture brief's pre-declared funnel shape.",
        "target_screen_to_investigation_compression": TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION,
        "rounding": "ceil",
        "screen_count": screen_count,
        "investigation_count": target,
        "rank_fields": [
            "screen_disposition",
            "impact_rank",
            "reason_code",
            "absolute_magnitude_desc",
            "breadth_desc",
            "velocity_desc",
            "candidate_id_asc",
        ],
        "label_fields": [],
        "frozen_before_scoring": True,
    }
    protocol["parent_v7"] = {
        "protocol_sha256": PUBLISHED_V7_PROTOCOL_SHA256,
        "screen_request_bundle_sha256": PUBLISHED_V7_SCREEN_REQUESTS_SHA256,
        "screen_verdict_bundle_sha256": PUBLISHED_V7_SCREEN_VERDICTS_SHA256,
        "scorecard_sha256": PUBLISHED_V7_SCORECARD_SHA256,
        "inherited_without_rerun": [
            "stage1",
            "candidate census",
            "candidate gate",
            "light-screen requests",
            "light-screen verdicts",
            "model",
            "prompts",
            "schemas",
            "batching",
            "evidence policy",
            "evaluation",
        ],
        "allowed_behavioral_delta": ["election_policy"],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"V71_PROTOCOL protocol_sha256={canonical_sha256(protocol)} "
        f"screened={screen_count} investigate={target}"
    )


def _verify_parent(
    protocol: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> None:
    if canonical_sha256(protocol) != PUBLISHED_V7_PROTOCOL_SHA256:
        raise RuntimeError("v7.1 parent protocol is not the published frozen v7 protocol")
    if requests.get("request_bundle_sha256") != PUBLISHED_V7_SCREEN_REQUESTS_SHA256:
        raise RuntimeError("v7.1 parent screen requests are not the published v7 bundle")
    if verdicts.get("verdict_bundle_sha256") != PUBLISHED_V7_SCREEN_VERDICTS_SHA256:
        raise RuntimeError("v7.1 parent screen verdicts are not the published v7 bundle")
    for document, stamp in (
        (requests, "request_bundle_sha256"),
        (verdicts, "verdict_bundle_sha256"),
    ):
        if document.get(stamp) != canonical_sha256(
            {key: value for key, value in document.items() if key != stamp}
        ):
            raise RuntimeError(f"invalid parent {stamp}")
    if verdicts.get("request_bundle_sha256") != requests.get("request_bundle_sha256"):
        raise RuntimeError("v7.1 parent screen request/verdict linkage mismatch")
    if int(requests.get("request_count") or 0) != len(
        cast(Sequence[Mapping[str, Any]], verdicts["records"])
    ):
        raise RuntimeError("v7.1 parent screen census is incomplete")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
