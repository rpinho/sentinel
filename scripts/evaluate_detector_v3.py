#!/usr/bin/env python3
"""Open the frozen test repositories once and publish detector-v3 metrics."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
from sklearn.metrics import f1_score, recall_score, roc_auc_score

from sentinel.detector_v3 import canonical_sha256
from sentinel.model_v3 import predict_probability, verify_model
from sentinel.types import parse_timestamp

DEFAULT_DATASET = Path("holdout/v3/dataset.json")
DEFAULT_MODEL = Path("holdout/v3/model.json")
DEFAULT_BURST_MODEL = Path("holdout/v3/burst-model.json")
DEFAULT_V1 = Path("holdout/scorecard.json")
DEFAULT_V2 = Path("holdout/v2/scorecard.json")
DEFAULT_SCORECARD = Path("holdout/v3/scorecard.json")
DEFAULT_MARKDOWN = Path("holdout/v3/SCORECARD-v3.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--burst-model", type=Path, default=DEFAULT_BURST_MODEL)
    parser.add_argument("--v1-scorecard", type=Path, default=DEFAULT_V1)
    parser.add_argument("--v2-scorecard", type=Path, default=DEFAULT_V2)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    dataset = _read_object(args.dataset)
    model = _read_object(args.model)
    burst_model = _read_object(args.burst_model)
    v1 = _read_object(args.v1_scorecard)
    v2 = _read_object(args.v2_scorecard)
    verify_model(model)
    verify_model(burst_model)
    expected_dataset = dataset["hashes"]["dataset_content_sha256"]
    for artifact in (model, burst_model):
        if artifact["training"]["dataset_content_sha256"] != expected_dataset:
            raise RuntimeError("frozen v3 model and dataset identities differ")

    test_rows = [
        row
        for row in cast(Sequence[Mapping[str, Any]], dataset["rows"])
        if row["split"] == "test"
    ]
    test_incidents = [
        incident
        for incident in cast(
            Sequence[Mapping[str, Any]], dataset["positive_incidents"]
        )
        if incident["split"] == "test"
    ]
    primary = evaluate_model(test_rows, test_incidents, model)
    burst = evaluate_model(test_rows, test_incidents, burst_model)
    detection_hours = sum(
        int(
            (
                parse_timestamp(str(window["detection_end"]))
                - parse_timestamp(str(window["detection_start"]))
            ).total_seconds()
            / 3600
        )
        for window in cast(Sequence[Mapping[str, Any]], dataset["coverage"])
    )
    repository_counts = {
        int(window["repositories"])
        for window in cast(Sequence[Mapping[str, Any]], dataset["coverage"])
    }
    if len(repository_counts) != 1:
        raise RuntimeError("v3 windows do not share one frozen repository count")
    repository_count = repository_counts.pop()
    test_repositories = sorted({str(row["entity"]) for row in test_rows})
    scorecard: dict[str, Any] = {
        "schema_version": 1,
        "study_id": dataset["study_id"],
        "frozen_identities": {
            "dataset_content_sha256": expected_dataset,
            "protocol_sha256": dataset["hashes"]["protocol_sha256"],
            "label_audit_sha256": dataset["hashes"]["label_audit_sha256"],
            "model_sha256": model["model_sha256"],
            "detector_version": model["detector_version"],
            "burst_model_sha256": burst_model["model_sha256"],
        },
        "coverage": {
            "repositories": repository_count,
            "test_repositories": test_repositories,
            "archive_hours": sum(
                int(window["archive_hours_expected"])
                for window in cast(
                    Sequence[Mapping[str, Any]], dataset["coverage"]
                )
            ),
            "source_repo_hours": sum(
                int(window["archive_hours_expected"]) * repository_count
                for window in cast(
                    Sequence[Mapping[str, Any]], dataset["coverage"]
                )
            ),
            "detection_hours": detection_hours,
            "detection_repo_hours": detection_hours * repository_count,
            "test_detection_repo_hours": detection_hours * len(test_repositories),
            "candidate_hours": dataset["class_balance"]["all"][
                "candidate_hours"
            ],
            "test_candidate_hours": len(test_rows),
            "complete": all(
                window["archive_hours_expected"] == window["archive_hours_observed"]
                for window in cast(
                    Sequence[Mapping[str, Any]], dataset["coverage"]
                )
            ),
        },
        "ground_truth": {
            "auto_label_candidates": len(dataset["auto_label_candidates"]),
            "audited_positive_incidents": len(dataset["positive_incidents"]),
            "test_positive_incidents": len(test_incidents),
            "label_audit": dataset["label_audit"],
            "class_balance": dataset["class_balance"],
        },
        "test": primary,
        "burst_ablation": burst,
        "finding": {
            "burst_roc_auc": burst["metrics"]["roc_auc"],
            "coherence_roc_auc": primary["metrics"]["roc_auc"],
            "roc_auc_delta": primary["metrics"]["roc_auc"]
            - burst["metrics"]["roc_auc"],
            "coherence_feature_importance": model["tree"][
                "feature_importance"
            ],
        },
        "train_oof": {
            "coherence": model["selection"]["oof_metrics"],
            "burst": burst_model["selection"]["oof_metrics"],
        },
        "version_progression": _version_progression(v1, v2, model, primary),
        "methodology": {
            "unit": "repository UTC activity-spike hour",
            "split": "whole repositories",
            "selection": "leave-one-train-repository-out F1; test opened once after model commit",
            "temporal_invariant": "Every feature source event is strictly before candidate_ts; credit requires candidate_ts < maintainer acknowledgment.",
            "comparability_note": "V1/v2 use the original 3-repository holdout; v3 uses the larger audited candidate-hour corpus. Their rows show iteration, not a same-sample leaderboard.",
        },
    }
    unstamped = dict(scorecard)
    scorecard["scorecard_sha256"] = canonical_sha256(unstamped)
    _write_json(args.scorecard, scorecard)
    args.markdown.write_text(render_markdown(scorecard), encoding="utf-8")
    print(
        "V3_TEST "
        f"precision={_number(primary['metrics']['precision'])} "
        f"recall={primary['metrics']['recall']:.3f} "
        f"auc={primary['metrics']['roc_auc']:.3f} "
        f"burst_auc={burst['metrics']['roc_auc']:.3f} "
        f"median_lead_minutes={primary['lead_time']['median_minutes']} "
        f"alerts={primary['metrics']['alerts']} "
        f"misses={len(primary['misses'])}"
    )


def evaluate_model(
    rows: Sequence[Mapping[str, Any]],
    incidents: Sequence[Mapping[str, Any]],
    model: Mapping[str, Any],
) -> dict[str, Any]:
    labels = np.asarray([int(row["label"]) for row in rows])
    probabilities = np.asarray(
        [
            predict_probability(
                cast(Mapping[str, float], row["features"]), model
            )
            for row in rows
        ]
    )
    predictions = probabilities >= float(model["threshold"])
    tp = int(np.sum((labels == 1) & predictions))
    fp = int(np.sum((labels == 0) & predictions))
    tn = int(np.sum((labels == 0) & ~predictions))
    fn = int(np.sum((labels == 1) & ~predictions))
    metrics = {
        "alerts": int(predictions.sum()),
        "true_positive_hours": tp,
        "false_positive_hours": fp,
        "true_negative_hours": tn,
        "missed_positive_hours": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "precision_wilson_95": _wilson(tp, tp + fp),
        "recall_wilson_95": _wilson(tp, tp + fn),
    }
    row_by_id = {str(row["row_id"]): row for row in rows}
    probability_by_id = {
        str(row["row_id"]): float(probability)
        for row, probability in zip(rows, probabilities)
    }
    predicted_by_id = {
        str(row["row_id"]): bool(predicted)
        for row, predicted in zip(rows, predictions)
    }
    catches: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    for incident in incidents:
        candidate = dict(incident)
        row_id = str(incident["row_id"])
        candidate["probability"] = probability_by_id[row_id]
        candidate["candidate_url"] = row_by_id[row_id]["candidate_url"]
        if not (
            parse_timestamp(str(candidate["candidate_ts"]))
            < parse_timestamp(str(candidate["ack_ts"]))
        ):
            raise RuntimeError(f"temporal credit violation for {candidate['incident_id']}")
        (catches if predicted_by_id[row_id] else misses).append(candidate)
    false_positives = [
        {
            "row_id": row["row_id"],
            "entity": row["entity"],
            "candidate_ts": row["candidate_ts"],
            "candidate_url": row["candidate_url"],
            "probability": float(probability),
        }
        for row, probability, predicted in zip(rows, probabilities, predictions)
        if predicted and not int(row["label"])
    ]
    lead_minutes = sorted(float(incident["lead_minutes"]) for incident in catches)
    return {
        "detector_version": model["detector_version"],
        "model_sha256": model["model_sha256"],
        "threshold": model["threshold"],
        "metrics": metrics,
        "incident_recall": len(catches) / len(incidents) if incidents else 0.0,
        "caught_incidents": catches,
        "misses": misses,
        "false_positives": false_positives,
        "lead_time": _lead_summary(lead_minutes),
        "predictions": [
            {
                "row_id": row["row_id"],
                "entity": row["entity"],
                "candidate_ts": row["candidate_ts"],
                "candidate_url": row["candidate_url"],
                "label": int(row["label"]),
                "probability": round(float(probability), 12),
                "predicted": bool(predicted),
            }
            for row, probability, predicted in zip(rows, probabilities, predictions)
        ],
    }


def _lead_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "median_minutes": None,
            "median_hours": None,
            "minimum_minutes": None,
            "maximum_minutes": None,
            "values_minutes": [],
        }
    median = statistics.median(values)
    return {
        "count": len(values),
        "median_minutes": round(median, 3),
        "median_hours": round(median / 60, 3),
        "minimum_minutes": round(min(values), 3),
        "maximum_minutes": round(max(values), 3),
        "values_minutes": [round(value, 3) for value in values],
    }


def _wilson(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return [max(0.0, center - spread), min(1.0, center + spread)]


def _version_progression(
    v1: Mapping[str, Any],
    v2: Mapping[str, Any],
    model: Mapping[str, Any],
    primary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "version": "v1",
            "detector_version": v1["holdout"]["detector_version"],
            "corpus": "original frozen holdout",
            "firings": v1["metrics"]["no_agent"]["firings"],
            "precision": v1["metrics"]["no_agent"]["precision"],
            "recall": None,
            "published_misses": len(v1["detector_misses"]),
            "finding": "Naive burst magnitude fired on routine and automated floods.",
        },
        {
            "version": "v2",
            "detector_version": v2["holdout"]["detector_version"],
            "corpus": "original frozen holdout",
            "firings": v2["metrics"]["no_agent"]["firings"],
            "precision": v2["metrics"]["no_agent"]["precision"],
            "recall": None,
            "published_misses": len(v2["detector_misses"]),
            "finding": "Robust rate and bot gates removed the noise but went silent.",
        },
        {
            "version": "v3",
            "detector_version": model["detector_version"],
            "corpus": "whole-repository test split",
            "firings": primary["metrics"]["alerts"],
            "precision": primary["metrics"]["precision"],
            "recall": primary["metrics"]["recall"],
            "published_misses": len(primary["misses"]),
            "finding": "Coherence tree frozen from train-only repository-grouped selection.",
        },
    ]


def render_markdown(scorecard: Mapping[str, Any]) -> str:
    test = cast(Mapping[str, Any], scorecard["test"])
    metrics = cast(Mapping[str, Any], test["metrics"])
    lead = cast(Mapping[str, Any], test["lead_time"])
    finding = cast(Mapping[str, Any], scorecard["finding"])
    lines = [
        "# Sentinel detector v3 — frozen test scorecard",
        "",
        "> Model and threshold were committed before this whole-repository test split was opened.",
        "",
        "## Headline test result",
        "",
        "| Precision | Recall | ROC AUC | Median lead | Alerts | Test incidents |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {_percent(metrics['precision'])} | {_percent(metrics['recall'])} | "
            f"{metrics['roc_auc']:.3f} | {_minutes(lead['median_minutes'])} | "
            f"{metrics['alerts']} | {scorecard['ground_truth']['test_positive_incidents']} |"
        ),
        "",
        "## Burst magnitude versus coherence",
        "",
        "| Frozen model | Test ROC AUC | Test precision | Test recall |",
        "| --- | ---: | ---: | ---: |",
        (
            f"| Burst-only ablation | {finding['burst_roc_auc']:.3f} | "
            f"{_percent(scorecard['burst_ablation']['metrics']['precision'])} | "
            f"{_percent(scorecard['burst_ablation']['metrics']['recall'])} |"
        ),
        (
            f"| Coherence v3 | {finding['coherence_roc_auc']:.3f} | "
            f"{_percent(metrics['precision'])} | {_percent(metrics['recall'])} |"
        ),
        "",
        f"Coherence AUC delta: **{finding['roc_auc_delta']:+.3f}**.",
        "",
        "## Caught incidents",
        "",
        "| Candidate | Repository / issue | Acknowledgment | Lead | Score |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for incident in cast(Sequence[Mapping[str, Any]], test["caught_incidents"]):
        lines.append(
            f"| [{incident['candidate_ts']}]({incident['candidate_url']}) | "
            f"{incident['entity']} #{incident['issue_number']} | "
            f"[{incident['ack_ts']}]({incident['ack_url']}) | "
            f"{_minutes(incident['lead_minutes'])} | {incident['probability']:.3f} |"
        )
    if not test["caught_incidents"]:
        lines.append("| none | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Misses",
            "",
            "| Candidate | Repository / issue | Acknowledgment | Score |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for incident in cast(Sequence[Mapping[str, Any]], test["misses"]):
        lines.append(
            f"| [{incident['candidate_ts']}]({incident['candidate_url']}) | "
            f"{incident['entity']} #{incident['issue_number']} | "
            f"[{incident['ack_ts']}]({incident['ack_url']}) | "
            f"{incident['probability']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Frozen identities",
            "",
            f"- Dataset: `{scorecard['frozen_identities']['dataset_content_sha256']}`",
            f"- Model: `{scorecard['frozen_identities']['model_sha256']}`",
            f"- Label audit: `{scorecard['frozen_identities']['label_audit_sha256']}`",
            f"- Scorecard: `{scorecard['scorecard_sha256']}`",
            "",
            "V1/v2 and v3 use different frozen corpora. The progression is evidence of iteration, not a same-sample leaderboard.",
            "",
        ]
    )
    return "\n".join(lines)


def _minutes(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.1f} min"


def _percent(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.1%}"


def _number(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
