#!/usr/bin/env python3
"""Freeze the rich-evidence v6 stage-two evaluation protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json
from sentinel.stage2_v6 import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CLI_VERSION,
    DEFAULT_EFFORT,
    DEFAULT_EVENT_BUDGET_CHARS,
    DEFAULT_MAX_BUDGET_USD_PER_BATCH,
    DEFAULT_MAX_EVENT_TEXT_CHARS,
    DEFAULT_MAX_EVIDENCE_EVENTS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_RECENT_CONTEXT_HOURS,
    DEFAULT_SOURCE_LOOKBACK_HOURS,
    EVIDENCE_POLICY_VERSION,
    PROTOCOL_SCHEMA_VERSION,
    SYSTEM_PROMPT,
    TASK,
    TRANSPORT_VERSION,
    batch_schema,
)

DEFAULT_V5_PROTOCOL = Path("holdout/v5/protocol.json")
DEFAULT_V5_SCORECARD = Path("holdout/v5/scorecard.json")
DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_V4_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_OUTPUT = Path("holdout/v6/protocol.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v5-protocol", type=Path, default=DEFAULT_V5_PROTOCOL)
    parser.add_argument("--v5-scorecard", type=Path, default=DEFAULT_V5_SCORECARD)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--v4-scorecard", type=Path, default=DEFAULT_V4_SCORECARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    v5_protocol = _read_object(args.v5_protocol)
    v5_scorecard = _read_object(args.v5_scorecard)
    dataset = read_gzip_json(args.dataset)
    v4_scorecard = _read_object(args.v4_scorecard)
    sampling = cast(Mapping[str, Any], v5_protocol["sampling"])
    sample_rows = cast(Sequence[Mapping[str, Any]], sampling["rows"])
    if len(sample_rows) != int(sampling["total_stage2_requests"]):
        raise RuntimeError("v5 sample is incomplete")
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != cast(
        Mapping[str, Any], v5_protocol["v4_frozen_identities"]
    )["test_dataset_content_sha256"]:
        raise RuntimeError("v6 dataset differs from the frozen v5/v4 test")
    candidate_count = len(sample_rows)
    tail = candidate_count % DEFAULT_BATCH_SIZE
    schema_counts = sorted({DEFAULT_BATCH_SIZE, tail or DEFAULT_BATCH_SIZE})
    v4_test = cast(Mapping[str, Any], v4_scorecard["test"])
    v4_metrics = cast(Mapping[str, Any], v4_test["metrics"])
    protocol = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "study_id": "sentinel-rich-evidence-stage2-v6",
        "version": "v6",
        "objective": (
            "Test the two-stage architecture with v1-style rich pre-candidate evidence "
            "in scheduled-size batches, preserving the exact v5 evaluation sample."
        ),
        "integrity": {
            "v4_stage1_unchanged": True,
            "v4_test_split_reused_exactly": True,
            "v5_sample_reused_exactly": True,
            "test_labels_never_enter_stage2_requests": True,
            "all_stage2_evidence_strictly_pre_candidate": True,
            "no_openai_calls": True,
            "fresh_holdout_claim": False,
            "limitation": (
                "V6 reuses the published v4 test and v5 evaluation sample. It tests "
                "evidence sufficiency, not a new untouched holdout."
            ),
        },
        "frozen_identities": {
            "v5_protocol_sha256": canonical_sha256(v5_protocol),
            "v5_sample_sha256": sampling["sample_sha256"],
            "v5_scorecard_sha256": v5_scorecard["scorecard_sha256"],
            "v4_scorecard_sha256": v4_scorecard["scorecard_sha256"],
            "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])[
                "dataset_content_sha256"
            ],
        },
        "stage1": {
            "candidate_hours": cast(Mapping[str, Any], v4_scorecard["power"])["test_rows"],
            "alerts": v4_metrics["alerts"],
            "true_positive_hours": v4_metrics["true_positive_hours"],
            "false_positive_hours": v4_metrics["false_positive_hours"],
            "missed_positive_hours": v4_metrics["missed_positive_hours"],
            "precision": v4_metrics["precision"],
            "recall": v4_metrics["recall"],
            "incident_recall": v4_test["incident_recall"],
        },
        "stage2": {
            "provider": DEFAULT_PROVIDER,
            "model": DEFAULT_MODEL,
            "transport": TRANSPORT_VERSION,
            "claude_cli_version": DEFAULT_CLI_VERSION,
            "authentication": (
                "Claude Code claude.ai subscription; Anthropic and OpenAI API "
                "credentials removed from every subprocess."
            ),
            "effort": DEFAULT_EFFORT,
            "max_budget_usd_per_batch": DEFAULT_MAX_BUDGET_USD_PER_BATCH,
            "system_prompt": SYSTEM_PROMPT,
            "task": TASK,
            "verdict_rule": "A candidate survives exactly when structured real=true.",
            "evidence_grounding_rule": (
                "Ungrounded annotations are dropped and audited; real=true with text "
                "but no remaining exact quote is deterministically downgraded to false."
            ),
            "structured_schema_sha256_by_candidate_count": {
                str(count): canonical_sha256(batch_schema(count))
                for count in schema_counts
            },
            "stage1_threshold": v4_test["threshold"],
            "stage1_detector_version": v4_test["detector_version"],
        },
        "evidence_policy": {
            "version": EVIDENCE_POLICY_VERSION,
            "source_lookback_hours": DEFAULT_SOURCE_LOOKBACK_HOURS,
            "recent_context_hours": DEFAULT_RECENT_CONTEXT_HOURS,
            "event_budget_chars": DEFAULT_EVENT_BUDGET_CHARS,
            "max_event_text_chars": DEFAULT_MAX_EVENT_TEXT_CHARS,
            "max_evidence_events": DEFAULT_MAX_EVIDENCE_EVENTS,
            "full_stage1_feature_vector_sent": True,
            "full_source_window_sent_to_model": False,
            "full_source_window_hashed_for_audit": True,
            "selection": (
                "Candidate-hour breakage reports, prior releases, other candidate-hour "
                "events, recent issues, human comments, then automation-suspect events; "
                "final evidence is chronological."
            ),
        },
        "batching": {
            "mode": "one_agent_invocation_per_scheduled_size_candidate_batch",
            "ordering": "candidate_ts, entity, row_id",
            "candidates_per_batch": DEFAULT_BATCH_SIZE,
            "candidate_count": candidate_count,
            "batch_count": (candidate_count + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE,
            "production_interpretation": (
                "A live 15-60 minute detector run sends its complete candidate set once. "
                "Historical evaluation candidates span many intervals and are grouped "
                "only to reproduce that scheduled-run scale."
            ),
        },
        "sampling": dict(sampling),
        "evaluation": {
            "primary_unit": "repository UTC hour",
            "negative_estimator": "inverse-probability weighted within frozen v5 strata",
            "bootstrap": {"method": "whole-repository plus within-negative-stratum bootstrap", "replicates": 2000, "seed": 20260720},
            "clean_sources": ["completed_bug_closure", "merged_fixing_pr"],
            "full_label_result": "descriptive because v4 comment-source audit noise is 30%",
            "automation_slice": (
                "Predeclared content/actor/repetition heuristic; report candidate count "
                "and stage-two rejection rate descriptively."
            ),
        },
        "cost_gate": {
            "checkpoint_equivalent_usd": 60.0,
            "action": "Stop before uncached batches beyond the gate and tell Ricardo.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "V6_PROTOCOL "
        f"candidates={candidate_count} batches={protocol['batching']['batch_count']} "
        f"protocol_sha256={canonical_sha256(protocol)}"
    )


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
