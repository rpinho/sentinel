#!/usr/bin/env python3
"""Select and freeze v3 from train repositories only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
import sklearn
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.tree import DecisionTreeClassifier

from sentinel.model_v3 import (
    MODEL_SCHEMA_VERSION,
    finalize_model,
    serialize_sklearn_tree,
    verify_model,
)

DEFAULT_DATASET = Path("holdout/v3/dataset.json")
DEFAULT_MODEL = Path("holdout/v3/model.json")
DEFAULT_BURST_MODEL = Path("holdout/v3/burst-model.json")
DEFAULT_REPORT = Path("holdout/v3/training-report.json")
DEPTHS = (1, 2, 3, 4)
MIN_LEAVES = (2, 5, 10, 15, 20, 30)
THRESHOLDS = tuple(round(index * 0.025, 3) for index in range(1, 40))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--burst-model", type=Path, default=DEFAULT_BURST_MODEL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    dataset = _read_object(args.dataset)
    train_rows = [
        row
        for row in cast(Sequence[Mapping[str, Any]], dataset["rows"])
        if row["split"] == "train"
    ]
    if not train_rows or any(row["split"] != "train" for row in train_rows):
        raise RuntimeError("v3 training input is not train-only")
    feature_names = cast(Mapping[str, Sequence[str]], dataset["feature_names"])
    coherence, coherence_search = select_model(
        rows=train_rows,
        features=feature_names["coherence"],
        family="coherence",
        dataset=dataset,
    )
    burst, burst_search = select_model(
        rows=train_rows,
        features=feature_names["burst"],
        family="burst",
        dataset=dataset,
    )
    _write_json(args.model, coherence)
    _write_json(args.burst_model, burst)
    report = {
        "schema_version": 1,
        "study_id": dataset["study_id"],
        "dataset_content_sha256": dataset["hashes"]["dataset_content_sha256"],
        "selection_protocol": (
            "Leave-one-repository-out predictions across train repositories; "
            "maximize row-level F1, then precision, recall, ROC AUC, and simplicity."
        ),
        "test_rows_used": 0,
        "train_rows": len(train_rows),
        "train_positive_hours": sum(int(row["label"]) for row in train_rows),
        "train_repositories": sorted({str(row["entity"]) for row in train_rows}),
        "primary": {
            "detector_version": coherence["detector_version"],
            "model_sha256": coherence["model_sha256"],
            "selection": coherence["selection"],
            "feature_importance": coherence["tree"]["feature_importance"],
        },
        "burst_ablation": {
            "detector_version": burst["detector_version"],
            "model_sha256": burst["model_sha256"],
            "selection": burst["selection"],
            "feature_importance": burst["tree"]["feature_importance"],
        },
        "search": {
            "coherence": coherence_search,
            "burst": burst_search,
        },
        "sklearn_version": sklearn.__version__,
    }
    _write_json(args.report, report)
    print(
        "V3_TRAIN "
        f"model={coherence['detector_version']} "
        f"oof_f1={coherence['selection']['oof_metrics']['f1']:.3f} "
        f"oof_auc={coherence['selection']['oof_metrics']['roc_auc']:.3f} "
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
        [[float(cast(Mapping[str, Any], row["features"])[name]) for name in features] for row in rows]
    )
    labels = np.asarray([int(row["label"]) for row in rows])
    groups = np.asarray([str(row["entity"]) for row in rows])
    folds = tuple(LeaveOneGroupOut().split(matrix, labels, groups))
    candidates: list[dict[str, Any]] = []
    best: tuple[tuple[float, ...], dict[str, Any], np.ndarray] | None = None
    for depth in DEPTHS:
        for minimum_leaf in MIN_LEAVES:
            probabilities = np.zeros(len(labels))
            for train, validation in folds:
                classifier = _classifier(depth=depth, minimum_leaf=minimum_leaf)
                classifier.fit(matrix[train], labels[train])
                probabilities[validation] = classifier.predict_proba(
                    matrix[validation]
                )[:, 1]
            auc = float(roc_auc_score(labels, probabilities))
            for threshold in THRESHOLDS:
                predictions = probabilities >= threshold
                metrics = _metrics(labels, predictions, probabilities)
                candidate = {
                    "max_depth": depth,
                    "min_samples_leaf": minimum_leaf,
                    "threshold": threshold,
                    "metrics": metrics,
                }
                candidates.append(candidate)
                key = (
                    metrics["f1"],
                    metrics["precision"],
                    metrics["recall"],
                    auc,
                    -float(depth),
                    float(minimum_leaf),
                    float(threshold),
                )
                if best is None or key > best[0]:
                    best = (key, candidate, probabilities.copy())
    assert best is not None
    selected = best[1]
    classifier = _classifier(
        depth=int(selected["max_depth"]),
        minimum_leaf=int(selected["min_samples_leaf"]),
    )
    classifier.fit(matrix, labels)
    oof_rows = [
        {
            "row_id": row["row_id"],
            "entity": row["entity"],
            "label": int(row["label"]),
            "probability": round(float(probability), 12),
            "predicted": bool(probability >= float(selected["threshold"])),
        }
        for row, probability in zip(rows, best[2])
    ]
    base = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "study_id": dataset["study_id"],
        "model_kind": "decision_tree_classifier",
        "feature_family": family,
        "features": list(features),
        "threshold": selected["threshold"],
        "hyperparameters": {
            "max_depth": selected["max_depth"],
            "min_samples_leaf": selected["min_samples_leaf"],
            "class_weight": "balanced",
            "random_state": 0,
        },
        "training": {
            "dataset_content_sha256": dataset["hashes"]["dataset_content_sha256"],
            "split": "train",
            "rows": len(rows),
            "positive_hours": int(labels.sum()),
            "repositories": sorted(set(groups.tolist())),
        },
        "selection": {
            "method": "leave_one_repository_out",
            "metric": "F1",
            "candidates_evaluated": len(DEPTHS) * len(MIN_LEAVES) * len(THRESHOLDS),
            "oof_metrics": selected["metrics"],
            "oof_predictions": oof_rows,
        },
        "tree": serialize_sklearn_tree(classifier, features),
        "sklearn_version": sklearn.__version__,
    }
    artifact = finalize_model(base)
    verify_model(artifact)
    summaries = sorted(
        candidates,
        key=lambda row: (
            -float(cast(Mapping[str, Any], row["metrics"])["f1"]),
            -float(cast(Mapping[str, Any], row["metrics"])["precision"]),
            int(row["max_depth"]),
            -int(row["min_samples_leaf"]),
            -float(row["threshold"]),
        ),
    )[:25]
    return artifact, summaries


def _classifier(*, depth: int, minimum_leaf: int) -> DecisionTreeClassifier:
    return DecisionTreeClassifier(
        max_depth=depth,
        min_samples_leaf=minimum_leaf,
        class_weight="balanced",
        random_state=0,
    )


def _metrics(
    labels: np.ndarray, predictions: np.ndarray, probabilities: np.ndarray
) -> dict[str, float]:
    return {
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(labels, probabilities)),
    }


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
