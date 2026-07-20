import importlib.util
from pathlib import Path

from sentinel.model_v4 import finalize_model


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_detector_v4.py"
SPEC = importlib.util.spec_from_file_location("evaluate_detector_v4", SCRIPT)
assert SPEC and SPEC.loader
evaluate_detector_v4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_detector_v4)


def test_evaluate_v4_scores_frozen_model_and_future_confirmations(
    monkeypatch,
) -> None:
    monkeypatch.setattr(evaluate_detector_v4, "BOOTSTRAP_REPLICATES", 20)
    model = finalize_model(
        {
            "schema_version": 1,
            "detector_version": "sentinel-v4-test",
            "model_kind": "standardized_logistic_regression",
            "features": ["signal"],
            "threshold": 0.5,
            "linear": {
                "mean": [0.5],
                "scale": [0.5],
                "coefficients": [5.0],
                "intercept": 0.0,
            },
        }
    )
    rows = [
        _row("a-0", "a/repo", 0, 0.0),
        _row("a-1", "a/repo", 1, 1.0),
        _row("b-0", "b/repo", 0, 0.0),
        _row("b-1", "b/repo", 1, 1.0),
    ]
    incidents = [
        _incident("a-1", "a/repo", "2025-01-01T01:30:00Z", 30.0),
        _incident("b-1", "b/repo", "2025-01-01T02:00:00Z", 60.0),
    ]

    result = evaluate_detector_v4.evaluate_model(rows, incidents, model)

    assert result["metrics"]["precision"] == 1.0
    assert result["metrics"]["recall"] == 1.0
    assert result["metrics"]["roc_auc"] == 1.0
    assert result["incident_recall"] == 1.0
    assert result["lead_time"]["median_minutes"] == 45.0
    assert len(result["caught_incidents"]) == 2
    assert not result["misses"]


def _row(row_id: str, entity: str, label: int, signal: float) -> dict:
    return {
        "row_id": row_id,
        "entity": entity,
        "candidate_ts": "2025-01-01T01:00:00Z",
        "candidate_url": f"https://example.test/{row_id}",
        "features": {"signal": signal},
        "label": label,
    }


def _incident(
    row_id: str,
    entity: str,
    confirmation_ts: str,
    lead_minutes: float,
) -> dict:
    return {
        "incident_id": f"incident-{row_id}",
        "row_id": row_id,
        "entity": entity,
        "issue_number": 1,
        "candidate_ts": "2025-01-01T01:00:00Z",
        "confirmation_ts": confirmation_ts,
        "confirmation_url": f"https://example.test/confirm/{row_id}",
        "label_source": "completed_bug_closure",
        "lead_minutes": lead_minutes,
    }
