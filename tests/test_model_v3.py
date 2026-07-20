from sentinel.model_v3 import finalize_model, predict, predict_probability, verify_model


def _model() -> dict[str, object]:
    return finalize_model(
        {
            "schema_version": 1,
            "features": ["coherence"],
            "threshold": 0.6,
            "tree": {
                "children_left": [1, -1, -1],
                "children_right": [2, -1, -1],
                "feature_index": [0, -2, -2],
                "threshold": [0.5, -2.0, -2.0],
                "class_values": [[1.0, 1.0], [9.0, 1.0], [1.0, 9.0]],
            },
        }
    )


def test_portable_v3_tree_inference_and_identity() -> None:
    model = _model()

    verify_model(model)

    assert predict_probability({"coherence": 0.1}, model) == 0.1
    assert predict_probability({"coherence": 0.9}, model) == 0.9
    assert not predict({"coherence": 0.1}, model)
    assert predict({"coherence": 0.9}, model)


def test_v3_model_rejects_mutation() -> None:
    model = _model()
    model["threshold"] = 0.99

    try:
        verify_model(model)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("mutated model unexpectedly passed verification")
