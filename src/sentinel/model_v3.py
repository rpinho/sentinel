"""Portable inference contract for Sentinel's frozen v3 decision trees."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256

MODEL_SCHEMA_VERSION = 1


def finalize_model(document: Mapping[str, Any]) -> dict[str, Any]:
    """Stamp a model document with a deterministic content identity."""

    result = deepcopy(dict(document))
    result.pop("model_sha256", None)
    result.pop("detector_version", None)
    digest = canonical_sha256(result)
    result["model_sha256"] = digest
    result["detector_version"] = f"sha256:{digest[:12]}"
    return result


def verify_model(document: Mapping[str, Any]) -> None:
    if document.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise ValueError("unsupported detector-v3 model schema")
    expected = str(document.get("model_sha256") or "")
    if not expected:
        raise ValueError("detector-v3 model has no content hash")
    unstamped = {
        key: value
        for key, value in document.items()
        if key not in {"model_sha256", "detector_version"}
    }
    if canonical_sha256(unstamped) != expected:
        raise ValueError("detector-v3 model hash mismatch")
    if document.get("detector_version") != f"sha256:{expected[:12]}":
        raise ValueError("detector-v3 version does not match its model hash")


def predict_probability(
    features: Mapping[str, float], model: Mapping[str, Any]
) -> float:
    """Traverse the committed tree without requiring sklearn at serving time."""

    verify_model(model)
    names = cast(Sequence[str], model["features"])
    tree = cast(Mapping[str, Sequence[Any]], model["tree"])
    left = cast(Sequence[int], tree["children_left"])
    right = cast(Sequence[int], tree["children_right"])
    feature_index = cast(Sequence[int], tree["feature_index"])
    thresholds = cast(Sequence[float], tree["threshold"])
    values = cast(Sequence[Sequence[float]], tree["class_values"])
    node = 0
    while left[node] != -1:
        index = feature_index[node]
        value = float(features.get(names[index], 0.0))
        node = left[node] if value <= thresholds[node] else right[node]
    negative, positive = (float(item) for item in values[node])
    total = negative + positive
    return positive / total if total else 0.0


def predict(features: Mapping[str, float], model: Mapping[str, Any]) -> bool:
    return predict_probability(features, model) >= float(model["threshold"])


def serialize_sklearn_tree(
    classifier: Any, feature_names: Sequence[str]
) -> dict[str, Any]:
    """Convert a fitted sklearn DecisionTreeClassifier to portable JSON."""

    tree = classifier.tree_
    return {
        "children_left": [int(value) for value in tree.children_left],
        "children_right": [int(value) for value in tree.children_right],
        "feature_index": [int(value) for value in tree.feature],
        "threshold": [float(value) for value in tree.threshold],
        "class_values": [
            [float(value) for value in row[0]] for row in tree.value
        ],
        "node_samples": [int(value) for value in tree.n_node_samples],
        "weighted_node_samples": [
            float(value) for value in tree.weighted_n_node_samples
        ],
        "feature_importance": {
            name: float(importance)
            for name, importance in sorted(
                zip(feature_names, classifier.feature_importances_),
                key=lambda item: (-item[1], item[0]),
            )
            if importance > 1e-12
        },
    }
