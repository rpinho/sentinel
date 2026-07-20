from sentinel.model_v4 import finalize_model, predict, predict_probability, verify_model


def _model() -> dict[str, object]:
    return finalize_model(
        {
            "schema_version": 1,
            "model_kind": "standardized_logistic_regression",
            "features": ["coherence"],
            "threshold": 0.6,
            "linear": {
                "mean": [1.0],
                "scale": [2.0],
                "coefficients": [2.0],
                "intercept": 0.0,
            },
        }
    )


def test_portable_v4_linear_inference_and_identity() -> None:
    model = _model()
    verify_model(model)

    assert predict_probability({"coherence": 1.0}, model) == 0.5
    assert predict_probability({"coherence": 3.0}, model) > 0.88
    assert not predict({"coherence": 1.0}, model)
    assert predict({"coherence": 3.0}, model)


def test_v4_model_rejects_mutation() -> None:
    model = _model()
    model["threshold"] = 0.99

    try:
        verify_model(model)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("mutated model unexpectedly passed verification")
