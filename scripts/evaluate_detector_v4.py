#!/usr/bin/env python3
"""Open the frozen v4 test split once and publish its honest scorecard."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from sentinel.detector_v3 import canonical_sha256
from sentinel.detector_v4 import build_audit_candidates
from sentinel.model_v4 import predict_probability, verify_model
from sentinel.types import parse_timestamp

DEFAULT_PROTOCOL = Path("holdout/v4/study-protocol.json")
DEFAULT_TRAIN_DATASET = Path("holdout/v4/dataset-train.json.gz")
DEFAULT_TEST_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_MODEL = Path("holdout/v4/model.json")
DEFAULT_BURST_MODEL = Path("holdout/v4/burst-model.json")
DEFAULT_TRAIN_AUDIT = Path("holdout/v4/label-audit-train.json")
DEFAULT_TEST_AUDIT = Path("holdout/v4/label-audit-test.json")
DEFAULT_DATASET_MANIFEST = Path("holdout/v4/dataset.json")
DEFAULT_LABEL_AUDIT = Path("holdout/v4/label-audit.json")
DEFAULT_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_MARKDOWN = Path("holdout/v4/SCORECARD-v4.md")
BOOTSTRAP_SEED = 20260720
BOOTSTRAP_REPLICATES = 2000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--train-dataset", type=Path, default=DEFAULT_TRAIN_DATASET)
    parser.add_argument("--test-dataset", type=Path, default=DEFAULT_TEST_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--burst-model", type=Path, default=DEFAULT_BURST_MODEL)
    parser.add_argument("--train-audit", type=Path, default=DEFAULT_TRAIN_AUDIT)
    parser.add_argument("--test-audit", type=Path, default=DEFAULT_TEST_AUDIT)
    parser.add_argument("--dataset-manifest", type=Path, default=DEFAULT_DATASET_MANIFEST)
    parser.add_argument("--label-audit", type=Path, default=DEFAULT_LABEL_AUDIT)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--model-commit", required=True)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    train_dataset = _read_gzip_object(args.train_dataset)
    test_dataset = _read_gzip_object(args.test_dataset)
    model = _read_object(args.model)
    burst_model = _read_object(args.burst_model)
    train_audit = _read_object(args.train_audit)
    test_audit = _read_object(args.test_audit)
    _verify_dataset(train_dataset, protocol, split="train")
    _verify_dataset(test_dataset, protocol, split="test")
    _verify_audit(train_dataset, protocol, train_audit, split="train")
    _verify_audit(test_dataset, protocol, test_audit, split="test")
    for artifact in (model, burst_model):
        verify_model(artifact)
        if artifact["training"]["dataset_content_sha256"] != train_dataset["hashes"][
            "dataset_content_sha256"
        ]:
            raise RuntimeError("v4 model and train dataset identities differ")
        if artifact["training"]["split"] != "train":
            raise RuntimeError("v4 frozen model was not train-only")

    test_rows = cast(Sequence[Mapping[str, Any]], test_dataset["rows"])
    test_incidents = cast(
        Sequence[Mapping[str, Any]], test_dataset["positive_incidents"]
    )
    primary = evaluate_model(test_rows, test_incidents, model)
    burst = evaluate_model(test_rows, test_incidents, burst_model)
    combined_audit = _combine_audits(train_audit, test_audit)
    dataset_manifest = _dataset_manifest(
        train_dataset,
        test_dataset,
        train_path=args.train_dataset,
        test_path=args.test_dataset,
    )
    _write_json(args.dataset_manifest, dataset_manifest)
    _write_json(args.label_audit, combined_audit)

    train_balance = cast(Mapping[str, Any], train_dataset["class_balance"])
    test_balance = cast(Mapping[str, Any], test_dataset["class_balance"])
    test_attribution = cast(Mapping[str, Any], test_dataset["source_attribution"])
    targets = cast(Mapping[str, Any], protocol["power_targets"])
    test_power_met = int(test_balance["positive_incidents"]) >= int(
        targets["minimum_test_positive_incidents"]
    )
    maximum_noise = float(targets["maximum_obvious_label_noise_rate_per_source"])
    test_audit_sources = cast(
        Mapping[str, Mapping[str, Any]], test_audit["by_source"]
    )
    failed_audit_sources = [
        source
        for source, result in sorted(test_audit_sources.items())
        if float(result["obvious_label_noise_rate"]) > maximum_noise
    ]
    label_quality_met = not failed_audit_sources
    scorecard: dict[str, Any] = {
        "schema_version": 1,
        "study_id": protocol["study_id"],
        "model_frozen_commit": args.model_commit,
        "frozen_identities": {
            "protocol_sha256": canonical_sha256(protocol),
            "train_dataset_content_sha256": train_dataset["hashes"][
                "dataset_content_sha256"
            ],
            "test_dataset_content_sha256": test_dataset["hashes"][
                "dataset_content_sha256"
            ],
            "dataset_manifest_sha256": dataset_manifest["manifest_sha256"],
            "model_sha256": model["model_sha256"],
            "detector_version": model["detector_version"],
            "burst_model_sha256": burst_model["model_sha256"],
            "label_audit_sha256": combined_audit["audit_sha256"],
        },
        "power": {
            "train_rows": train_balance["candidate_hours"],
            "train_positive_hours": train_balance["positive_hours"],
            "train_positive_incidents": train_balance["positive_incidents"],
            "test_rows": test_balance["candidate_hours"],
            "test_positive_hours": test_balance["positive_hours"],
            "test_positive_incidents": test_balance["positive_incidents"],
            "minimum_train_positive_incidents": targets[
                "minimum_train_positive_incidents"
            ],
            "minimum_test_positive_incidents": targets[
                "minimum_test_positive_incidents"
            ],
            "train_target_met": int(train_balance["positive_incidents"])
            >= int(targets["minimum_train_positive_incidents"]),
            "test_target_met": test_power_met,
        },
        "coverage": {
            "train_repositories": len(
                cast(Mapping[str, Any], protocol["repositories"])["train"]
            ),
            "test_repositories": len(
                cast(Mapping[str, Any], protocol["repositories"])["test"]
            ),
            "test_repository_names": list(
                cast(Mapping[str, Any], protocol["repositories"])["test"]
            ),
            "windows": test_dataset["coverage"],
        },
        "population": {
            "candidate_rule": protocol["label_rule"]["unit"],
            "train_source_attribution": train_dataset["source_attribution"],
            "test_source_attribution": test_attribution,
            "confirmed_incidents_outside_old_spike_population": test_attribution[
                "outside_old_spike_population"
            ],
            "confirmed_incidents_outside_old_spike_share": test_attribution[
                "outside_old_spike_share"
            ],
        },
        "label_audit": {
            "maximum_obvious_label_noise_rate_per_source": maximum_noise,
            "test_target_met": label_quality_met,
            "test_failed_sources": failed_audit_sources,
            "train": {
                "counts": train_audit["counts"],
                "by_source": train_audit["by_source"],
            },
            "test": {
                "counts": test_audit["counts"],
                "by_source": test_audit["by_source"],
            },
        },
        "train_oof": {
            "coherence": model["selection"]["oof_metrics"],
            "burst": burst_model["selection"]["oof_metrics"],
        },
        "test": primary,
        "burst_ablation": burst,
        "finding": _finding(
            primary,
            burst,
            test_power_met=test_power_met,
            label_quality_met=label_quality_met,
            failed_audit_sources=failed_audit_sources,
        ),
        "methodology": {
            "unit": "repository UTC active hour",
            "split": "whole repositories",
            "selection": "leave-one-train-repository-out F1 with equal repository/class weight; test opened after model artifact commit",
            "temporal_invariant": "Every feature event precedes candidate_ts and every credited confirmation follows candidate_ts.",
            "uncertainty": f"{BOOTSTRAP_REPLICATES} deterministic whole-repository bootstrap replicates (seed {BOOTSTRAP_SEED}).",
            "test_rows_used_for_selection": 0,
        },
    }
    scorecard["scorecard_sha256"] = canonical_sha256(scorecard)
    _write_json(args.scorecard, scorecard)
    args.markdown.write_text(render_markdown(scorecard), encoding="utf-8")
    metrics = cast(Mapping[str, Any], primary["metrics"])
    print(
        "V4_TEST "
        f"precision={_number(metrics['precision'])} "
        f"recall={metrics['recall']:.3f} "
        f"auc={metrics['roc_auc']:.3f} "
        f"burst_auc={burst['metrics']['roc_auc']:.3f} "
        f"median_lead_minutes={primary['lead_time']['median_minutes']} "
        f"alerts={metrics['alerts']} incidents={len(test_incidents)} "
        f"powered={str(test_power_met).lower()}"
    )


def evaluate_model(
    rows: Sequence[Mapping[str, Any]],
    incidents: Sequence[Mapping[str, Any]],
    model: Mapping[str, Any],
) -> dict[str, Any]:
    labels = np.asarray([int(row["label"]) for row in rows], dtype=int)
    probabilities = np.asarray(
        [
            predict_probability(cast(Mapping[str, float], row["features"]), model)
            for row in rows
        ]
    )
    predictions = probabilities >= float(model["threshold"])
    groups = np.asarray([str(row["entity"]) for row in rows])
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
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "average_precision": float(average_precision_score(labels, probabilities)),
    }
    metrics["repository_bootstrap_95"] = _repository_bootstrap(
        labels, predictions, probabilities, groups
    )
    row_by_id = {str(row["row_id"]): row for row in rows}
    probability_by_id = {
        str(row["row_id"]): float(probability)
        for row, probability in zip(rows, probabilities)
    }
    predicted_by_id = {
        str(row["row_id"]): bool(predicted)
        for row, predicted in zip(rows, predictions)
    }
    caught: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for incident in incidents:
        row_id = str(incident["row_id"])
        candidate = {
            **dict(incident),
            "probability": probability_by_id[row_id],
            "candidate_url": row_by_id[row_id]["candidate_url"],
        }
        if not (
            parse_timestamp(str(candidate["candidate_ts"]))
            < parse_timestamp(str(candidate["confirmation_ts"]))
        ):
            raise RuntimeError(
                f"v4 temporal credit violation for {candidate['incident_id']}"
            )
        (caught if predicted_by_id[row_id] else missed).append(candidate)
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
    source_recall = {}
    for source in sorted({str(row["label_source"]) for row in incidents}):
        selected = [row for row in incidents if row["label_source"] == source]
        source_recall[source] = {
            "incidents": len(selected),
            "caught": sum(predicted_by_id[str(row["row_id"])] for row in selected),
            "recall": (
                sum(predicted_by_id[str(row["row_id"])] for row in selected)
                / len(selected)
            ),
        }
    lead_minutes = sorted(float(row["lead_minutes"]) for row in caught)
    return {
        "detector_version": model["detector_version"],
        "model_sha256": model["model_sha256"],
        "threshold": model["threshold"],
        "metrics": metrics,
        "incident_recall": len(caught) / len(incidents) if incidents else 0.0,
        "source_recall": source_recall,
        "caught_incidents": caught,
        "misses": missed,
        "false_positives": false_positives,
        "lead_time": _lead_summary(lead_minutes),
        "predictions": [
            {
                "row_id": row["row_id"],
                "entity": row["entity"],
                "candidate_ts": row["candidate_ts"],
                "label": int(row["label"]),
                "probability": round(float(probability), 12),
                "predicted": bool(predicted),
            }
            for row, probability, predicted in zip(rows, probabilities, predictions)
        ],
    }


def _repository_bootstrap(
    labels: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
    groups: np.ndarray,
) -> dict[str, list[float]]:
    repositories = np.unique(groups)
    indices = {repo: np.flatnonzero(groups == repo) for repo in repositories}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    values: dict[str, list[float]] = {
        "precision": [],
        "recall": [],
        "roc_auc": [],
    }
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled = rng.choice(repositories, size=len(repositories), replace=True)
        selected = np.concatenate([indices[repo] for repo in sampled])
        y = labels[selected]
        predicted = predictions[selected]
        probability = probabilities[selected]
        values["precision"].append(
            float(precision_score(y, predicted, zero_division=0))
        )
        values["recall"].append(
            float(recall_score(y, predicted, zero_division=0))
        )
        if len(np.unique(y)) == 2:
            values["roc_auc"].append(float(roc_auc_score(y, probability)))
    return {
        name: [
            float(np.quantile(samples, 0.025)),
            float(np.quantile(samples, 0.975)),
        ]
        for name, samples in values.items()
        if samples
    }


def _finding(
    primary: Mapping[str, Any],
    burst: Mapping[str, Any],
    *,
    test_power_met: bool,
    label_quality_met: bool,
    failed_audit_sources: Sequence[str],
) -> dict[str, Any]:
    primary_auc = float(cast(Mapping[str, Any], primary["metrics"])["roc_auc"])
    burst_auc = float(cast(Mapping[str, Any], burst["metrics"])["roc_auc"])
    interval = cast(Mapping[str, Sequence[float]], cast(Mapping[str, Any], primary["metrics"])["repository_bootstrap_95"])["roc_auc"]
    if not label_quality_met:
        conclusion = (
            "The frozen test metrics are descriptive, not confirmatory: "
            "the predeclared label-noise ceiling was exceeded by "
            + ", ".join(failed_audit_sources)
            + "."
        )
    elif not test_power_met:
        conclusion = "The frozen test split missed its declared power target."
    elif interval[0] > 0.5:
        conclusion = "Coherence signals generalized above chance on the powered whole-repository test split."
    elif interval[1] < 0.5:
        conclusion = "Coherence signals generalized below chance on the powered whole-repository test split."
    else:
        conclusion = "The powered whole-repository test does not distinguish coherence ranking from chance."
    return {
        "conclusion": conclusion,
        "coherence_roc_auc": primary_auc,
        "burst_roc_auc": burst_auc,
        "roc_auc_delta": primary_auc - burst_auc,
        "coherence_auc_bootstrap_95": interval,
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


def _dataset_manifest(
    train: Mapping[str, Any],
    test: Mapping[str, Any],
    *,
    train_path: Path,
    test_path: Path,
) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "study_id": train["study_id"],
        "format": "deterministic gzip-compressed canonical JSON",
        "splits": {
            "train": {
                "path": str(train_path),
                "dataset_content_sha256": train["hashes"][
                    "dataset_content_sha256"
                ],
                "rows_sha256": train["hashes"]["rows_sha256"],
                "positive_incidents_sha256": train["hashes"][
                    "positive_incidents_sha256"
                ],
                "class_balance": train["class_balance"],
            },
            "test": {
                "path": str(test_path),
                "dataset_content_sha256": test["hashes"][
                    "dataset_content_sha256"
                ],
                "rows_sha256": test["hashes"]["rows_sha256"],
                "positive_incidents_sha256": test["hashes"][
                    "positive_incidents_sha256"
                ],
                "class_balance": test["class_balance"],
            },
        },
    }
    return {**base, "manifest_sha256": canonical_sha256(base)}


def _combine_audits(
    train: Mapping[str, Any], test: Mapping[str, Any]
) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "study_id": train["study_id"],
        "policy": train["policy"],
        "splits": {"train": train, "test": test},
    }
    return {**base, "audit_sha256": canonical_sha256(base)}


def _verify_dataset(
    dataset: Mapping[str, Any], protocol: Mapping[str, Any], *, split: str
) -> None:
    if dataset.get("split") != split or dataset.get("study_id") != protocol.get(
        "study_id"
    ):
        raise RuntimeError(f"v4 {split} dataset identity mismatch")
    hashes = cast(Mapping[str, Any], dataset["hashes"])
    if hashes.get("protocol_sha256") != canonical_sha256(protocol):
        raise RuntimeError(f"v4 {split} dataset protocol mismatch")
    content = canonical_sha256(
        {
            "split": dataset["split"],
            "rows": dataset["rows"],
            "positive_incidents": dataset["positive_incidents"],
            "source": dataset["source"],
        }
    )
    if hashes.get("dataset_content_sha256") != content:
        raise RuntimeError(f"v4 {split} dataset content hash mismatch")


def _verify_audit(
    dataset: Mapping[str, Any],
    protocol: Mapping[str, Any],
    audit: Mapping[str, Any],
    *,
    split: str,
) -> None:
    if audit.get("split") != split or audit.get("study_id") != dataset.get(
        "study_id"
    ):
        raise RuntimeError(f"v4 {split} label-audit identity mismatch")
    unstamped = {
        key: value for key, value in audit.items() if key != "audit_sha256"
    }
    if canonical_sha256(unstamped) != audit.get("audit_sha256"):
        raise RuntimeError(f"v4 {split} label-audit hash mismatch")
    expected = build_audit_candidates(dataset, protocol)
    if audit.get("candidate_sample_sha256") != expected.get("sample_sha256"):
        raise RuntimeError(f"v4 {split} label-audit sample mismatch")


def render_markdown(scorecard: Mapping[str, Any]) -> str:
    power = cast(Mapping[str, Any], scorecard["power"])
    test = cast(Mapping[str, Any], scorecard["test"])
    metrics = cast(Mapping[str, Any], test["metrics"])
    burst = cast(Mapping[str, Any], scorecard["burst_ablation"])
    burst_metrics = cast(Mapping[str, Any], burst["metrics"])
    finding = cast(Mapping[str, Any], scorecard["finding"])
    population = cast(Mapping[str, Any], scorecard["population"])
    label_audit = cast(Mapping[str, Any], scorecard["label_audit"])
    test_audit = cast(Mapping[str, Any], label_audit["test"])
    ci = cast(Mapping[str, Sequence[float]], metrics["repository_bootstrap_95"])
    lines = [
        "# Sentinel detector v4 — powered frozen test scorecard",
        "",
        f"> Model and threshold were frozen at commit `{scorecard['model_frozen_commit']}` before the v4 test repositories were extracted.",
        "",
        "## Statistical power",
        "",
        "| Split | Candidate hours | Positive hours | Confirmed incidents | Target met |",
        "| --- | ---: | ---: | ---: | :---: |",
        f"| Train | {power['train_rows']} | {power['train_positive_hours']} | {power['train_positive_incidents']} | {_yes(power['train_target_met'])} |",
        f"| Test | {power['test_rows']} | {power['test_positive_hours']} | {power['test_positive_incidents']} | {_yes(power['test_target_met'])} |",
        "",
        "## Headline test result",
        "",
        "| Precision | Recall | ROC AUC | Median lead | Alerts |",
        "| ---: | ---: | ---: | ---: | ---: |",
        f"| {_percent(metrics['precision'])} | {_percent(metrics['recall'])} | {metrics['roc_auc']:.3f} | {_minutes(test['lead_time']['median_minutes'])} | {metrics['alerts']} |",
        "",
        f"Whole-repository bootstrap 95% intervals: precision {_interval(ci['precision'], percent=True)}, recall {_interval(ci['recall'], percent=True)}, ROC AUC {_interval(ci['roc_auc'])}.",
        "",
        "## Burst magnitude versus coherence",
        "",
        "| Frozen model | ROC AUC | Precision | Recall |",
        "| --- | ---: | ---: | ---: |",
        f"| Burst-only ablation | {burst_metrics['roc_auc']:.3f} | {_percent(burst_metrics['precision'])} | {_percent(burst_metrics['recall'])} |",
        f"| Coherence v4 | {metrics['roc_auc']:.3f} | {_percent(metrics['precision'])} | {_percent(metrics['recall'])} |",
        "",
        f"Coherence AUC delta: **{finding['roc_auc_delta']:+.3f}**. {finding['conclusion']}",
        "",
        "## Population correction",
        "",
        f"**{population['confirmed_incidents_outside_old_spike_population']}** test incidents ({population['confirmed_incidents_outside_old_spike_share']:.1%}) fell outside the old five-event spike gate. V3 could not learn or detect them.",
        "",
        "## Label sources and held-out audit",
        "",
        "| Source | Test incidents | Audited | Rejected | Obvious noise |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    source_counts = cast(Mapping[str, int], cast(Mapping[str, Any], population["test_source_attribution"])["by_source"])
    audit_sources = cast(Mapping[str, Mapping[str, Any]], test_audit["by_source"])
    for source in sorted(source_counts):
        audit = audit_sources[source]
        lines.append(
            f"| `{source}` | {source_counts[source]} | {audit['reviewed']} | {audit['rejected']} | {audit['obvious_label_noise_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            f"Predeclared per-source noise ceiling: {label_audit['maximum_obvious_label_noise_rate_per_source']:.1%}. Held-out label-quality target met: **{_yes(label_audit['test_target_met'])}**.",
        ]
    )
    lines.extend(
        [
            "",
            "## Recall by confirmation source",
            "",
            "| Source | Incidents | Caught | Recall |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for source, row in cast(Mapping[str, Mapping[str, Any]], test["source_recall"]).items():
        lines.append(
            f"| `{source}` | {row['incidents']} | {row['caught']} | {_percent(row['recall'])} |"
        )
    lines.extend(
        [
            "",
            "## Sample caught incidents",
            "",
            "| Candidate | Repository / issue | Source | Confirmation | Lead | Score |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    caught = cast(Sequence[Mapping[str, Any]], test["caught_incidents"])
    for incident in caught[:20]:
        lines.append(
            f"| [{incident['candidate_ts']}]({incident['candidate_url']}) | {incident['entity']} #{incident['issue_number']} | `{incident['label_source']}` | [{incident['confirmation_ts']}]({incident['confirmation_url']}) | {_minutes(incident['lead_minutes'])} | {incident['probability']:.3f} |"
        )
    if not caught:
        lines.append("| none | — | — | — | — | — |")
    identities = cast(Mapping[str, Any], scorecard["frozen_identities"])
    lines.extend(
        [
            "",
            "## Frozen identities",
            "",
            f"- Train dataset: `{identities['train_dataset_content_sha256']}`",
            f"- Test dataset: `{identities['test_dataset_content_sha256']}`",
            f"- Model: `{identities['model_sha256']}`",
            f"- Label audit: `{identities['label_audit_sha256']}`",
            f"- Scorecard: `{scorecard['scorecard_sha256']}`",
            "",
            "V1/v2/v3/v4 use different frozen corpora. Their progression documents iteration; it is not a same-sample leaderboard.",
            "",
        ]
    )
    return "\n".join(lines)


def _interval(values: Sequence[float], *, percent: bool = False) -> str:
    if percent:
        return f"[{values[0]:.1%}, {values[1]:.1%}]"
    return f"[{values[0]:.3f}, {values[1]:.3f}]"


def _minutes(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.1f} min"


def _percent(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.1%}"


def _number(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def _yes(value: object) -> str:
    return "yes" if bool(value) else "no"


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


def _read_gzip_object(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        raw = json.load(source)
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
