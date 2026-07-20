#!/usr/bin/env python3
"""Select and freeze v4 from the train split only."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

from sentinel.detector_v3 import canonical_sha256
from sentinel.detector_v4 import build_audit_candidates
from sentinel.model_v4 import MODEL_SCHEMA_VERSION, finalize_model, verify_model

DEFAULT_PROTOCOL = Path("holdout/v4/study-protocol.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-train.json.gz")
DEFAULT_MODEL = Path("holdout/v4/model.json")
DEFAULT_BURST_MODEL = Path("holdout/v4/burst-model.json")
DEFAULT_REPORT = Path("holdout/v4/training-report.json")
DEFAULT_AUDIT = Path("holdout/v4/label-audit-train.json")
C_VALUES = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0)
PENALTIES = ("l1", "l2")
THRESHOLDS = tuple(round(index * 0.01, 2) for index in range(1, 100))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--burst-model", type=Path, default=DEFAULT_BURST_MODEL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()

    protocol = _read_object(args.protocol)
    dataset = _read_gzip_object(args.dataset)
    audit = _read_object(args.audit)
    _verify_train_dataset(dataset, protocol)
    _verify_train_audit(dataset, protocol, audit)
    rows = cast(Sequence[Mapping[str, Any]], dataset["rows"])
    feature_names = cast(Mapping[str, Sequence[str]], dataset["feature_names"])
    primary, primary_search = select_model(
        rows=rows,
        features=feature_names["coherence"],
        family="coherence",
        dataset=dataset,
    )
    burst, burst_search = select_model(
        rows=rows,
        features=feature_names["burst"],
        family="burst",
        dataset=dataset,
    )
    _write_json(args.model, primary)
    _write_json(args.burst_model, burst)

    balance = cast(Mapping[str, Any], dataset["class_balance"])
    by_repo = cast(Mapping[str, Mapping[str, Any]], balance["by_repository"])
    positive_incidents = int(balance["positive_incidents"])
    max_repo = max(
        (
            (repo, int(values["positive_incidents"]))
            for repo, values in by_repo.items()
        ),
        key=lambda item: item[1],
    )
    targets = cast(Mapping[str, Any], protocol["power_targets"])
    report = {
        "schema_version": 1,
        "study_id": dataset["study_id"],
        "dataset_content_sha256": dataset["hashes"]["dataset_content_sha256"],
        "protocol_sha256": dataset["hashes"]["protocol_sha256"],
        "selection_protocol": protocol["model_policy"],
        "test_rows_used": 0,
        "train_rows": len(rows),
        "train_positive_hours": int(balance["positive_hours"]),
        "train_positive_incidents": positive_incidents,
        "train_repositories": sorted({str(row["entity"]) for row in rows}),
        "source_attribution": dataset["source_attribution"],
        "label_audit": {
            "audit_sha256": audit["audit_sha256"],
            "candidate_sample_sha256": audit["candidate_sample_sha256"],
            "counts": audit["counts"],
            "by_source": audit["by_source"],
        },
        "power": {
            "minimum_train_positive_incidents": targets[
                "minimum_train_positive_incidents"
            ],
            "achieved_train_positive_incidents": positive_incidents,
            "maximum_single_repository_positive_share": targets[
                "maximum_single_train_repository_positive_share"
            ],
            "observed_largest_repository": max_repo[0],
            "observed_largest_repository_positives": max_repo[1],
            "observed_largest_repository_positive_share": (
                max_repo[1] / positive_incidents if positive_incidents else 0.0
            ),
        },
        "primary": _report_model(primary),
        "burst_ablation": _report_model(burst),
        "search": {"coherence": primary_search, "burst": burst_search},
        "sklearn_version": sklearn.__version__,
    }
    _write_json(args.report, report)
    print(
        "V4_TRAIN "
        f"rows={len(rows)} positives={positive_incidents} "
        f"model={primary['detector_version']} "
        f"oof_f1={primary['selection']['oof_metrics']['f1']:.3f} "
        f"oof_auc={primary['selection']['oof_metrics']['roc_auc']:.3f} "
        f"burst_auc={burst['selection']['oof_metrics']['roc_auc']:.3f} "
        "test_rows_used=0"
    )


def select_model(
    *,
    rows: Sequence[Mapping[str, Any]],
    features: Sequence[str],
    family: str,
    dataset: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    matrix = np.asarray(
        [
            [float(cast(Mapping[str, Any], row["features"])[name]) for name in features]
            for row in rows
        ],
        dtype=float,
    )
    labels = np.asarray([int(row["label"]) for row in rows], dtype=int)
    groups = np.asarray([str(row["entity"]) for row in rows])
    if len(np.unique(labels)) != 2:
        raise RuntimeError("v4 training requires both classes")
    folds = tuple(LeaveOneGroupOut().split(matrix, labels, groups))
    candidates: list[dict[str, Any]] = []
    best: tuple[tuple[float, ...], dict[str, Any], np.ndarray] | None = None
    for penalty in PENALTIES:
        for regularization in C_VALUES:
            probabilities = np.zeros(len(labels), dtype=float)
            for train, validation in folds:
                scaler = StandardScaler().fit(matrix[train])
                classifier = _classifier(
                    penalty=penalty, regularization=regularization
                )
                classifier.fit(
                    scaler.transform(matrix[train]),
                    labels[train],
                    sample_weight=_repository_class_weights(
                        labels[train], groups[train]
                    ),
                )
                probabilities[validation] = classifier.predict_proba(
                    scaler.transform(matrix[validation])
                )[:, 1]
            for threshold in THRESHOLDS:
                metrics = _metrics(
                    labels, probabilities >= threshold, probabilities, groups
                )
                candidate = {
                    "penalty": penalty,
                    "C": regularization,
                    "threshold": threshold,
                    "metrics": metrics,
                }
                candidates.append(candidate)
                key = (
                    metrics["f1"],
                    metrics["precision"],
                    metrics["recall"],
                    metrics["roc_auc"],
                    metrics["repository_macro_auc"],
                    threshold,
                    -regularization,
                    float(penalty == "l1"),
                )
                if best is None or key > best[0]:
                    best = (key, candidate, probabilities.copy())
    assert best is not None
    selected = best[1]
    scaler = StandardScaler().fit(matrix)
    classifier = _classifier(
        penalty=str(selected["penalty"]),
        regularization=float(selected["C"]),
    )
    classifier.fit(
        scaler.transform(matrix),
        labels,
        sample_weight=_repository_class_weights(labels, groups),
    )
    oof_rows = [
        {
            "row_id": row["row_id"],
            "label": int(label),
            "probability": round(float(probability), 12),
        }
        for row, label, probability in zip(rows, labels, best[2])
    ]
    coefficients = [float(value) for value in classifier.coef_[0]]
    importance_denominator = sum(abs(value) for value in coefficients)
    feature_importance = {
        name: abs(value) / importance_denominator
        for name, value in sorted(
            zip(features, coefficients), key=lambda item: (-abs(item[1]), item[0])
        )
        if abs(value) > 1e-12 and importance_denominator
    }
    base = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "study_id": dataset["study_id"],
        "model_kind": "standardized_logistic_regression",
        "feature_family": family,
        "features": list(features),
        "threshold": selected["threshold"],
        "hyperparameters": {
            "penalty": selected["penalty"],
            "C": selected["C"],
            "solver": "liblinear",
            "max_iter": 2000,
            "random_state": 0,
            "repository_class_weighted": True,
        },
        "training": {
            "dataset_content_sha256": dataset["hashes"][
                "dataset_content_sha256"
            ],
            "split": "train",
            "rows": len(rows),
            "positive_hours": int(labels.sum()),
            "repositories": sorted(set(groups.tolist())),
        },
        "selection": {
            "method": "leave_one_repository_out",
            "metric": "row-level F1",
            "candidates_evaluated": len(PENALTIES)
            * len(C_VALUES)
            * len(THRESHOLDS),
            "oof_metrics": selected["metrics"],
            "oof_predictions_sha256": canonical_sha256(oof_rows),
        },
        "linear": {
            "mean": [float(value) for value in scaler.mean_],
            "scale": [float(value) for value in scaler.scale_],
            "coefficients": coefficients,
            "intercept": float(classifier.intercept_[0]),
            "feature_importance": feature_importance,
        },
        "sklearn_version": sklearn.__version__,
    }
    artifact = finalize_model(base)
    verify_model(artifact)
    summaries = sorted(
        candidates,
        key=lambda row: (
            -float(cast(Mapping[str, Any], row["metrics"])["f1"]),
            -float(cast(Mapping[str, Any], row["metrics"])["precision"]),
            -float(row["threshold"]),
        ),
    )[:25]
    return artifact, summaries


def _classifier(*, penalty: str, regularization: float) -> LogisticRegression:
    return LogisticRegression(
        penalty=penalty,
        C=regularization,
        solver="liblinear",
        max_iter=2000,
        random_state=0,
    )


def _repository_class_weights(
    labels: np.ndarray, groups: np.ndarray
) -> np.ndarray:
    weights = np.zeros(len(labels), dtype=float)
    repositories = np.unique(groups)
    for repo in repositories:
        repo_indices = np.flatnonzero(groups == repo)
        repo_labels = labels[repo_indices]
        classes = np.unique(repo_labels)
        for label in classes:
            class_indices = repo_indices[repo_labels == label]
            weights[class_indices] = 1.0 / (
                len(repositories) * len(classes) * len(class_indices)
            )
    return weights * (len(weights) / weights.sum())


def _metrics(
    labels: np.ndarray,
    predictions: np.ndarray,
    probabilities: np.ndarray,
    groups: np.ndarray,
) -> dict[str, float]:
    repository_aucs = []
    for repo in np.unique(groups):
        selected = groups == repo
        if len(np.unique(labels[selected])) == 2:
            repository_aucs.append(
                float(roc_auc_score(labels[selected], probabilities[selected]))
            )
    return {
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
        "average_precision": float(average_precision_score(labels, probabilities)),
        "repository_macro_auc": (
            float(np.mean(repository_aucs)) if repository_aucs else 0.5
        ),
    }


def _verify_train_dataset(
    dataset: Mapping[str, Any], protocol: Mapping[str, Any]
) -> None:
    if dataset.get("split") != "train":
        raise RuntimeError("v4 model selection accepts only the train split")
    if dataset.get("study_id") != protocol.get("study_id"):
        raise RuntimeError("v4 dataset and protocol study IDs differ")
    hashes = cast(Mapping[str, Any], dataset["hashes"])
    if hashes.get("protocol_sha256") != canonical_sha256(protocol):
        raise RuntimeError("v4 train dataset does not match the frozen protocol")
    content = canonical_sha256(
        {
            "split": dataset["split"],
            "rows": dataset["rows"],
            "positive_incidents": dataset["positive_incidents"],
            "source": dataset["source"],
        }
    )
    if hashes.get("dataset_content_sha256") != content:
        raise RuntimeError("v4 train dataset content hash mismatch")
    balance = cast(Mapping[str, Any], dataset["class_balance"])
    targets = cast(Mapping[str, Any], protocol["power_targets"])
    positives = int(balance["positive_incidents"])
    if positives < int(targets["minimum_train_positive_incidents"]):
        raise RuntimeError(
            f"v4 train power target missed: {positives} incidents"
        )
    by_repo = cast(Mapping[str, Mapping[str, Any]], balance["by_repository"])
    largest = max(int(row["positive_incidents"]) for row in by_repo.values())
    if largest / positives > float(
        targets["maximum_single_train_repository_positive_share"]
    ):
        raise RuntimeError("one repository dominates v4 training positives")


def _verify_train_audit(
    dataset: Mapping[str, Any],
    protocol: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> None:
    if audit.get("split") != "train" or audit.get("study_id") != dataset.get(
        "study_id"
    ):
        raise RuntimeError("v4 train label audit identity mismatch")
    unstamped = {
        key: value for key, value in audit.items() if key != "audit_sha256"
    }
    if canonical_sha256(unstamped) != audit.get("audit_sha256"):
        raise RuntimeError("v4 train label audit hash mismatch")
    expected_sample = build_audit_candidates(dataset, protocol)
    if audit.get("candidate_sample_sha256") != expected_sample.get(
        "sample_sha256"
    ):
        raise RuntimeError("v4 train label audit sample mismatch")
    targets = cast(Mapping[str, Any], protocol["power_targets"])
    maximum_noise = float(targets["maximum_obvious_label_noise_rate_per_source"])
    by_source = cast(Mapping[str, Mapping[str, Any]], audit.get("by_source") or {})
    expected_sources = set(cast(Mapping[str, Any], dataset["source_attribution"])["by_source"])
    if set(by_source) != expected_sources:
        raise RuntimeError("v4 train label audit does not cover every source")
    for source, result in by_source.items():
        if int(result["reviewed"]) == 0:
            raise RuntimeError(f"v4 train label source {source} was not audited")
        if float(result["obvious_label_noise_rate"]) > maximum_noise:
            raise RuntimeError(f"v4 train label source {source} is too noisy")


def _report_model(model: Mapping[str, Any]) -> dict[str, Any]:
    linear = cast(Mapping[str, Any], model["linear"])
    return {
        "detector_version": model["detector_version"],
        "model_sha256": model["model_sha256"],
        "threshold": model["threshold"],
        "hyperparameters": model["hyperparameters"],
        "selection": model["selection"],
        "feature_importance": linear["feature_importance"],
    }


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
