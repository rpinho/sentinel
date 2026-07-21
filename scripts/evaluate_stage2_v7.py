#!/usr/bin/env python3
"""Evaluate frozen v7 signal claims as deduplicated stories against v4 labels."""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json
from sentinel.types import parse_timestamp

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=Path("holdout/v7/protocol.json"))
    parser.add_argument("--dataset", type=Path, default=Path("holdout/v4/dataset-test.json.gz"))
    parser.add_argument("--v4-scorecard", type=Path, default=Path("holdout/v4/scorecard.json"))
    parser.add_argument("--v5-scorecard", type=Path, default=Path("holdout/v5/scorecard.json"))
    parser.add_argument("--v6-scorecard", type=Path, default=Path("holdout/v6/scorecard.json"))
    parser.add_argument("--screen-requests", type=Path, default=Path("holdout/v7/requests-screen.json.gz"))
    parser.add_argument("--screen-verdicts", type=Path, default=Path("holdout/v7/verdicts-screen.json.gz"))
    parser.add_argument("--investigation-requests", type=Path, default=Path("holdout/v7/requests-investigation.json.gz"))
    parser.add_argument("--investigation-verdicts", type=Path, default=Path("holdout/v7/verdicts-investigation.json.gz"))
    parser.add_argument("--output", type=Path, default=Path("holdout/v7/scorecard.json"))
    parser.add_argument("--markdown", type=Path, default=Path("holdout/v7/SCORECARD-v7.md"))
    args = parser.parse_args()
    protocol = _read(args.protocol)
    dataset = read_gzip_json(args.dataset)
    v4 = _read(args.v4_scorecard)
    v5 = _read(args.v5_scorecard)
    v6 = _read(args.v6_scorecard)
    screen_requests = read_gzip_json(args.screen_requests)
    screen_verdicts = read_gzip_json(args.screen_verdicts)
    investigation_requests = read_gzip_json(args.investigation_requests)
    investigation_verdicts = read_gzip_json(args.investigation_verdicts)
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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(_markdown(result), encoding="utf-8")
    primary = cast(Mapping[str, Any], result["end_to_end"])
    print(
        f"V7_SCORECARD stories={primary['published_stories']} "
        f"story_precision={primary['story_precision']:.6f} "
        f"hour_precision={primary['hour_mapped_precision']:.6f} "
        f"incident_recall={primary['incident_recall']:.6f} "
        f"scorecard_sha256={result['scorecard_sha256']}"
    )


def evaluate(
    *,
    protocol: Mapping[str, Any],
    dataset: Mapping[str, Any],
    v4_scorecard: Mapping[str, Any],
    v5_scorecard: Mapping[str, Any],
    v6_scorecard: Mapping[str, Any],
    screen_requests: Mapping[str, Any],
    screen_verdicts: Mapping[str, Any],
    investigation_requests: Mapping[str, Any],
    investigation_verdicts: Mapping[str, Any],
    current_version: str = "v7",
    elected_candidate_ids: set[str] | None = None,
    inherited_screen: bool = False,
) -> dict[str, Any]:
    _verify(
        protocol,
        dataset,
        v4_scorecard,
        v5_scorecard,
        v6_scorecard,
        screen_requests,
        screen_verdicts,
        investigation_requests,
        investigation_verdicts,
        inherited_screen=inherited_screen,
    )
    candidates = cast(Sequence[Mapping[str, Any]], screen_requests["candidates"])
    candidate_by_id = {str(item["candidate_id"]): item for item in candidates}
    screen_by_id = {str(item["candidate_id"]): item for item in cast(Sequence[Mapping[str, Any]], screen_verdicts["records"])}
    final_by_id = {str(item["candidate_id"]): item for item in cast(Sequence[Mapping[str, Any]], investigation_verdicts["records"])}
    dispositions = []
    for candidate in candidates:
        candidate_key = str(candidate["candidate_id"])
        if candidate["deterministic_disposition"] != "eligible":
            response = {
                "disposition": candidate["deterministic_disposition"],
                "reason_code": candidate["deterministic_reason_code"],
                "severity": "none",
            }
            source = "deterministic_candidate_gate"
        else:
            screen_record = screen_by_id[candidate_key]
            screen_response = cast(Mapping[str, Any], screen_record["response"])
            elected = (
                candidate_key in elected_candidate_ids
                if elected_candidate_ids is not None
                else screen_response["disposition"] == "investigate"
            )
            if elected:
                final_record = final_by_id[candidate_key]
                response = dict(cast(Mapping[str, Any], final_record["response"]))
                source = "investigation_judge"
            else:
                response = {
                    **dict(screen_response),
                    "severity": "none",
                }
                source = "screen_judge"
        dispositions.append(
            {
                "candidate_id": candidate_key,
                "row_id": candidate["row_id"],
                "entity": candidate["entity"],
                "series": cast(Mapping[str, Any], candidate["identity"])["series"],
                "source": source,
                "response": response,
            }
        )
    findings = [item for item in dispositions if cast(Mapping[str, Any], item["response"])["disposition"] == "finding"]
    stories = _build_stories(findings, candidate_by_id)
    incidents = list(cast(Sequence[Mapping[str, Any]], dataset["positive_incidents"]))
    _match_stories(stories, incidents)
    end_to_end = _metrics(stories, incidents, v4_scorecard)
    clean_sources = set(cast(Sequence[str], cast(Mapping[str, Any], protocol["evaluation"])["clean_sources"]))
    clean_incidents = [item for item in incidents if str(item["label_source"]) in clean_sources]
    clean = _metrics(stories, clean_incidents, v4_scorecard, clean=True)
    funnel = _funnel(
        candidates,
        screen_verdicts,
        investigation_verdicts,
        stories,
        elected_candidate_count=(
            len(elected_candidate_ids) if elected_candidate_ids is not None else None
        ),
    )
    runtime = {
        "screen": _runtime(cast(Sequence[Mapping[str, Any]], screen_verdicts["batches"])),
        "investigation": _runtime(cast(Sequence[Mapping[str, Any]], investigation_verdicts["batches"])),
    }
    runtime["combined_modeled_equivalent_cost_usd"] = float(cast(Mapping[str, Any], runtime["screen"])["modeled_equivalent_cost_usd"]) + float(cast(Mapping[str, Any], runtime["investigation"])["modeled_equivalent_cost_usd"])
    v5_system = cast(Mapping[str, Any], v5_scorecard["end_to_end"])
    v6_system = cast(Mapping[str, Any], v6_scorecard["end_to_end"])
    comparison = {
        "unit_note": "V5/v6 alerts are estimated repository-hours from their frozen sample; v7 reports exact story precision and an exact story-to-hour mapping.",
        "versions": [
            _comparison_row("v5", v5_system, cast(Mapping[str, Any], v5_scorecard.get("runtime") or {})),
            _comparison_row("v6", v6_system, cast(Mapping[str, Any], v6_scorecard.get("runtime") or {})),
            {
                "version": current_version,
                "precision": end_to_end["hour_mapped_precision"],
                "primary_story_precision": end_to_end["story_precision"],
                "incident_recall": end_to_end["incident_recall"],
                "true_positive_hours_killed": end_to_end["true_positive_hours_killed"],
                "median_lead_hours": cast(Mapping[str, Any], end_to_end["lead_time"])["median_hours"],
                "alerts_or_stories": end_to_end["published_stories"],
                "mapped_alert_hours": end_to_end["mapped_alert_hours"],
                "modeled_equivalent_cost_usd": runtime["combined_modeled_equivalent_cost_usd"],
            },
        ],
    }
    result = {
        "schema_version": 1,
        "study_id": protocol["study_id"],
        "protocol_sha256": canonical_sha256(protocol),
        "frozen_identities": dict(cast(Mapping[str, Any], protocol["frozen_identities"])),
        "end_to_end": end_to_end,
        "clean_source_cut": clean,
        "funnel": funnel,
        "candidate_dispositions": _disposition_summary(dispositions),
        "automation_candidate_stage": {
            "dismissed": sum(candidate.get("deterministic_reason_code") == "automation" for candidate in candidates),
            "reached_screen": 0,
            "rule": "All candidates classified as automation are recorded and dismissed before any judge call.",
        },
        "stories": stories,
        "runtime": runtime,
        "comparison_v5_v6_v7": comparison,
        "interpretation": {
            "architecture": (
                "V7 judges signal-scoped claims, fetches heavy evidence only after election, and publishes deduplicated stories."
                if current_version == "v7"
                else "V7.1 reuses the exact v7 signal-scoped screen and corrects only its label-blind election policy before heavy investigation."
            ),
            "full_label_status": "Descriptive because the published v4 comment-source audit found 30% obvious label noise.",
            "clean_source_status": "Confirmatory-grade source cut on a reused, already published test—not a fresh holdout.",
            "no_openai_calls": True,
            "cost_status": "Claude ran with claude.ai subscription auth and API credentials removed; USD is modeled equivalent usage, not API billing.",
        },
    }
    result["finding"] = _finding(result)
    result["scorecard_sha256"] = canonical_sha256(result)
    return result


def _build_stories(findings: Sequence[Mapping[str, Any]], candidates: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for finding in findings:
        candidate = candidates[str(finding["candidate_id"])]
        response = cast(Mapping[str, Any], finding["response"])
        features = cast(Mapping[str, Any], candidate["features"])
        issue_number = features.get("issue_number")
        related = [int(item) for item in cast(Sequence[Any], response.get("related_issue_numbers") or [])]
        story_match = re.fullmatch(r"issue:(\d+)", str(response.get("story_key") or "").strip(), re.I)
        if issue_number is not None:
            token = f"issue:{int(issue_number)}"
        elif story_match and int(story_match.group(1)) in related:
            token = f"issue:{int(story_match.group(1))}"
        elif len(related) == 1:
            token = f"issue:{related[0]}"
        else:
            raw = re.sub(r"[^a-z0-9_.:-]+", "-", str(response.get("story_key") or "").casefold()).strip("-")
            token = raw or str(finding["candidate_id"])
        grouped[(str(candidate["entity"]), token)].append(finding)
    stories = []
    for (entity, token), members in sorted(grouped.items()):
        member_candidates = [candidates[str(item["candidate_id"])] for item in members]
        numbers = set()
        for item, candidate in zip(members, member_candidates):
            features = cast(Mapping[str, Any], candidate["features"])
            if features.get("issue_number") is not None:
                numbers.add(int(features["issue_number"]))
            numbers.update(int(number) for number in cast(Sequence[Any], cast(Mapping[str, Any], item["response"]).get("related_issue_numbers") or []))
        responses = [cast(Mapping[str, Any], item["response"]) for item in members]
        severity = max((str(response["severity"]) for response in responses), key=lambda value: SEVERITY_ORDER[value])
        base = {
            "story_id": "v7story:" + canonical_sha256({"entity": entity, "token": token})[:24],
            "story_key": token,
            "entity": entity,
            "severity": severity,
            "candidate_ids": sorted(str(item["candidate_id"]) for item in members),
            "row_ids": sorted({str(candidate["row_id"]) for candidate in member_candidates}),
            "series": sorted({str(cast(Mapping[str, Any], candidate["identity"])["series"]) for candidate in member_candidates}),
            "issue_numbers": sorted(numbers),
            "earliest_candidate_ts": min(str(candidate["candidate_ts"]) for candidate in member_candidates),
            "summaries": [str(response.get("summary") or "") for response in responses],
            "evidence_quotes": sorted({str(quote) for response in responses for quote in cast(Sequence[Any], response.get("evidence_quotes") or [])}),
            "attribution_status": max((str(response.get("attribution_status") or "no_claim") for response in responses), key=lambda value: {"no_claim": 0, "correlation_only": 1, "verified": 2}[value]),
        }
        stories.append({**base, "story_sha256": canonical_sha256(base)})
    return stories


def _match_stories(stories: list[dict[str, Any]], incidents: Sequence[Mapping[str, Any]]) -> None:
    by_key: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for incident in incidents:
        by_key[(str(incident["entity"]), int(incident["issue_number"]))].append(incident)
    for story in stories:
        earliest = parse_timestamp(str(story["earliest_candidate_ts"]))
        matches = []
        for number in cast(Sequence[int], story["issue_numbers"]):
            for incident in by_key.get((str(story["entity"]), int(number)), []):
                confirmation = parse_timestamp(str(incident["confirmation_ts"]))
                if earliest <= confirmation:
                    matches.append(incident)
        story["matched_incident_ids"] = sorted({str(item["incident_id"]) for item in matches})
        story["matched_incident_row_ids"] = sorted({str(item["row_id"]) for item in matches})
        story["true_positive"] = bool(matches)


def _metrics(stories: Sequence[Mapping[str, Any]], incidents: Sequence[Mapping[str, Any]], v4: Mapping[str, Any], *, clean: bool = False) -> dict[str, Any]:
    incident_ids = {str(item["incident_id"]) for item in incidents}
    matched = {
        incident_id
        for story in stories
        for incident_id in cast(Sequence[str], story["matched_incident_ids"])
        if incident_id in incident_ids
    }
    true_stories = [story for story in stories if set(cast(Sequence[str], story["matched_incident_ids"])) & incident_ids]
    matched_row_ids = {
        str(incident["row_id"])
        for incident in incidents
        if str(incident["incident_id"]) in matched
    }
    false_story_rows = {
        str(row_id)
        for story in stories
        if story not in true_stories
        for row_id in cast(Sequence[str], story["row_ids"])
    }
    mapped_hours = matched_row_ids | false_story_rows
    test = cast(Mapping[str, Any], v4["test"])
    metrics = cast(Mapping[str, Any], test["metrics"])
    positive_hours = (
        len({str(item["row_id"]) for item in incidents})
        if clean
        else int(metrics["true_positive_hours"]) + int(metrics["missed_positive_hours"])
    )
    predicted_ids = {
        str(prediction["row_id"])
        for prediction in cast(Sequence[Mapping[str, Any]], test["predictions"])
        if bool(prediction["predicted"])
    }
    stage1_true_hours = (
        len({str(item["row_id"]) for item in incidents if str(item["row_id"]) in predicted_ids})
        if clean
        else int(metrics["true_positive_hours"])
    )
    lead = []
    story_by_incident = {
        incident_id: story
        for story in true_stories
        for incident_id in cast(Sequence[str], story["matched_incident_ids"])
        if incident_id in incident_ids
    }
    for incident in incidents:
        story = story_by_incident.get(str(incident["incident_id"]))
        if story:
            lead.append((parse_timestamp(str(incident["confirmation_ts"])) - parse_timestamp(str(story["earliest_candidate_ts"]))).total_seconds() / 60)
    repositories = sorted({str(item["entity"]) for item in incidents} | {str(item["entity"]) for item in stories})
    ci_story, ci_recall = _bootstrap(stories, incidents, repositories)
    return {
        "published_stories": len(stories),
        "true_positive_stories": len(true_stories),
        "false_positive_stories": len(stories) - len(true_stories),
        "story_precision": len(true_stories) / len(stories) if stories else 0.0,
        "story_precision_repository_bootstrap_95": ci_story,
        "incidents": len(incidents),
        "incidents_caught": len(matched),
        "incident_recall": len(matched) / len(incidents) if incidents else 0.0,
        "incident_recall_repository_bootstrap_95": ci_recall,
        "mapped_alert_hours": len(mapped_hours),
        "matched_true_positive_hours": len(matched_row_ids),
        "hour_mapped_precision": len(matched_row_ids) / len(mapped_hours) if mapped_hours else 0.0,
        "hour_recall": len(matched_row_ids) / positive_hours if positive_hours else 0.0,
        "true_positive_hours_killed": stage1_true_hours - len(matched_row_ids),
        "alert_hour_reduction_vs_stage1": int(metrics["alerts"]) - len(mapped_hours),
        "lead_time": _lead_summary(lead),
    }


def _bootstrap(stories: Sequence[Mapping[str, Any]], incidents: Sequence[Mapping[str, Any]], repositories: Sequence[str]) -> tuple[list[float], list[float]]:
    if not repositories:
        return [0.0, 0.0], [0.0, 0.0]
    rng = random.Random(20260720)
    precision_values = []
    recall_values = []
    for _ in range(2000):
        sample = Counter(rng.choices(repositories, k=len(repositories)))
        story_total = sum(sample[str(story["entity"])] for story in stories)
        story_true = sum(sample[str(story["entity"])] for story in stories if story["true_positive"])
        incident_total = sum(sample[str(item["entity"])] for item in incidents)
        matched_ids = {incident_id for story in stories for incident_id in cast(Sequence[str], story["matched_incident_ids"])}
        incident_true = sum(sample[str(item["entity"])] for item in incidents if str(item["incident_id"]) in matched_ids)
        precision_values.append(story_true / story_total if story_total else 0.0)
        recall_values.append(incident_true / incident_total if incident_total else 0.0)
    precision_values.sort()
    recall_values.sort()
    return [precision_values[49], precision_values[1949]], [recall_values[49], recall_values[1949]]


def _lead_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "minimum_minutes": None, "median_minutes": None, "median_hours": None, "maximum_minutes": None}
    return {
        "count": len(values),
        "minimum_minutes": min(values),
        "median_minutes": statistics.median(values),
        "median_hours": statistics.median(values) / 60,
        "maximum_minutes": max(values),
    }


def _funnel(candidates: Sequence[Mapping[str, Any]], screen: Mapping[str, Any], investigation: Mapping[str, Any], stories: Sequence[Mapping[str, Any]], *, elected_candidate_count: int | None = None) -> dict[str, Any]:
    screen_records = cast(Sequence[Mapping[str, Any]], screen["records"])
    investigation_records = cast(Sequence[Mapping[str, Any]], investigation["records"])
    counts = {
        "stage1_alert_hours": 1724,
        "scoped_candidates_generated": len(candidates),
        "deterministic_gate_survivors": sum(item["deterministic_disposition"] == "eligible" for item in candidates),
        "screen_elected_investigations": (
            elected_candidate_count
            if elected_candidate_count is not None
            else sum(cast(Mapping[str, Any], item["response"])["disposition"] == "investigate" for item in screen_records)
        ),
        "investigation_findings_before_story_dedup": sum(cast(Mapping[str, Any], item["response"])["disposition"] == "finding" for item in investigation_records),
        "published_stories": len(stories),
    }
    return {
        **counts,
        "compression": {
            "generated_to_gate": counts["scoped_candidates_generated"] / counts["deterministic_gate_survivors"] if counts["deterministic_gate_survivors"] else None,
            "gate_to_investigation": counts["deterministic_gate_survivors"] / counts["screen_elected_investigations"] if counts["screen_elected_investigations"] else None,
            "investigation_to_story": counts["screen_elected_investigations"] / counts["published_stories"] if counts["published_stories"] else None,
        },
        "screen_dispositions": dict(Counter(str(cast(Mapping[str, Any], item["response"])["disposition"]) for item in screen_records)),
        "investigation_dispositions": dict(Counter(str(cast(Mapping[str, Any], item["response"])["disposition"]) for item in investigation_records)),
    }


def _runtime(batches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    costs = []
    wall = []
    models: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for batch in batches:
        runtime = cast(Mapping[str, Any], batch["runtime"])
        costs.append(float(runtime.get("total_cost_usd_equivalent") or 0))
        wall.append(float(runtime.get("wall_duration_ms") or 0))
        for model, usage in cast(Mapping[str, Mapping[str, Any]], runtime.get("model_usage") or {}).items():
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    models[model][key] += float(value)
    return {
        "invocations": len(batches),
        "modeled_equivalent_cost_usd": sum(costs),
        "wall_duration_ms_sum": sum(wall),
        "wall_duration_ms_median": statistics.median(wall) if wall else 0,
        "wall_duration_ms_max": max(wall, default=0),
        "actual_model_usage": {model: dict(values) for model, values in models.items()},
    }


def _disposition_summary(dispositions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(dispositions),
        "by_source": dict(Counter(str(item["source"]) for item in dispositions)),
        "by_disposition": dict(Counter(str(cast(Mapping[str, Any], item["response"])["disposition"]) for item in dispositions)),
        "by_series": dict(Counter(str(item["series"]) for item in dispositions)),
        "all_candidates_accounted_for": len({str(item["candidate_id"]) for item in dispositions}) == len(dispositions),
    }


def _comparison_row(version: str, metrics: Mapping[str, Any], runtime: Mapping[str, Any]) -> dict[str, Any]:
    lead = cast(Mapping[str, Any], metrics["lead_time"])
    return {
        "version": version,
        "precision": metrics["precision"],
        "incident_recall": metrics["incident_recall"],
        "true_positive_hours_killed": metrics["true_positive_hours_killed"],
        "median_lead_hours": lead["median_hours"],
        "alerts_or_stories": metrics.get("estimated_alerts"),
        "modeled_equivalent_cost_usd": runtime.get("modeled_equivalent_cost_usd_total"),
    }


def _finding(result: Mapping[str, Any]) -> dict[str, Any]:
    primary = cast(Mapping[str, Any], result["end_to_end"])
    comparison = cast(Sequence[Mapping[str, Any]], cast(Mapping[str, Any], result["comparison_v5_v6_v7"])["versions"])
    v6 = next(item for item in comparison if item["version"] == "v6")
    current_version = str(comparison[-1]["version"])
    version_name = "Signal-scoped v7" if current_version == "v7" else "Signal-scoped v7.1"
    return {
        "conclusion": (
            f"{version_name} improved end-to-end hourly precision over v6 while preserving at least half of labeled incidents."
            if float(primary["hour_mapped_precision"]) > float(v6["precision"]) and float(primary["incident_recall"]) >= 0.5
            else f"{version_name} did not clear both the precision and recall bar; the frozen result is reported without tuning."
        ),
        "hour_precision_delta_vs_v6": float(primary["hour_mapped_precision"]) - float(v6["precision"]),
        "incident_recall_delta_vs_v6": float(primary["incident_recall"]) - float(v6["incident_recall"]),
    }


def _verify(protocol: Mapping[str, Any], dataset: Mapping[str, Any], v4: Mapping[str, Any], v5: Mapping[str, Any], v6: Mapping[str, Any], screen_requests: Mapping[str, Any], screen_verdicts: Mapping[str, Any], investigation_requests: Mapping[str, Any], investigation_verdicts: Mapping[str, Any], *, inherited_screen: bool = False) -> None:
    protocol_hash = canonical_sha256(protocol)
    frozen = cast(Mapping[str, Any], protocol["frozen_identities"])
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != frozen["v4_test_dataset_content_sha256"]:
        raise RuntimeError("v7 evaluation dataset mismatch")
    if v4["scorecard_sha256"] != frozen["v4_scorecard_sha256"] or v5["scorecard_sha256"] != frozen["v5_scorecard_sha256"] or v6["scorecard_sha256"] != frozen["v6_scorecard_sha256"]:
        raise RuntimeError("v7 comparison scorecard mismatch")
    for document in [investigation_requests, investigation_verdicts]:
        if document.get("protocol_sha256") != protocol_hash:
            raise RuntimeError("v7 artifact protocol mismatch")
    if inherited_screen:
        parent = cast(Mapping[str, Any], protocol["parent_v7"])
        if screen_requests.get("protocol_sha256") != parent["protocol_sha256"] or screen_verdicts.get("protocol_sha256") != parent["protocol_sha256"]:
            raise RuntimeError("v7.1 inherited screen protocol mismatch")
        if screen_requests.get("request_bundle_sha256") != parent["screen_request_bundle_sha256"] or screen_verdicts.get("verdict_bundle_sha256") != parent["screen_verdict_bundle_sha256"]:
            raise RuntimeError("v7.1 inherited screen identity mismatch")
    else:
        for document in [screen_requests, screen_verdicts]:
            if document.get("protocol_sha256") != protocol_hash:
                raise RuntimeError("v7 artifact protocol mismatch")
    if screen_verdicts["request_bundle_sha256"] != screen_requests["request_bundle_sha256"]:
        raise RuntimeError("v7 screen linkage mismatch")
    if investigation_requests["screen_verdict_bundle_sha256"] != screen_verdicts["verdict_bundle_sha256"]:
        raise RuntimeError("v7 phase linkage mismatch")
    if investigation_verdicts["request_bundle_sha256"] != investigation_requests["request_bundle_sha256"]:
        raise RuntimeError("v7 investigation linkage mismatch")
    for document, stamp in [
        (screen_requests, "request_bundle_sha256"),
        (screen_verdicts, "verdict_bundle_sha256"),
        (investigation_requests, "request_bundle_sha256"),
        (investigation_verdicts, "verdict_bundle_sha256"),
    ]:
        if document.get(stamp) != canonical_sha256({key: value for key, value in document.items() if key != stamp}):
            raise RuntimeError(f"invalid v7 {stamp}")
    expected_model = str(cast(Mapping[str, Any], protocol["stage2"])["model"])
    for verdicts in [screen_verdicts, investigation_verdicts]:
        for batch in cast(Sequence[Mapping[str, Any]], verdicts["batches"]):
            usage = cast(Mapping[str, Any], cast(Mapping[str, Any], batch["runtime"]).get("model_usage") or {})
            if expected_model not in usage:
                raise RuntimeError("v7 actual model attribution misses requested Opus model")


def _markdown(result: Mapping[str, Any]) -> str:
    primary = cast(Mapping[str, Any], result["end_to_end"])
    clean = cast(Mapping[str, Any], result["clean_source_cut"])
    funnel = cast(Mapping[str, Any], result["funnel"])
    runtime = cast(Mapping[str, Any], result["runtime"])
    rows = cast(Sequence[Mapping[str, Any]], cast(Mapping[str, Any], result["comparison_v5_v6_v7"])["versions"])
    comparison = "\n".join(
        f"| {row['version']} | {float(row['precision']):.1%} | {float(row['incident_recall']):.1%} | {int(row['true_positive_hours_killed'])} | {float(row['median_lead_hours']):.1f} h |"
        for row in rows
    )
    return f"""# Sentinel v7 signal-scoped scorecard

V7 freezes one signal claim per candidate, screens every eligible claim compactly,
fetches heavy evidence only for elected investigations, and publishes deduplicated stories.

## Result

- Published stories: **{primary['published_stories']}**
- Story precision: **{primary['story_precision']:.1%}**
- Hour-mapped precision: **{primary['hour_mapped_precision']:.1%}**
- Incident recall: **{primary['incident_recall']:.1%}**
- True stage-one alert-hours killed: **{primary['true_positive_hours_killed']}**
- Median lead: **{cast(Mapping[str, Any], primary['lead_time'])['median_hours']:.1f} hours**
- Clean-source story precision / incident recall: **{clean['story_precision']:.1%} / {clean['incident_recall']:.1%}**

| Version | Hour precision | Incident recall | True hours killed | Median lead |
|---|---:|---:|---:|---:|
{comparison}

## Funnel

`{funnel['stage1_alert_hours']} stage-one hours → {funnel['scoped_candidates_generated']} scoped claims → {funnel['deterministic_gate_survivors']} screened → {funnel['screen_elected_investigations']} investigated → {funnel['published_stories']} stories`

All {cast(Mapping[str, Any], result['candidate_dispositions'])['total']} generated candidates have recorded dispositions. Automation dismissed at the candidate gate: {cast(Mapping[str, Any], result['automation_candidate_stage'])['dismissed']}.

## Runtime and integrity

- Claude subscription invocations: {cast(Mapping[str, Any], runtime['screen'])['invocations']} screen + {cast(Mapping[str, Any], runtime['investigation'])['invocations']} investigation
- Modeled equivalent usage: ${runtime['combined_modeled_equivalent_cost_usd']:.2f} (not API billing)
- OpenAI calls: zero
- Protocol SHA-256: `{result['protocol_sha256']}`
- Scorecard SHA-256: `{result['scorecard_sha256']}`

This reuses the published v4 test. The full-label result is descriptive because
the v4 label audit found 30% obvious noise in its comment-derived source; the
clean-source cut is reported separately. No labels entered either judge phase.
"""


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
