#!/usr/bin/env python3
"""Evaluate the frozen v7.1 election correction beside frozen v7."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from evaluate_stage2_v7 import evaluate
from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=Path("holdout/v7.1/protocol.json"))
    parser.add_argument("--dataset", type=Path, default=Path("holdout/v4/dataset-test.json.gz"))
    parser.add_argument("--v4-scorecard", type=Path, default=Path("holdout/v4/scorecard.json"))
    parser.add_argument("--v5-scorecard", type=Path, default=Path("holdout/v5/scorecard.json"))
    parser.add_argument("--v6-scorecard", type=Path, default=Path("holdout/v6/scorecard.json"))
    parser.add_argument("--v7-scorecard", type=Path, default=Path("holdout/v7/scorecard.json"))
    parser.add_argument("--screen-requests", type=Path, default=Path("holdout/v7/requests-screen.json.gz"))
    parser.add_argument("--screen-verdicts", type=Path, default=Path("holdout/v7/verdicts-screen.json.gz"))
    parser.add_argument("--investigation-requests", type=Path, default=Path("holdout/v7.1/requests-investigation.json.gz"))
    parser.add_argument("--investigation-verdicts", type=Path, default=Path("holdout/v7.1/verdicts-investigation.json.gz"))
    parser.add_argument("--output", type=Path, default=Path("holdout/v7.1/scorecard.json"))
    parser.add_argument("--markdown", type=Path, default=Path("holdout/v7.1/SCORECARD-v7.1.md"))
    args = parser.parse_args()

    protocol = _read(args.protocol)
    dataset = read_gzip_json(args.dataset)
    v4 = _read(args.v4_scorecard)
    v5 = _read(args.v5_scorecard)
    v6 = _read(args.v6_scorecard)
    v7 = _read(args.v7_scorecard)
    screen_requests = read_gzip_json(args.screen_requests)
    screen_verdicts = read_gzip_json(args.screen_verdicts)
    investigation_requests = read_gzip_json(args.investigation_requests)
    investigation_verdicts = read_gzip_json(args.investigation_verdicts)
    elected_ids = _verify_v71(
        protocol,
        v7,
        investigation_requests,
        investigation_verdicts,
    )
    result = evaluate(
        protocol=protocol,
        dataset=dataset,
        v4_scorecard=v4,
        v5_scorecard=v5,
        v6_scorecard=v6,
        screen_requests=screen_requests,
        screen_verdicts=screen_verdicts,
        investigation_requests=investigation_requests,
        investigation_verdicts=investigation_verdicts,
        current_version="v7.1",
        elected_candidate_ids=elected_ids,
        inherited_screen=True,
    )
    result.pop("scorecard_sha256", None)
    comparison = cast(
        dict[str, Any], result["comparison_v5_v6_v7"]
    )
    versions = cast(list[dict[str, Any]], comparison["versions"])
    versions.insert(-1, _v7_comparison_row(v7))
    comparison["unit_note"] = (
        "V5/v6 alerts are estimated repository-hours from their frozen samples; "
        "v7 and v7.1 report exact story precision and story-to-hour mapping."
    )
    manifest = cast(Mapping[str, Any], investigation_requests["election_manifest"])
    inherited_screen_runtime = cast(Mapping[str, Any], cast(Mapping[str, Any], result["runtime"])["screen"])
    investigation_runtime = cast(Mapping[str, Any], cast(Mapping[str, Any], result["runtime"])["investigation"])
    cast(dict[str, Any], result["runtime"])["screen_reused_from_v7"] = True
    cast(dict[str, Any], result["runtime"])["incremental_v7_1_modeled_equivalent_cost_usd"] = investigation_runtime["modeled_equivalent_cost_usd"]
    result["v7_1_correction"] = {
        "classification": "election policy failed the pre-declared design spec; candidate unit and all other v7 components remain frozen",
        "only_behavioral_delta": "election_policy",
        "label_blind": True,
        "parent_v7_protocol_sha256": cast(Mapping[str, Any], protocol["parent_v7"])["protocol_sha256"],
        "parent_v7_scorecard_sha256": v7["scorecard_sha256"],
        "parent_v7_screen_request_bundle_sha256": screen_requests["request_bundle_sha256"],
        "parent_v7_screen_verdict_bundle_sha256": screen_verdicts["verdict_bundle_sha256"],
        "election_manifest_sha256": manifest["election_manifest_sha256"],
        "screen_count": manifest["screen_count"],
        "investigation_count": manifest["selected_count"],
        "screen_to_investigation_compression": float(manifest["screen_count"]) / float(manifest["selected_count"]),
        "screen_runtime_inherited_modeled_equivalent_cost_usd": inherited_screen_runtime["modeled_equivalent_cost_usd"],
        "incremental_investigation_modeled_equivalent_cost_usd": investigation_runtime["modeled_equivalent_cost_usd"],
    }
    primary = cast(Mapping[str, Any], result["end_to_end"])
    old = cast(Mapping[str, Any], v7["end_to_end"])
    result["finding"] = {
        "conclusion": (
            "V7.1 corrected the label-blind election throttle and the frozen "
            "result is reported without any label-informed tuning."
        ),
        "story_precision_delta_vs_v7": float(primary["story_precision"]) - float(old["story_precision"]),
        "hour_precision_delta_vs_v7": float(primary["hour_mapped_precision"]) - float(old["hour_mapped_precision"]),
        "incident_recall_delta_vs_v7": float(primary["incident_recall"]) - float(old["incident_recall"]),
        "incidents_caught_delta_vs_v7": int(primary["incidents_caught"]) - int(old["incidents_caught"]),
    }
    result["scorecard_sha256"] = canonical_sha256(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(_markdown(result), encoding="utf-8")
    print(
        f"V71_SCORECARD stories={primary['published_stories']} "
        f"story_precision={primary['story_precision']:.6f} "
        f"hour_precision={primary['hour_mapped_precision']:.6f} "
        f"incident_recall={primary['incident_recall']:.6f} "
        f"scorecard_sha256={result['scorecard_sha256']}"
    )


def _verify_v71(
    protocol: Mapping[str, Any],
    v7: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> set[str]:
    parent = cast(Mapping[str, Any], protocol["parent_v7"])
    if v7.get("scorecard_sha256") != parent["scorecard_sha256"]:
        raise RuntimeError("v7.1 comparison is not the frozen v7 scorecard")
    manifest = cast(Mapping[str, Any], requests["election_manifest"])
    if manifest.get("election_manifest_sha256") != canonical_sha256(
        {key: value for key, value in manifest.items() if key != "election_manifest_sha256"}
    ):
        raise RuntimeError("invalid v7.1 election manifest")
    selected = {
        str(item["candidate_id"])
        for item in cast(Sequence[Mapping[str, Any]], manifest["selected"])
    }
    request_ids = {
        str(item["candidate_id"])
        for item in cast(Sequence[Mapping[str, Any]], requests["requests"])
    }
    verdict_ids = {
        str(item["candidate_id"])
        for item in cast(Sequence[Mapping[str, Any]], verdicts["records"])
    }
    if selected != request_ids or selected != verdict_ids:
        raise RuntimeError("v7.1 election/request/verdict identities differ")
    if len(selected) != int(cast(Mapping[str, Any], protocol["election_policy"])["investigation_count"]):
        raise RuntimeError("v7.1 election count differs from frozen policy")
    return selected


def _v7_comparison_row(scorecard: Mapping[str, Any]) -> dict[str, Any]:
    metrics = cast(Mapping[str, Any], scorecard["end_to_end"])
    runtime = cast(Mapping[str, Any], scorecard["runtime"])
    return {
        "version": "v7",
        "precision": metrics["hour_mapped_precision"],
        "primary_story_precision": metrics["story_precision"],
        "incident_recall": metrics["incident_recall"],
        "true_positive_hours_killed": metrics["true_positive_hours_killed"],
        "median_lead_hours": cast(Mapping[str, Any], metrics["lead_time"])["median_hours"],
        "alerts_or_stories": metrics["published_stories"],
        "mapped_alert_hours": metrics["mapped_alert_hours"],
        "modeled_equivalent_cost_usd": runtime["combined_modeled_equivalent_cost_usd"],
    }


def _markdown(result: Mapping[str, Any]) -> str:
    primary = cast(Mapping[str, Any], result["end_to_end"])
    clean = cast(Mapping[str, Any], result["clean_source_cut"])
    funnel = cast(Mapping[str, Any], result["funnel"])
    correction = cast(Mapping[str, Any], result["v7_1_correction"])
    runtime = cast(Mapping[str, Any], result["runtime"])
    rows = cast(
        Sequence[Mapping[str, Any]],
        cast(Mapping[str, Any], result["comparison_v5_v6_v7"])["versions"],
    )
    comparison = "\n".join(
        f"| {row['version']} | {float(row['precision']):.1%} | "
        f"{float(row['incident_recall']):.1%} | "
        f"{int(row['true_positive_hours_killed'])} | "
        f"{float(row['median_lead_hours']):.1f} h |"
        for row in rows
    )
    return f"""# Sentinel v7.1 election-corrected scorecard

V7.1 freezes every v7 component except the election policy. It reuses the
exact 4,412 v7 screen verdicts and investigates the label-blind top decile.

## Result

- Published stories: **{primary['published_stories']}**
- Story precision: **{primary['story_precision']:.1%}**
- Hour-mapped precision: **{primary['hour_mapped_precision']:.1%}**
- Incident recall: **{primary['incident_recall']:.1%}**
- Incidents caught: **{primary['incidents_caught']} / {primary['incidents']}**
- True stage-one alert-hours killed: **{primary['true_positive_hours_killed']}**
- Median lead: **{cast(Mapping[str, Any], primary['lead_time'])['median_hours']:.1f} hours**
- Clean-source story precision / incident recall: **{clean['story_precision']:.1%} / {clean['incident_recall']:.1%}**

| Version | Hour precision | Incident recall | True hours killed | Median lead |
|---|---:|---:|---:|---:|
{comparison}

## Funnel and correction

`{funnel['stage1_alert_hours']} stage-one hours → {funnel['scoped_candidates_generated']} scoped claims → {funnel['deterministic_gate_survivors']} screened → {funnel['screen_elected_investigations']} investigated → {funnel['published_stories']} stories`

V7's 4,412→4 election was a policy failure against the pre-declared funnel
shape, not evidence against the signal-scoped unit. V7.1 changes only that
policy: {correction['screen_count']}→{correction['investigation_count']}
({correction['screen_to_investigation_compression']:.1f}:1 compression). No
labels entered election or either judge request.

## Runtime and integrity

- Inherited v7 screen invocations: {cast(Mapping[str, Any], runtime['screen'])['invocations']} (not rerun)
- New Claude subscription investigation invocations: {cast(Mapping[str, Any], runtime['investigation'])['invocations']}
- Incremental modeled equivalent usage: ${runtime['incremental_v7_1_modeled_equivalent_cost_usd']:.2f} (not API billing)
- OpenAI calls: zero
- Protocol SHA-256: `{result['protocol_sha256']}`
- Election manifest SHA-256: `{correction['election_manifest_sha256']}`
- Scorecard SHA-256: `{result['scorecard_sha256']}`

Both v7 and v7.1 are frozen. This reuses the published v4 test; the full-label
result remains descriptive because the v4 label audit found 30% obvious noise.
"""


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
