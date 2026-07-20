#!/usr/bin/env python3
"""Evaluate rich-evidence v6 batches and publish the end-to-end scorecard."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json

V5_EVALUATOR_PATH = Path(__file__).with_name("evaluate_stage2_v5.py")
SPEC = importlib.util.spec_from_file_location("sentinel_evaluate_stage2_v5", V5_EVALUATOR_PATH)
assert SPEC and SPEC.loader
V5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V5)

DEFAULT_PROTOCOL = Path("holdout/v6/protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_V4_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_V4_AUDIT = Path("holdout/v4/label-audit-test.json")
DEFAULT_V5_SCORECARD = Path("holdout/v5/scorecard.json")
DEFAULT_REQUESTS = Path("holdout/v6/requests.json.gz")
DEFAULT_VERDICTS = Path("holdout/v6/verdicts.json.gz")
DEFAULT_SCORECARD = Path("holdout/v6/scorecard.json")
DEFAULT_MARKDOWN = Path("holdout/v6/SCORECARD-v6.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--v4-scorecard", type=Path, default=DEFAULT_V4_SCORECARD)
    parser.add_argument("--v4-audit", type=Path, default=DEFAULT_V4_AUDIT)
    parser.add_argument("--v5-scorecard", type=Path, default=DEFAULT_V5_SCORECARD)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--verdicts", type=Path, default=DEFAULT_VERDICTS)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    dataset = read_gzip_json(args.dataset)
    v4_scorecard = _read_object(args.v4_scorecard)
    audit = _read_object(args.v4_audit)
    v5_scorecard = _read_object(args.v5_scorecard)
    requests = read_gzip_json(args.requests)
    verdicts = read_gzip_json(args.verdicts)
    scorecard = evaluate(
        protocol=protocol,
        dataset=dataset,
        v4_scorecard=v4_scorecard,
        audit=audit,
        v5_scorecard=v5_scorecard,
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
        "V6_SYSTEM "
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
    v5_scorecard: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> dict[str, Any]:
    _verify_v6(protocol, requests, verdicts)
    protocol_like, requests_like, verdicts_like = _single_batch_adapter(
        protocol, requests, verdicts
    )
    result = V5.evaluate(
        protocol=protocol_like,
        dataset=dataset,
        v4_scorecard=v4_scorecard,
        audit=audit,
        requests=requests_like,
        verdicts=verdicts_like,
    )
    result.pop("scorecard_sha256", None)
    result["schema_version"] = 1
    result["study_id"] = protocol["study_id"]
    result["frozen_identities"] = {
        "protocol_sha256": canonical_sha256(protocol),
        "request_bundle_sha256": requests["request_bundle_sha256"],
        "verdict_bundle_sha256": verdicts["verdict_bundle_sha256"],
        "v4_scorecard_sha256": v4_scorecard["scorecard_sha256"],
        "v5_scorecard_sha256": v5_scorecard["scorecard_sha256"],
        "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"],
        "provider": protocol["stage2"]["provider"],
        "requested_model": protocol["stage2"]["model"],
        "transport": protocol["stage2"]["transport"],
    }
    result["runtime"] = _runtime_summary(
        cast(Sequence[Mapping[str, Any]], verdicts["batches"])
    )
    result["requests"] = _request_summary(requests)
    result["automation_suspect_slice"] = _automation_slice(
        dataset=dataset,
        requests=cast(Sequence[Mapping[str, Any]], requests["requests"]),
        records=cast(Sequence[Mapping[str, Any]], verdicts["records"]),
    )
    v5_system = cast(Mapping[str, Any], v5_scorecard["end_to_end"])
    system = cast(Mapping[str, Any], result["end_to_end"])
    result["v5_evidence_starved_baseline"] = {
        "scorecard_sha256": v5_scorecard["scorecard_sha256"],
        "precision": v5_system["precision"],
        "hour_recall": v5_system["hour_recall"],
        "incident_recall": v5_system["incident_recall"],
        "true_positive_hours_killed": v5_system["true_positive_hours_killed"],
        "median_evidence_events": cast(Mapping[str, Any], v5_scorecard["requests"])["supplied_events_per_candidate"]["median"],
        "precision_delta_v6": float(system["precision"]) - float(v5_system["precision"]),
        "hour_recall_delta_v6": float(system["hour_recall"]) - float(v5_system["hour_recall"]),
        "incident_recall_delta_v6": float(system["incident_recall"]) - float(v5_system["incident_recall"]),
    }
    result["interpretation"] = {
        "architecture": (
            "The frozen v4 high-recall ranker feeds complete scheduled-size candidate "
            "batches to Claude with rich, strictly pre-cutoff signal packs."
        ),
        "full_label_status": (
            "Descriptive: the v4 held-out audit found 30% obvious noise in the "
            "substantive maintainer-comment source."
        ),
        "clean_source_status": (
            "Confirmatory-grade label-source cut, but not a fresh holdout because v6 "
            "reuses the published v4 test and v5 sample."
        ),
        "no_openai_calls": True,
        "cost_status": (
            "Claude ran through claude.ai subscription auth with API credentials "
            "removed. USD is modeled equivalent usage, not API billing."
        ),
    }
    result["finding"] = _finding(result, v5_scorecard)
    result["scorecard_sha256"] = canonical_sha256(result)
    return result


def _verify_v6(
    protocol: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> None:
    protocol_hash = canonical_sha256(protocol)
    if requests.get("protocol_sha256") != protocol_hash or verdicts.get("protocol_sha256") != protocol_hash:
        raise RuntimeError("v6 protocol identity mismatch")
    for document, stamp in [
        (requests, "request_bundle_sha256"),
        (verdicts, "verdict_bundle_sha256"),
    ]:
        unstamped = {key: value for key, value in document.items() if key != stamp}
        if document.get(stamp) != canonical_sha256(unstamped):
            raise RuntimeError(f"invalid {stamp}")
    if verdicts.get("request_bundle_sha256") != requests.get("request_bundle_sha256"):
        raise RuntimeError("v6 verdicts differ from request bundle")
    request_batches = cast(Sequence[Mapping[str, Any]], requests["batches"])
    verdict_batches = cast(Sequence[Mapping[str, Any]], verdicts["batches"])
    records = {
        str(record["row_id"]): record
        for record in cast(Sequence[Mapping[str, Any]], verdicts["records"])
    }
    if len(request_batches) != len(verdict_batches) or len(verdict_batches) != int(verdicts["invocation_count"]):
        raise RuntimeError("v6 batch count mismatch")
    verdict_by_hash = {str(batch["batch_request_hash"]): batch for batch in verdict_batches}
    expected_model = str(cast(Mapping[str, Any], protocol["stage2"])["model"])
    for request_batch in request_batches:
        batch_hash = str(request_batch["batch_request_hash"])
        batch = verdict_by_hash[batch_hash]
        row_ids = [str(row_id) for row_id in cast(Sequence[str], request_batch["row_ids"])]
        reconstructed = {
            **{key: value for key, value in batch.items() if key not in {"record_sha256", "batch_index", "row_ids"}},
            "records": [records[row_id] for row_id in row_ids],
        }
        if batch.get("record_sha256") != canonical_sha256(reconstructed):
            raise RuntimeError(f"invalid v6 batch record {request_batch['batch_index']}")
        model_usage = cast(Mapping[str, Any], cast(Mapping[str, Any], batch["runtime"])["model_usage"])
        if expected_model not in model_usage:
            raise RuntimeError("v6 actual model attribution misses requested Opus model")


def _single_batch_adapter(
    protocol: Mapping[str, Any],
    requests: Mapping[str, Any],
    verdicts: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol_like = json.loads(json.dumps(protocol))
    frozen = cast(Mapping[str, Any], protocol["frozen_identities"])
    protocol_like["v4_frozen_identities"] = {
        "test_dataset_content_sha256": frozen["v4_test_dataset_content_sha256"],
        "v4_scorecard_sha256": frozen["v4_scorecard_sha256"],
    }
    protocol_hash = canonical_sha256(protocol_like)
    aggregate_hash = "v6-evaluation-adapter"
    requests_base = {
        **{key: value for key, value in requests.items() if key != "request_bundle_sha256"},
        "protocol_sha256": protocol_hash,
        "request_count": requests["candidate_count"],
        "invocation_count": 1,
        "batch_user_content_chars": requests["batch_user_content_chars_total"],
        "batch_request": {"batch_request_hash": aggregate_hash},
    }
    requests_like = {
        **requests_base,
        "request_bundle_sha256": canonical_sha256(requests_base),
    }
    records = list(cast(Sequence[Mapping[str, Any]], verdicts["records"]))
    runtime = _aggregate_runtime(cast(Sequence[Mapping[str, Any]], verdicts["batches"]))
    batch_base = {
        "schema_version": 1,
        "batch_request_hash": aggregate_hash,
        "batch_request_identity_sha256": aggregate_hash,
        "provider": protocol["stage2"]["provider"],
        "requested_model": protocol["stage2"]["model"],
        "transport": protocol["stage2"]["transport"],
        "candidate_count": len(records),
        "records": records,
        "runtime": runtime,
        "response_metadata": {},
        "created_at": "v6-evaluation-adapter",
    }
    batch_record = {**batch_base, "record_sha256": canonical_sha256(batch_base)}
    batch_summary = {key: value for key, value in batch_record.items() if key != "records"}
    verdict_base = {
        "schema_version": 2,
        "study_id": protocol["study_id"],
        "protocol_sha256": protocol_hash,
        "request_bundle_sha256": requests_like["request_bundle_sha256"],
        "provider": protocol["stage2"]["provider"],
        "requested_model": protocol["stage2"]["model"],
        "transport": protocol["stage2"]["transport"],
        "invocation_count": 1,
        "batch": batch_summary,
        "records": records,
    }
    verdicts_like = {
        **verdict_base,
        "verdict_bundle_sha256": canonical_sha256(verdict_base),
    }
    return protocol_like, requests_like, verdicts_like


def _aggregate_runtime(batches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    model_usage: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for batch in batches:
        runtime = cast(Mapping[str, Any], batch["runtime"])
        for model, usage in cast(Mapping[str, Mapping[str, Any]], runtime.get("model_usage") or {}).items():
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    model_usage[model][key] += float(value)
    return {
        "wall_duration_ms": math.fsum(float(cast(Mapping[str, Any], batch["runtime"])["wall_duration_ms"]) for batch in batches),
        "total_cost_usd_equivalent": math.fsum(float(cast(Mapping[str, Any], batch["runtime"]).get("total_cost_usd_equivalent") or 0) for batch in batches),
        "input_tokens": sum(int(cast(Mapping[str, Any], batch["runtime"]).get("input_tokens") or 0) for batch in batches),
        "output_tokens": sum(int(cast(Mapping[str, Any], batch["runtime"]).get("output_tokens") or 0) for batch in batches),
        "model_usage": {model: dict(usage) for model, usage in model_usage.items()},
    }


def _runtime_summary(batches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wall = [float(cast(Mapping[str, Any], batch["runtime"])["wall_duration_ms"]) for batch in batches]
    aggregate = _aggregate_runtime(batches)
    return {
        "invocations": len(batches),
        "wall_duration_ms_sum": sum(wall),
        "wall_duration_ms_per_batch": _distribution(wall),
        "modeled_equivalent_cost_usd_total": aggregate["total_cost_usd_equivalent"],
        "input_tokens_total": aggregate["input_tokens"],
        "output_tokens_total": aggregate["output_tokens"],
        "actual_model_usage": aggregate["model_usage"],
    }


def _request_summary(requests: Mapping[str, Any]) -> dict[str, Any]:
    candidates = cast(Sequence[Mapping[str, Any]], requests["requests"])
    batches = cast(Sequence[Mapping[str, Any]], requests["batches"])
    event_counts = [int(cast(Mapping[str, Any], request["evidence_manifest"])["supplied_event_count"]) for request in candidates]
    event_chars = [int(cast(Mapping[str, Any], request["evidence_manifest"])["supplied_event_json_chars"]) for request in candidates]
    batch_chars = [len(str(cast(Mapping[str, Any], batch["identity"])["user_content"])) for batch in batches]
    return {
        "candidate_count": len(candidates),
        "invocation_count": len(batches),
        "supplied_events_per_candidate": _distribution(event_counts),
        "supplied_event_json_chars_per_candidate": _distribution(event_chars),
        "batch_user_content_chars": _distribution(batch_chars),
        "batch_user_content_chars_total": sum(batch_chars),
    }


def _automation_slice(
    *,
    dataset: Mapping[str, Any],
    requests: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    labels = {str(row["row_id"]): int(row["label"]) for row in cast(Sequence[Mapping[str, Any]], dataset["rows"])}
    verdict = {str(record["row_id"]): bool(cast(Mapping[str, Any], record["response"])["real"]) for record in records}
    suspects = [request for request in requests if bool(cast(Mapping[str, Any], request["automation_suspect"])["suspect"])]
    rejected = [request for request in suspects if not verdict[str(request["row_id"])]]
    return {
        "candidates": len(suspects),
        "stage2_rejected": len(rejected),
        "rejection_rate": len(rejected) / len(suspects) if suspects else None,
        "full_label_positive_candidates": sum(labels[str(request["row_id"])] == 1 for request in suspects),
        "full_label_negative_candidates": sum(labels[str(request["row_id"])] == 0 for request in suspects),
        "definition": "Frozen actor/content/repetition heuristic in the v6 request artifact.",
    }


def _finding(result: Mapping[str, Any], v5: Mapping[str, Any]) -> dict[str, Any]:
    system = cast(Mapping[str, Any], result["end_to_end"])
    v5_system = cast(Mapping[str, Any], v5["end_to_end"])
    precision_delta = float(system["precision"]) - float(v5_system["precision"])
    return {
        "precision_delta_vs_v5": precision_delta,
        "hour_recall_delta_vs_v5": float(system["hour_recall"]) - float(v5_system["hour_recall"]),
        "conclusion": (
            "Rich evidence materially improved stage-two precision over evidence-starved v5."
            if precision_delta >= 0.05
            else "Rich evidence left stage-two precision approximately flat; hourly GitHub triage remains the limiting architecture."
        ),
    }


def render_markdown(scorecard: Mapping[str, Any]) -> str:
    stage1 = cast(Mapping[str, Any], scorecard["stage1"])
    v5 = cast(Mapping[str, Any], scorecard["v5_evidence_starved_baseline"])
    system = cast(Mapping[str, Any], scorecard["end_to_end"])
    clean = cast(Mapping[str, Any], scorecard["clean_source_cut"])
    requests = cast(Mapping[str, Any], scorecard["requests"])
    runtime = cast(Mapping[str, Any], scorecard["runtime"])
    automation = cast(Mapping[str, Any], scorecard["automation_suspect_slice"])
    return "\n".join(
        [
            "# Sentinel v6 — rich-evidence two-stage result",
            "",
            "> Frozen v4 candidate ranker followed by Claude Opus over rich, strictly pre-candidate evidence in scheduled-size batches.",
            "",
            "| System | Precision | Hour recall | Incident recall | True alert-hours killed |",
            "| --- | ---: | ---: | ---: | ---: |",
            f"| Stage 1 only | {_percent(stage1['precision'])} | {_percent(stage1['hour_recall'])} | {_percent(stage1['incident_recall'])} | 0 |",
            f"| V5, one-event judge | {_percent(v5['precision'])} | {_percent(v5['hour_recall'])} | {_percent(v5['incident_recall'])} | {v5['true_positive_hours_killed']} |",
            f"| V6, rich-evidence judge | {_percent(system['precision'])} | {_percent(system['hour_recall'])} | {_percent(system['incident_recall'])} | {system['true_positive_hours_killed']} |",
            "",
            f"V6 estimates {_number(system['estimated_alerts'])} surviving alerts and retains a median {_number(cast(Mapping[str, Any], system['lead_time'])['median_hours'])} hours of lead time.",
            "",
            "## Clean-source cut",
            "",
            f"Completed bug closures + merged fixing PRs: {_percent(clean['precision'])} precision, {_percent(clean['hour_recall'])} hour recall, {_percent(clean['incident_recall'])} incident recall.",
            "",
            "## Evidence, automation, and runtime",
            "",
            f"Median evidence: {_number(cast(Mapping[str, Any], requests['supplied_events_per_candidate'])['median'])} events / {_number(cast(Mapping[str, Any], requests['supplied_event_json_chars_per_candidate'])['median'])} JSON characters per candidate. {requests['invocation_count']} subscription batches.",
            "",
            f"Automation-suspect candidates: {automation['candidates']}; Stage 2 rejected {automation['stage2_rejected']} ({_percent(automation['rejection_rate'])}).",
            "",
            f"Claude CLI modeled-equivalent usage: ${_number(runtime['modeled_equivalent_cost_usd_total'])}; no API-key or OpenAI calls.",
            "",
            "## Limits",
            "",
            "The full-label result is descriptive because the maintainer-comment source has 30% obvious audit noise. The clean-source cut removes that source. V6 reuses the published v4 test split and v5 sample, so this is not a fresh holdout.",
            "",
            f"Scorecard SHA-256: `{scorecard['scorecard_sha256']}`",
            "",
        ]
    )


def _distribution(values: Sequence[float | int]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean": statistics.mean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[int(0.95 * (len(ordered) - 1))],
        "max": max(ordered),
    }


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
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
