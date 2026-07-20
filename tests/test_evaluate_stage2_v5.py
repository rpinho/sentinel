import importlib.util
from pathlib import Path

from sentinel.detector_v3 import canonical_sha256


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate_stage2_v5.py"
SPEC = importlib.util.spec_from_file_location("evaluate_stage2_v5", SCRIPT)
assert SPEC and SPEC.loader
evaluate_stage2_v5 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_stage2_v5)


def test_evaluate_two_stage_weighted_system_and_clean_cut() -> None:
    protocol = {
        "study_id": "test-v5",
        "v4_frozen_identities": {
            "test_dataset_content_sha256": "dataset-test",
            "v4_scorecard_sha256": "scorecard-v4",
        },
        "stage2": {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "transport": "subscription",
            "effort": "medium",
        },
        "sampling": {
            "method": "test census",
            "stage1_alert_population": 4,
            "positive_alert_census": 2,
            "negative_alert_population": 2,
            "negative_alert_sample": 2,
            "total_stage2_requests": 4,
            "sample_sha256": "sample",
            "rows": [
                _sample("a-positive", "a/repo", "positive_census"),
                _sample("b-positive", "b/repo", "positive_census"),
                _sample("a-negative", "a/repo", "negative:a/repo:q0"),
                _sample("b-negative", "b/repo", "negative:b/repo:q0"),
            ],
        },
        "evaluation": {"bootstrap": {"replicates": 20, "seed": 1}},
    }
    dataset = {
        "hashes": {"dataset_content_sha256": "dataset-test"},
        "rows": [
            _row("a-positive", "a/repo", 1),
            _row("a-negative", "a/repo", 0),
            _row("b-positive", "b/repo", 1),
            _row("b-negative", "b/repo", 0),
        ],
        "positive_incidents": [
            _incident("a-incident", "a-positive", "a/repo", "completed_bug_closure"),
            _incident(
                "b-incident",
                "b-positive",
                "b/repo",
                "substantive_maintainer_comment",
            ),
        ],
    }
    predictions = [
        {"row_id": row_id, "predicted": True}
        for row_id in ["a-positive", "a-negative", "b-positive", "b-negative"]
    ]
    v4_scorecard = {
        "scorecard_sha256": "scorecard-v4",
        "power": {"test_rows": 4},
        "coverage": {"test_repository_names": ["a/repo", "b/repo"]},
        "test": {
            "predictions": predictions,
            "metrics": {
                "alerts": 4,
                "precision": 0.5,
                "recall": 1.0,
                "true_positive_hours": 2,
                "false_positive_hours": 2,
            },
            "incident_recall": 1.0,
            "caught_incidents": [{}, {}],
        },
    }
    requests_base = {
        "protocol_sha256": canonical_sha256(protocol),
        "requests": [
            {
                "row_id": row_id,
                "identity": {"user_content": "{}"},
                "evidence_manifest": {
                    "supplied_event_count": 1,
                    "supplied_event_json_chars": 100,
                },
            }
            for row_id in ["a-positive", "a-negative", "b-positive", "b-negative"]
        ],
    }
    requests = {
        **requests_base,
        "request_bundle_sha256": canonical_sha256(requests_base),
    }
    records = [
        _record("a-positive", True),
        _record("a-negative", True),
        _record("b-positive", False),
        _record("b-negative", False),
    ]
    verdict_base = {
        "protocol_sha256": canonical_sha256(protocol),
        "request_bundle_sha256": requests["request_bundle_sha256"],
        "records": records,
    }
    verdicts = {
        **verdict_base,
        "verdict_bundle_sha256": canonical_sha256(verdict_base),
    }

    result = evaluate_stage2_v5.evaluate(
        protocol=protocol,
        dataset=dataset,
        v4_scorecard=v4_scorecard,
        audit={"reviews": []},
        requests=requests,
        verdicts=verdicts,
    )

    assert result["end_to_end"]["precision"] == 0.5
    assert result["end_to_end"]["hour_recall"] == 0.5
    assert result["end_to_end"]["incident_recall"] == 0.5
    assert result["end_to_end"]["true_positive_hours_killed"] == 1
    assert result["clean_source_cut"]["precision"] == 0.5
    assert result["clean_source_cut"]["hour_recall"] == 1.0
    assert result["clean_source_cut"]["incident_recall"] == 1.0


def _sample(row_id: str, entity: str, stratum: str) -> dict:
    return {
        "row_id": row_id,
        "entity": entity,
        "stratum": stratum,
        "population_size": 1,
        "sample_size": 1,
        "analysis_weight": 1.0,
    }


def _row(row_id: str, entity: str, label: int) -> dict:
    return {"row_id": row_id, "entity": entity, "label": label}


def _incident(
    incident_id: str, row_id: str, entity: str, label_source: str
) -> dict:
    return {
        "incident_id": incident_id,
        "row_id": row_id,
        "entity": entity,
        "label_source": label_source,
        "lead_minutes": 60,
    }


def _record(row_id: str, real: bool) -> dict:
    base = {
        "row_id": row_id,
        "provider": "anthropic",
        "requested_model": "claude-opus-4-8",
        "response": {"real": real},
        "runtime": {
            "wall_duration_ms": 100,
            "total_cost_usd_equivalent": 0.01,
            "input_tokens": 100,
            "output_tokens": 10,
            "model_usage": {"claude-opus-4-8": {}},
        },
    }
    return {**base, "record_sha256": canonical_sha256(base)}
