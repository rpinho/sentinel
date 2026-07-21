import copy
import gzip
import json
from pathlib import Path

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v71 import (
    TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION,
    apply_election_manifest,
    elect_for_investigation,
    election_count,
)


ROOT = Path(__file__).parents[1]
V71_PROTOCOL_SHA256 = "bd7336d99fc9603c0ecb6913adb4159b64271484e0dfb3025e938c82973040f9"
V71_ELECTION_MANIFEST_SHA256 = "759b84ce4c20ace8aeb8d589df13ef50f24306d0f6ae8b926ba019aa5f39bcd8"
V71_INVESTIGATION_REQUESTS_SHA256 = "f773dabdb37e76b5b078ee975cea323a764b9fa2b314e5424f42657d7f9fc547"
V71_INVESTIGATION_VERDICTS_SHA256 = "93a0ce8ba15f93706e32e95327b00500e04c23c4a0949e9fc1229e16ce716a6d"
V71_SCORECARD_SHA256 = "1bba31c415856ff0eedfe6d68efccf36c6436e7e525f9af4d4302fd857083af5"


def test_election_count_is_the_predeclared_top_decile() -> None:
    assert TARGET_SCREEN_TO_INVESTIGATION_COMPRESSION == 10
    assert election_count(4_412) == 442
    assert election_count(0) == 0


def test_election_is_label_blind_stable_and_adapts_only_selected_rows() -> None:
    candidates = [
        _candidate("a", magnitude=9, breadth=3),
        _candidate("b", magnitude=4, breadth=1),
        _candidate("c", magnitude=2, breadth=1),
        _candidate("d", magnitude=1, breadth=1),
    ]
    records = [
        _record("a", "watch", "medium", "plausible_breakage"),
        _record("b", "investigate", "low", "plausible_breakage"),
        _record("c", "benign_dismiss", "high", "plausible_breakage"),
        _record("d", "insufficient_data", "negligible", "unclear_signal"),
    ]
    first = elect_for_investigation(
        candidates=candidates,
        screen_records=records,
        target_compression=2,
    )
    labeled = copy.deepcopy(candidates)
    for index, candidate in enumerate(labeled):
        candidate["label"] = {"positive": bool(index % 2), "secret": index}
    second = elect_for_investigation(
        candidates=labeled,
        screen_records=records,
        target_compression=2,
    )
    assert first == second
    assert [item["candidate_id"] for item in first["selected"]] == ["b", "a"]
    adapted = apply_election_manifest(records, first)
    dispositions = {
        item["candidate_id"]: item["response"]["disposition"] for item in adapted
    }
    assert dispositions == {
        "a": "investigate",
        "b": "investigate",
        "c": "benign_dismiss",
        "d": "benign_dismiss",
    }


def test_frozen_v71_election_reuses_v7_and_selects_442() -> None:
    protocol = json.loads((ROOT / "holdout/v7.1/protocol.json").read_text())
    parent_protocol = json.loads((ROOT / "holdout/v7/protocol.json").read_text())
    with gzip.open(ROOT / "holdout/v7/requests-screen.json.gz", "rt") as handle:
        screen_requests = json.load(handle)
    with gzip.open(ROOT / "holdout/v7/verdicts-screen.json.gz", "rt") as handle:
        screen_verdicts = json.load(handle)
    with gzip.open(
        ROOT / "holdout/v7.1/requests-investigation.json.gz", "rt"
    ) as handle:
        investigation_requests = json.load(handle)

    parent = protocol["parent_v7"]
    assert canonical_sha256(protocol) == V71_PROTOCOL_SHA256
    assert investigation_requests["request_bundle_sha256"] == V71_INVESTIGATION_REQUESTS_SHA256
    assert parent_protocol["stage1"] == protocol["stage1"]
    assert parent_protocol["candidate_policy"] == protocol["candidate_policy"]
    assert parent_protocol["evidence_policy"] == protocol["evidence_policy"]
    assert parent_protocol["batching"] == protocol["batching"]
    assert parent_protocol["verdicts"] == protocol["verdicts"]
    assert parent_protocol["evaluation"] == protocol["evaluation"]
    assert parent_protocol["feedback_loop"] == protocol["feedback_loop"]
    assert parent_protocol["cost_gate"] == protocol["cost_gate"]
    parent_stage2 = dict(parent_protocol["stage2"])
    corrected_stage2 = dict(protocol["stage2"])
    parent_stage2.pop("election_rule")
    corrected_stage2.pop("election_rule")
    assert parent_stage2 == corrected_stage2
    assert screen_requests["request_bundle_sha256"] == parent["screen_request_bundle_sha256"]
    assert screen_verdicts["verdict_bundle_sha256"] == parent["screen_verdict_bundle_sha256"]
    assert investigation_requests["screen_request_bundle_sha256"] == parent["screen_request_bundle_sha256"]
    assert investigation_requests["screen_verdict_bundle_sha256"] == parent["screen_verdict_bundle_sha256"]
    assert investigation_requests["request_count"] == 442
    assert investigation_requests["invocation_count"] == 23
    manifest = investigation_requests["election_manifest"]
    assert manifest["selected_count"] == 442
    assert manifest["screen_count"] == 4_412
    assert manifest["label_blind"] is True
    assert manifest["election_manifest_sha256"] == V71_ELECTION_MANIFEST_SHA256
    assert manifest["election_manifest_sha256"] == canonical_sha256(
        {
            key: value
            for key, value in manifest.items()
            if key != "election_manifest_sha256"
        }
    )
    selected_ids = {item["candidate_id"] for item in manifest["selected"]}
    original_investigates = {
        item["candidate_id"]
        for item in screen_verdicts["records"]
        if item["response"]["disposition"] == "investigate"
    }
    assert original_investigates <= selected_ids


def test_published_v71_result_is_complete_linked_and_frozen() -> None:
    with gzip.open(
        ROOT / "holdout/v7.1/requests-investigation.json.gz", "rt"
    ) as handle:
        requests = json.load(handle)
    with gzip.open(
        ROOT / "holdout/v7.1/verdicts-investigation.json.gz", "rt"
    ) as handle:
        verdicts = json.load(handle)
    scorecard = json.loads(
        (ROOT / "holdout/v7.1/scorecard.json").read_text(encoding="utf-8")
    )

    assert verdicts["request_bundle_sha256"] == requests["request_bundle_sha256"]
    assert verdicts["verdict_bundle_sha256"] == V71_INVESTIGATION_VERDICTS_SHA256
    assert verdicts["verdict_bundle_sha256"] == canonical_sha256(
        {
            key: value
            for key, value in verdicts.items()
            if key != "verdict_bundle_sha256"
        }
    )
    assert len(verdicts["records"]) == 442
    assert scorecard["scorecard_sha256"] == V71_SCORECARD_SHA256
    assert scorecard["scorecard_sha256"] == canonical_sha256(
        {
            key: value
            for key, value in scorecard.items()
            if key != "scorecard_sha256"
        }
    )
    assert scorecard["funnel"]["screen_elected_investigations"] == 442
    assert scorecard["end_to_end"]["incidents_caught"] == 7
    assert scorecard["end_to_end"]["published_stories"] == 33


def _candidate(candidate_id: str, *, magnitude: float, breadth: int) -> dict:
    return {
        "candidate_id": candidate_id,
        "metric": {
            "current_value": magnitude,
            "delta_from_baseline": magnitude,
        },
        "features": {"unique_human_actors": breadth},
    }


def _record(
    candidate_id: str,
    disposition: str,
    impact_rank: str,
    reason_code: str,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "response": {
            "disposition": disposition,
            "impact_rank": impact_rank,
            "reason_code": reason_code,
        },
    }
