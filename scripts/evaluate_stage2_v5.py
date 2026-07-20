#!/usr/bin/env python3
"""Evaluate the frozen two-stage v5 system and publish its scorecard."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json

DEFAULT_PROTOCOL = Path("holdout/v5/protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_V4_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_V4_AUDIT = Path("holdout/v4/label-audit-test.json")
DEFAULT_REQUESTS = Path("holdout/v5/requests.json.gz")
DEFAULT_VERDICTS = Path("holdout/v5/verdicts.json.gz")
DEFAULT_SCORECARD = Path("holdout/v5/scorecard.json")
DEFAULT_MARKDOWN = Path("holdout/v5/SCORECARD-v5.md")
CLEAN_SOURCES = frozenset({"completed_bug_closure", "merged_fixing_pr"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--v4-scorecard", type=Path, default=DEFAULT_V4_SCORECARD)
    parser.add_argument("--v4-audit", type=Path, default=DEFAULT_V4_AUDIT)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--verdicts", type=Path, default=DEFAULT_VERDICTS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    dataset = read_gzip_json(args.dataset)
    v4_scorecard = _read_object(args.v4_scorecard)
    audit = _read_object(args.v4_audit)
    requests = read_gzip_json(args.requests)
    verdicts = read_gzip_json(args.verdicts)
    scorecard = evaluate(
        protocol=protocol,
        dataset=dataset,
        v4_scorecard=v4_scorecard,
        audit=audit,
        requests=requests,
        verdicts=verdicts,
    )
    args.scorecard.parent.mkdir(parents=True, exist_ok=True)
    args.scorecard.write_text(
        json.dumps(scorecard, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(scorecard), encoding="utf-8")
    system = cast(Mapping[str, Any], scorecard["end_to_end"])
    print(
        "V5_SYSTEM "
        f"precision={_number(system['precision'])} "
        f"hour_recall={_number(system['hour_recall'])} "
        f"incident_recall={_number(system['incident_recall'])} "
        f"estimated_alerts={_number(system['estimated_alerts'])} "
        f"true_alerts_killed={system['true_positive_hours_killed']} "
        f"scorecard_sha256={scorecard['scorecard_sha256']}"
    )


def evaluate(
    *,
    protocol: Mapping[str, Any],
    dataset: Mapping[str, Any],
    v4_scorecard: Mapping[str, Any],
    audit: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> dict[str, Any]:
    _verify_inputs(protocol, dataset, v4_scorecard, requests, verdicts)
    rows = list(cast(Sequence[Mapping[str, Any]], dataset["rows"]))
    incidents = list(
        cast(Sequence[Mapping[str, Any]], dataset["positive_incidents"])
    )
    predictions = list(
        cast(
            Sequence[Mapping[str, Any]],
            cast(Mapping[str, Any], v4_scorecard["test"])["predictions"],
        )
    )
    row_by_id = {str(row["row_id"]): row for row in rows}
    prediction_by_id = {str(row["row_id"]): row for row in predictions}
    record_by_id = {
        str(record["row_id"]): record
        for record in cast(Sequence[Mapping[str, Any]], verdicts["records"])
    }
    sample_rows = list(
        cast(
            Sequence[Mapping[str, Any]],
            cast(Mapping[str, Any], protocol["sampling"])["rows"],
        )
    )
    sample_by_id = {str(row["row_id"]): row for row in sample_rows}
    survives = {
        row_id: bool(cast(Mapping[str, Any], record["response"])["real"])
        for row_id, record in record_by_id.items()
    }
    clean_incidents = [
        incident
        for incident in incidents
        if str(incident["label_source"]) in CLEAN_SOURCES
    ]
    clean_row_ids = {str(incident["row_id"]) for incident in clean_incidents}
    alert_positive_ids = {
        row_id
        for row_id, prediction in prediction_by_id.items()
        if bool(prediction["predicted"]) and int(row_by_id[row_id]["label"]) == 1
    }
    sampled_negative_ids = {
        str(sample["row_id"])
        for sample in sample_rows
        if str(sample["stratum"]).startswith("negative:")
    }
    surviving_positive_ids = {row_id for row_id in alert_positive_ids if survives[row_id]}
    estimated_false_positives = math.fsum(
        sorted(
            float(sample_by_id[row_id]["analysis_weight"])
            for row_id in sampled_negative_ids
            if survives[row_id]
        )
    )
    true_positive_hours = len(surviving_positive_ids)
    estimated_alerts = true_positive_hours + estimated_false_positives
    total_positive_hours = sum(int(row["label"]) == 1 for row in rows)
    v4_metrics = cast(
        Mapping[str, Any], cast(Mapping[str, Any], v4_scorecard["test"])["metrics"]
    )
    caught_incidents = [
        incident
        for incident in incidents
        if str(incident["row_id"]) in surviving_positive_ids
    ]
    clean_true_positive_ids = surviving_positive_ids & clean_row_ids
    surviving_non_clean_positive_ids = surviving_positive_ids - clean_row_ids
    clean_false_positives = estimated_false_positives + len(
        surviving_non_clean_positive_ids
    )
    clean_alerts = len(clean_true_positive_ids) + clean_false_positives
    caught_clean_incidents = [
        incident
        for incident in clean_incidents
        if str(incident["row_id"]) in surviving_positive_ids
    ]
    bootstrap = _bootstrap(
        rows=rows,
        incidents=incidents,
        repositories=list(
            cast(
                Sequence[str],
                cast(Mapping[str, Any], v4_scorecard["coverage"])[
                    "test_repository_names"
                ],
            )
        ),
        alert_positive_ids=alert_positive_ids,
        clean_row_ids=clean_row_ids,
        sample_rows=sample_rows,
        survives=survives,
        replicates=int(
            cast(Mapping[str, Any], cast(Mapping[str, Any], protocol["evaluation"])["bootstrap"])[
                "replicates"
            ]
        ),
        seed=int(
            cast(Mapping[str, Any], cast(Mapping[str, Any], protocol["evaluation"])["bootstrap"])[
                "seed"
            ]
        ),
    )
    per_source = _source_breakdown(
        incidents=incidents,
        prediction_by_id=prediction_by_id,
        survives=survives,
    )
    runtime = _runtime_summary(
        cast(Mapping[str, Any], verdicts["batch"]),
        candidate_count=len(record_by_id),
    )
    request_summary = _request_summary(requests)
    llvmbot = _llvmbot_slice(
        audit=audit,
        incidents=incidents,
        prediction_by_id=prediction_by_id,
        survives=survives,
    )
    stage1 = {
        "candidate_hours": cast(Mapping[str, Any], v4_scorecard["power"])[
            "test_rows"
        ],
        "alerts": v4_metrics["alerts"],
        "precision": v4_metrics["precision"],
        "hour_recall": v4_metrics["recall"],
        "incident_recall": cast(Mapping[str, Any], v4_scorecard["test"])[
            "incident_recall"
        ],
        "true_positive_hours": v4_metrics["true_positive_hours"],
        "false_positive_hours": v4_metrics["false_positive_hours"],
    }
    end_to_end = {
        "estimated_alerts": estimated_alerts,
        "estimated_alert_reduction": float(v4_metrics["alerts"]) - estimated_alerts,
        "estimated_alert_reduction_fraction": 1 - estimated_alerts / float(v4_metrics["alerts"]),
        "true_positive_hours": true_positive_hours,
        "estimated_false_positive_hours": estimated_false_positives,
        "precision": _ratio(true_positive_hours, estimated_alerts),
        "precision_repository_bootstrap_95": bootstrap["precision"],
        "hour_recall": _ratio(true_positive_hours, total_positive_hours),
        "hour_recall_repository_bootstrap_95": bootstrap["hour_recall"],
        "stage2_true_alert_survival": _ratio(
            true_positive_hours, len(alert_positive_ids)
        ),
        "true_positive_hours_killed": len(alert_positive_ids) - true_positive_hours,
        "incidents_caught": len(caught_incidents),
        "incidents_killed_by_stage2": int(
            len(
                cast(
                    Sequence[Mapping[str, Any]],
                    cast(Mapping[str, Any], v4_scorecard["test"])[
                        "caught_incidents"
                    ],
                )
            )
        )
        - len(caught_incidents),
        "incident_recall": _ratio(len(caught_incidents), len(incidents)),
        "incident_recall_repository_bootstrap_95": bootstrap["incident_recall"],
        "lead_time": _lead_summary(
            [float(incident["lead_minutes"]) for incident in caught_incidents]
        ),
    }
    clean = {
        "sources": sorted(CLEAN_SOURCES),
        "positive_hours": len(clean_row_ids),
        "true_positive_hours": len(clean_true_positive_ids),
        "estimated_alerts": clean_alerts,
        "estimated_false_positive_hours": clean_false_positives,
        "precision": _ratio(len(clean_true_positive_ids), clean_alerts),
        "precision_repository_bootstrap_95": bootstrap["clean_precision"],
        "hour_recall": _ratio(len(clean_true_positive_ids), len(clean_row_ids)),
        "hour_recall_repository_bootstrap_95": bootstrap["clean_hour_recall"],
        "incidents": len(clean_incidents),
        "incidents_caught": len(caught_clean_incidents),
        "incident_recall": _ratio(len(caught_clean_incidents), len(clean_incidents)),
        "incident_recall_repository_bootstrap_95": bootstrap[
            "clean_incident_recall"
        ],
    }
    base: dict[str, Any] = {
        "schema_version": 1,
        "study_id": protocol["study_id"],
        "frozen_identities": {
            "protocol_sha256": canonical_sha256(protocol),
            "request_bundle_sha256": requests["request_bundle_sha256"],
            "verdict_bundle_sha256": verdicts["verdict_bundle_sha256"],
            "v4_scorecard_sha256": v4_scorecard["scorecard_sha256"],
            "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])[
                "dataset_content_sha256"
            ],
            "provider": cast(Mapping[str, Any], protocol["stage2"])["provider"],
            "requested_model": cast(Mapping[str, Any], protocol["stage2"])["model"],
            "transport": cast(Mapping[str, Any], protocol["stage2"])["transport"],
        },
        "stage1": stage1,
        "end_to_end": end_to_end,
        "clean_source_cut": clean,
        "per_label_source": per_source,
        "known_llvmbot_label_noise_slice": llvmbot,
        "sampling": {
            key: cast(Mapping[str, Any], protocol["sampling"])[key]
            for key in [
                "method",
                "stage1_alert_population",
                "positive_alert_census",
                "negative_alert_population",
                "negative_alert_sample",
                "total_stage2_requests",
                "sample_sha256",
            ]
        },
        "requests": request_summary,
        "runtime": runtime,
        "interpretation": {
            "architecture": (
                "Stage 1 is the unchanged v4 high-recall ranker; Stage 2 is the "
                "frozen Claude precision filter invoked once for the complete scheduled-run "
                "candidate batch. Only structured real=true survives."
            ),
            "full_label_status": (
                "Descriptive, not confirmatory: the v4 held-out audit found 30% "
                "obvious noise in substantive_maintainer_comment labels."
            ),
            "clean_source_status": (
                "Confirmatory-grade label-source cut using completed bug closures "
                "and merged fixing PRs; still not a fresh holdout because v5 reuses "
                "the published v4 test split."
            ),
            "cost_status": (
                "Claude ran through claude.ai subscription authentication with API "
                "keys removed. Reported USD is the CLI's modeled equivalent, not a "
                "claim of per-call API billing."
            ),
            "no_openai_calls": True,
        },
    }
    return {**base, "scorecard_sha256": canonical_sha256(base)}


def _verify_inputs(
    protocol: Mapping[str, Any],
    dataset: Mapping[str, Any],
    v4_scorecard: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> None:
    protocol_hash = canonical_sha256(protocol)
    if requests.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("v5 requests do not match protocol")
    if verdicts.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("v5 verdicts do not match protocol")
    if verdicts.get("request_bundle_sha256") != requests.get(
        "request_bundle_sha256"
    ):
        raise RuntimeError("v5 verdicts do not match requests")
    for document, stamp in [
        (requests, "request_bundle_sha256"),
        (verdicts, "verdict_bundle_sha256"),
    ]:
        unstamped = {key: value for key, value in document.items() if key != stamp}
        if document.get(stamp) != canonical_sha256(unstamped):
            raise RuntimeError(f"invalid {stamp}")
    identities = cast(Mapping[str, Any], protocol["v4_frozen_identities"])
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != identities[
        "test_dataset_content_sha256"
    ]:
        raise RuntimeError("v5 dataset does not match frozen v4 test")
    if v4_scorecard.get("scorecard_sha256") != identities["v4_scorecard_sha256"]:
        raise RuntimeError("v5 v4 scorecard identity mismatch")
    stage2 = cast(Mapping[str, Any], protocol["stage2"])
    expected_model = str(stage2["model"])
    request_rows = cast(Sequence[Mapping[str, Any]], requests["requests"])
    record_rows = cast(Sequence[Mapping[str, Any]], verdicts["records"])
    if len(request_rows) != int(cast(Mapping[str, Any], protocol["sampling"])["total_stage2_requests"]):
        raise RuntimeError("v5 request count differs from frozen sample")
    if len(record_rows) != len(request_rows):
        raise RuntimeError("v5 verdict bundle is incomplete")
    batch = cast(Mapping[str, Any], verdicts["batch"])
    batch_unstamped = {
        key: value for key, value in batch.items() if key != "record_sha256"
    }
    reconstructed_batch = {**batch_unstamped, "records": list(record_rows)}
    if batch.get("record_sha256") != canonical_sha256(reconstructed_batch):
        raise RuntimeError("invalid v5 batch record hash")
    if batch.get("provider") != stage2["provider"] or batch.get(
        "requested_model"
    ) != expected_model:
        raise RuntimeError("v5 batch provider/model attribution mismatch")
    model_usage = cast(
        Mapping[str, Any], cast(Mapping[str, Any], batch["runtime"])["model_usage"]
    )
    if expected_model not in model_usage:
        raise RuntimeError(
            f"v5 primary model attribution is {sorted(model_usage)}, expected {expected_model}"
        )
    if int(verdicts.get("invocation_count") or 0) != 1:
        raise RuntimeError("v5 verdicts must come from one batched invocation")
    frozen_batch = cast(Mapping[str, Any], requests["batch_request"])
    if batch.get("batch_request_hash") != frozen_batch.get("batch_request_hash"):
        raise RuntimeError("v5 verdict batch request identity mismatch")
    for record in record_rows:
        unstamped = {
            key: value for key, value in record.items() if key != "record_sha256"
        }
        if record.get("record_sha256") != canonical_sha256(unstamped):
            raise RuntimeError(f"invalid v5 record hash for {record.get('row_id')}")


def _source_breakdown(
    *,
    incidents: Sequence[Mapping[str, Any]],
    prediction_by_id: Mapping[str, Mapping[str, Any]],
    survives: Mapping[str, bool],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in sorted({str(incident["label_source"]) for incident in incidents}):
        population = [
            incident for incident in incidents if str(incident["label_source"]) == source
        ]
        stage1 = [
            incident
            for incident in population
            if bool(prediction_by_id[str(incident["row_id"])]["predicted"])
        ]
        stage2 = [
            incident
            for incident in stage1
            if survives[str(incident["row_id"])]
        ]
        result[source] = {
            "incidents": len(population),
            "stage1_caught": len(stage1),
            "stage1_recall": _ratio(len(stage1), len(population)),
            "end_to_end_caught": len(stage2),
            "end_to_end_recall": _ratio(len(stage2), len(population)),
            "stage2_survival_of_stage1_catches": _ratio(len(stage2), len(stage1)),
        }
    return result


def _llvmbot_slice(
    *,
    audit: Mapping[str, Any],
    incidents: Sequence[Mapping[str, Any]],
    prediction_by_id: Mapping[str, Mapping[str, Any]],
    survives: Mapping[str, bool],
) -> dict[str, Any]:
    reviews = [
        review
        for review in cast(Sequence[Mapping[str, Any]], audit["reviews"])
        if review.get("status") == "rejected"
        and "llvmbot" in str(review.get("reason") or "").casefold()
    ]
    incident_ids = [str(review["incident_id"]) for review in reviews]
    row_by_incident = {
        str(incident["incident_id"]): str(incident["row_id"])
        for incident in incidents
    }
    row_ids = {row_by_incident[incident_id] for incident_id in incident_ids}
    stage1_rows = {
        row_id for row_id in row_ids if bool(prediction_by_id[row_id]["predicted"])
    }
    return {
        "audited_rejected_incidents": len(incident_ids),
        "incident_ids": incident_ids,
        "stage1_alert_rows": len(stage1_rows),
        "stage2_rejected_rows": sum(not survives[row_id] for row_id in stage1_rows),
    }


def _runtime_summary(
    batch: Mapping[str, Any], *, candidate_count: int
) -> dict[str, Any]:
    runtime = cast(Mapping[str, Any], batch["runtime"])
    wall = float(runtime["wall_duration_ms"])
    cost = float(runtime.get("total_cost_usd_equivalent") or 0)
    input_tokens = int(runtime.get("input_tokens") or 0)
    output_tokens = int(runtime.get("output_tokens") or 0)
    actual_models: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
            "modeled_equivalent_cost_usd": 0.0,
        }
    )
    for model, usage in cast(
        Mapping[str, Mapping[str, Any]], runtime.get("model_usage") or {}
    ).items():
        summary = actual_models[model]
        summary["calls"] += 1
        summary["input_tokens"] += int(usage.get("inputTokens") or 0)
        summary["cache_creation_input_tokens"] += int(
            usage.get("cacheCreationInputTokens") or 0
        )
        summary["cache_read_input_tokens"] += int(
            usage.get("cacheReadInputTokens") or 0
        )
        summary["output_tokens"] += int(usage.get("outputTokens") or 0)
        summary["modeled_equivalent_cost_usd"] += float(usage.get("costUSD") or 0)
    return {
        "invocations": 1,
        "candidates": candidate_count,
        "wall_duration_ms": wall,
        "target_wall_duration_ms": 300_000,
        "completed_within_five_minutes": wall <= 300_000,
        "modeled_equivalent_cost_usd_total": cost,
        "modeled_equivalent_cost_usd_amortized_per_candidate": _ratio(
            cost, candidate_count
        ),
        "input_tokens_total": input_tokens,
        "output_tokens_total": output_tokens,
        "actual_model_usage": dict(sorted(actual_models.items())),
    }


def _request_summary(document: Mapping[str, Any]) -> dict[str, Any]:
    requests = cast(Sequence[Mapping[str, Any]], document["requests"])
    chars = [
        len(str(cast(Mapping[str, Any], request["identity"])["user_content"]))
        for request in requests
    ]
    event_counts = [
        int(cast(Mapping[str, Any], request["evidence_manifest"])["supplied_event_count"])
        for request in requests
    ]
    event_chars = [
        int(cast(Mapping[str, Any], request["evidence_manifest"])["supplied_event_json_chars"])
        for request in requests
    ]
    return {
        "count": len(requests),
        "invocation_count": int(document["invocation_count"]),
        "batch_user_content_chars": int(document["batch_user_content_chars"]),
        "approximate_batch_user_tokens_at_four_chars_per_token": round(
            int(document["batch_user_content_chars"]) / 4
        ),
        "user_content_chars_total": sum(chars),
        "user_content_chars_per_candidate": _distribution(chars),
        "approximate_user_tokens_total_at_four_chars_per_token": round(sum(chars) / 4),
        "supplied_events_per_candidate": _distribution(event_counts),
        "supplied_event_json_chars_per_candidate": _distribution(event_chars),
    }


def _bootstrap(
    *,
    rows: Sequence[Mapping[str, Any]],
    incidents: Sequence[Mapping[str, Any]],
    repositories: Sequence[str],
    alert_positive_ids: set[str],
    clean_row_ids: set[str],
    sample_rows: Sequence[Mapping[str, Any]],
    survives: Mapping[str, bool],
    replicates: int,
    seed: int,
) -> dict[str, list[float]]:
    rows_by_repo: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    alert_positive_by_repo: dict[str, list[str]] = defaultdict(list)
    incidents_by_repo: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    negatives_by_repo_stratum: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_repo[str(row["entity"])].append(row)
        row_id = str(row["row_id"])
        if row_id in alert_positive_ids:
            alert_positive_by_repo[str(row["entity"])].append(row_id)
    for incident in incidents:
        incidents_by_repo[str(incident["entity"])].append(incident)
    for sample in sample_rows:
        if str(sample["stratum"]).startswith("negative:"):
            negatives_by_repo_stratum[(str(sample["entity"]), str(sample["stratum"]))].append(sample)
    rng = random.Random(seed)
    values: dict[str, list[float]] = defaultdict(list)
    for _ in range(replicates):
        counts: Counter[str] = Counter(rng.choice(list(repositories)) for _ in repositories)
        full_tp = full_positive_total = 0.0
        clean_tp = clean_positive_total = 0.0
        full_fp = non_clean_positive_survivors = 0.0
        caught_incidents = total_incidents = 0.0
        caught_clean_incidents = total_clean_incidents = 0.0
        for repo, multiplier in counts.items():
            repo_rows = rows_by_repo[repo]
            positive_rows = [row for row in repo_rows if int(row["label"]) == 1]
            full_positive_total += multiplier * len(positive_rows)
            clean_positive_total += multiplier * sum(
                str(row["row_id"]) in clean_row_ids for row in repo_rows
            )
            for row_id in alert_positive_by_repo[repo]:
                if not survives[row_id]:
                    continue
                full_tp += multiplier
                if row_id in clean_row_ids:
                    clean_tp += multiplier
                else:
                    non_clean_positive_survivors += multiplier
            repo_incidents = incidents_by_repo[repo]
            total_incidents += multiplier * len(repo_incidents)
            total_clean_incidents += multiplier * sum(
                str(incident["label_source"]) in CLEAN_SOURCES
                for incident in repo_incidents
            )
            caught_incidents += multiplier * sum(
                str(incident["row_id"]) in alert_positive_ids
                and survives[str(incident["row_id"])]
                for incident in repo_incidents
            )
            caught_clean_incidents += multiplier * sum(
                str(incident["label_source"]) in CLEAN_SOURCES
                and str(incident["row_id"]) in alert_positive_ids
                and survives[str(incident["row_id"])]
                for incident in repo_incidents
            )
            for (negative_repo, _), sample in negatives_by_repo_stratum.items():
                if negative_repo != repo:
                    continue
                population_size = int(sample[0]["population_size"])
                sample_size = len(sample)
                resampled = [rng.choice(sample) for _ in range(sample_size)]
                full_fp += multiplier * (population_size / sample_size) * sum(
                    survives[str(item["row_id"])] for item in resampled
                )
        values["precision"].append(_ratio(full_tp, full_tp + full_fp) or 0.0)
        values["hour_recall"].append(_ratio(full_tp, full_positive_total) or 0.0)
        values["incident_recall"].append(_ratio(caught_incidents, total_incidents) or 0.0)
        clean_fp = full_fp + non_clean_positive_survivors
        values["clean_precision"].append(_ratio(clean_tp, clean_tp + clean_fp) or 0.0)
        values["clean_hour_recall"].append(_ratio(clean_tp, clean_positive_total) or 0.0)
        values["clean_incident_recall"].append(
            _ratio(caught_clean_incidents, total_clean_incidents) or 0.0
        )
    return {name: _quantile_95(metric_values) for name, metric_values in values.items()}


def _lead_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median_minutes": None, "median_hours": None}
    return {
        "count": len(values),
        "median_minutes": statistics.median(values),
        "median_hours": statistics.median(values) / 60,
        "minimum_minutes": min(values),
        "maximum_minutes": max(values),
    }


def _distribution(values: Sequence[float | int]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    return {
        "count": len(ordered),
        "mean": statistics.mean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "max": max(ordered),
    }


def _quantile_95(values: Sequence[float]) -> list[float]:
    ordered = sorted(values)
    return [
        ordered[int(0.025 * (len(ordered) - 1))],
        ordered[int(0.975 * (len(ordered) - 1))],
    ]


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def render_markdown(scorecard: Mapping[str, Any]) -> str:
    stage1 = cast(Mapping[str, Any], scorecard["stage1"])
    system = cast(Mapping[str, Any], scorecard["end_to_end"])
    clean = cast(Mapping[str, Any], scorecard["clean_source_cut"])
    runtime = cast(Mapping[str, Any], scorecard["runtime"])
    requests = cast(Mapping[str, Any], scorecard["requests"])
    lines = [
        "# Sentinel v5 — two-stage held-out result",
        "",
        "> Stage 1 is the unchanged v4 high-recall ranker. Stage 2 is a frozen Claude Opus precision filter over compact, strictly pre-candidate evidence.",
        "",
        "## End-to-end result",
        "",
        "| System | Alerts | Precision | Hour recall | Incident recall |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Stage 1 only | {stage1['alerts']:,} | {_percent(stage1['precision'])} | {_percent(stage1['hour_recall'])} | {_percent(stage1['incident_recall'])} |",
        f"| Stage 1 + Stage 2 | {_number(system['estimated_alerts'])} estimated | {_percent(system['precision'])} | {_percent(system['hour_recall'])} | {_percent(system['incident_recall'])} |",
        "",
        f"Stage 2 removed an estimated {_number(system['estimated_alert_reduction'])} of {stage1['alerts']:,} candidates ({_percent(system['estimated_alert_reduction_fraction'])}). It killed **{system['true_positive_hours_killed']}** of Stage 1's {stage1['true_positive_hours']} true alert-hours and **{system['incidents_killed_by_stage2']}** of its caught incidents.",
        "",
        f"Surviving incidents retain a median lead of {_number(cast(Mapping[str, Any], system['lead_time'])['median_hours'])} hours before confirmation.",
        "",
        "## Clean-source cut",
        "",
        f"Against completed bug closures and merged fixing PRs only: **{_percent(clean['precision'])} precision**, **{_percent(clean['hour_recall'])} hour recall**, and **{_percent(clean['incident_recall'])} incident recall**. This removes the noisy maintainer-comment label source from the positive class.",
        "",
        "## Label-source recall",
        "",
        "| Confirmation source | Incidents | Stage 1 recall | End-to-end recall | Stage 2 survival |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for source, result in cast(Mapping[str, Mapping[str, Any]], scorecard["per_label_source"]).items():
        lines.append(
            f"| `{source}` | {result['incidents']} | {_percent(result['stage1_recall'])} | {_percent(result['end_to_end_recall'])} | {_percent(result['stage2_survival_of_stage1_catches'])} |"
        )
    lines.extend(
        [
            "",
            "## Compact request and runtime envelope",
            "",
            f"The frozen study judged {requests['count']} candidates in **one agent invocation**: every true Stage 1 alert plus a deterministic inverse-probability-weighted sample of negative alerts. The batch was {_number(requests['batch_user_content_chars'])} characters total; each candidate supplied a median of {_number(cast(Mapping[str, Any], requests['supplied_events_per_candidate'])['median'])} events. Full 24-hour source windows were hashed, never sent.",
            "",
            f"Claude CLI reported {_number(runtime['modeled_equivalent_cost_usd_total'])} USD of modeled equivalent usage. The complete batch finished in {_number(float(runtime['wall_duration_ms']) / 1000)} seconds (five-minute target met: `{str(runtime['completed_within_five_minutes']).lower()}`). The run used claude.ai subscription auth; this is not an API billing claim, and no OpenAI calls were made.",
            "",
            "## Interpretation limits",
            "",
            "The full-label result is descriptive because the v4 held-out audit found 30% obvious noise in `substantive_maintainer_comment`. The clean-source cut uses the two reliable confirmation sources. V5 reuses the already-published v4 test split, so neither result is a fresh untouched-holdout claim.",
            "",
            f"Artifacts are frozen by scorecard SHA-256 `{scorecard['scorecard_sha256']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{100 * float(value):.1f}%"


def _number(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.3f}".rstrip("0").rstrip(".")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


if __name__ == "__main__":
    main()
