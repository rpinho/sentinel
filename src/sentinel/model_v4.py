"""Portable inference contract for frozen v4 linear models."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256

MODEL_SCHEMA_VERSION = 1


def finalize_model(document: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(document))
    result.pop("model_sha256", None)
    result.pop("detector_version", None)
    digest = canonical_sha256(result)
    result["model_sha256"] = digest
    result["detector_version"] = f"sha256:{digest[:12]}"
    return result


def verify_model(document: Mapping[str, Any]) -> None:
    if document.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise ValueError("unsupported detector-v4 model schema")
    if document.get("model_kind") != "standardized_logistic_regression":
        raise ValueError("unsupported detector-v4 model kind")
    expected = str(document.get("model_sha256") or "")
    unstamped = {
        key: value
        for key, value in document.items()
        if key not in {"model_sha256", "detector_version"}
    }
    if not expected or canonical_sha256(unstamped) != expected:
        raise ValueError("detector-v4 model hash mismatch")
    if document.get("detector_version") != f"sha256:{expected[:12]}":
        raise ValueError("detector-v4 version does not match its model hash")
    features = cast(Sequence[str], document.get("features") or ())
    linear = cast(Mapping[str, Any], document.get("linear") or {})
    for name in ("mean", "scale", "coefficients"):
        values = cast(Sequence[float], linear.get(name) or ())
        if len(values) != len(features):
            raise ValueError(f"detector-v4 {name} length mismatch")
    if any(float(value) <= 0 for value in linear["scale"]):
        raise ValueError("detector-v4 scale must be positive")


def predict_probability(
    features: Mapping[str, float], model: Mapping[str, Any]
) -> float:
    verify_model(model)
    names = cast(Sequence[str], model["features"])
    linear = cast(Mapping[str, Any], model["linear"])
    mean = cast(Sequence[float], linear["mean"])
    scale = cast(Sequence[float], linear["scale"])
    coefficients = cast(Sequence[float], linear["coefficients"])
    logit = float(linear["intercept"])
    for index, name in enumerate(names):
        standardized = (float(features.get(name, 0.0)) - mean[index]) / scale[index]
        logit += coefficients[index] * standardized
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1.0 + exp_logit)


def predict(features: Mapping[str, float], model: Mapping[str, Any]) -> bool:
    return predict_probability(features, model) >= float(model["threshold"])
