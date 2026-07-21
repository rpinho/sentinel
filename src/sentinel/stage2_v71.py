"""Label-blind election correction for the frozen Sentinel v7 study.

V7.1 reuses the complete v7 candidate census and light-screen verdicts.  Its
only behavioral change is the election policy between the light screen and the
heavy investigation: investigate the top decile rather than only rows whose
categorical screen disposition was ``investigate``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256

TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION = 10
ELECTION_POLICY_VERSION = "v7-screen-top-decile-v1"

_DISPOSITION_RANK = {
    "investigate": 0,
    "watch": 1,
    "insufficient_data": 2,
    "benign_dismiss": 3,
}
_IMPACT_RANK = {"high": 0, "medium": 1, "low": 2, "negligible": 3}
_REASON_RANK = {
    "plausible_breakage": 0,
    "unclear_signal": 1,
    "missing_corroboration": 2,
    "attribution_unverified": 3,
    "needs_more_observation": 4,
    "baseline_inflation": 5,
    "low_absolute_impact": 6,
    "ordinary_discussion": 7,
    "support_or_configuration": 8,
    "routine_maintenance": 9,
    "pull_request_workflow": 10,
    "feature_request": 11,
    "automation": 12,
}


def election_count(
    screen_count: int,
    *,
    target_compression: int = TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION,
) -> int:
    """Return the pre-declared, approximately one-order compression target."""

    if screen_count < 0:
        raise ValueError("screen_count must be non-negative")
    if target_compression <= 0:
        raise ValueError("target_compression must be positive")
    return math.ceil(screen_count / target_compression)


def elect_for_investigation(
    *,
    candidates: Sequence[Mapping[str, Any]],
    screen_records: Sequence[Mapping[str, Any]],
    target_compression: int = TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION,
) -> dict[str, Any]:
    """Rank frozen v7 screen outputs and return a deterministic election manifest.

    The rank consumes only fields already present in the v7 candidate and
    screen request/response.  It never accepts labels or evaluation artifacts.
    """

    candidate_by_id = {str(item["candidate_id"]): item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise RuntimeError("v7.1 election received duplicate candidate identities")
    if len({str(item["candidate_id"]) for item in screen_records}) != len(screen_records):
        raise RuntimeError("v7.1 election received duplicate screen identities")
    missing = [
        str(item["candidate_id"])
        for item in screen_records
        if str(item["candidate_id"]) not in candidate_by_id
    ]
    if missing:
        raise RuntimeError(f"v7.1 election screen identities miss candidates: {missing[:3]}")

    ordered = sorted(
        screen_records,
        key=lambda record: _election_rank_key(
            record,
            candidate_by_id[str(record["candidate_id"])],
        ),
    )
    selected_count = election_count(
        len(ordered), target_compression=target_compression
    )
    selected = []
    for rank, record in enumerate(ordered[:selected_count], start=1):
        response = cast(Mapping[str, Any], record["response"])
        candidate = candidate_by_id[str(record["candidate_id"])]
        metric = cast(Mapping[str, Any], candidate["metric"])
        features = cast(Mapping[str, Any], candidate["features"])
        selected.append(
            {
                "rank": rank,
                "candidate_id": record["candidate_id"],
                "screen_disposition": response["disposition"],
                "impact_rank": response["impact_rank"],
                "reason_code": response["reason_code"],
                "absolute_magnitude": metric["current_value"],
                "breadth": _breadth(features),
                "velocity": metric["delta_from_baseline"],
            }
        )
    base = {
        "policy_version": ELECTION_POLICY_VERSION,
        "target_screen_to_investigation_compression": target_compression,
        "rounding": "ceil",
        "screen_count": len(ordered),
        "selected_count": len(selected),
        "rank_fields": [
            "screen_disposition: investigate, watch, insufficient_data, benign_dismiss",
            "impact_rank: high, medium, low, negligible",
            "reason_code: plausible or unresolved before routine/benign classes",
            "absolute_magnitude: descending",
            "breadth: descending",
            "velocity: descending",
            "candidate_id: ascending stable tie-break",
        ],
        "label_blind": True,
        "selected": selected,
    }
    return {**base, "election_manifest_sha256": canonical_sha256(base)}


def apply_election_manifest(
    screen_records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Create an in-memory adapter accepted by the unchanged v7 pack builder."""

    selected_ids = {
        str(item["candidate_id"])
        for item in cast(Sequence[Mapping[str, Any]], manifest["selected"])
    }
    adapted = []
    for record in screen_records:
        copied = dict(record)
        response = dict(cast(Mapping[str, Any], record["response"]))
        response["disposition"] = (
            "investigate"
            if str(record["candidate_id"]) in selected_ids
            else "benign_dismiss"
        )
        copied["response"] = response
        adapted.append(copied)
    return adapted


def _election_rank_key(
    record: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[Any, ...]:
    response = cast(Mapping[str, Any], record["response"])
    metric = cast(Mapping[str, Any], candidate["metric"])
    features = cast(Mapping[str, Any], candidate["features"])
    return (
        _DISPOSITION_RANK[str(response["disposition"])],
        _IMPACT_RANK[str(response["impact_rank"])],
        _REASON_RANK.get(str(response["reason_code"]), len(_REASON_RANK)),
        -float(metric["current_value"]),
        -float(_breadth(features)),
        -float(metric["delta_from_baseline"]),
        str(record["candidate_id"]),
    )


def _breadth(features: Mapping[str, Any]) -> float:
    for key in (
        "unique_human_actors",
        "new_issue_count",
        "issue_count",
        "human_events",
    ):
        value = features.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    issue_numbers = features.get("issue_numbers")
    return float(len(issue_numbers)) if isinstance(issue_numbers, list) else 0.0
