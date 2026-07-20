#!/usr/bin/env python3
"""Freeze the v5 two-stage protocol and deterministic evaluation sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import (
    DEFAULT_CLI_VERSION,
    DEFAULT_EVENT_BUDGET_CHARS,
    DEFAULT_EFFORT,
    DEFAULT_MAX_BUDGET_USD_PER_RUN,
    DEFAULT_MAX_EVENT_TEXT_CHARS,
    DEFAULT_MAX_EVIDENCE_EVENTS,
    DEFAULT_MODEL,
    DEFAULT_NEGATIVES_PER_REPOSITORY_QUARTILE,
    DEFAULT_PROVIDER,
    DEFAULT_RECENT_CONTEXT_HOURS,
    DEFAULT_SOURCE_LOOKBACK_HOURS,
    EVIDENCE_POLICY_VERSION,
    PROTOCOL_SCHEMA_VERSION,
    STAGE2_SYSTEM_PROMPT,
    STAGE2_TASK,
    TRANSPORT_VERSION,
    build_batch_schema,
    build_sample,
    read_gzip_json,
)

DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_OUTPUT = Path("holdout/v5/protocol.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = read_gzip_json(args.dataset)
    scorecard = _read_object(args.scorecard)
    _verify_v4(dataset, scorecard)
    rows = cast(list[Mapping[str, Any]], dataset["rows"])
    predictions = cast(
        list[Mapping[str, Any]],
        cast(Mapping[str, Any], scorecard["test"])["predictions"],
    )
    sample = build_sample(
        rows,
        predictions,
        negatives_per_quartile=DEFAULT_NEGATIVES_PER_REPOSITORY_QUARTILE,
    )
    identities = cast(Mapping[str, Any], scorecard["frozen_identities"])
    test = cast(Mapping[str, Any], scorecard["test"])
    metrics = cast(Mapping[str, Any], test["metrics"])
    protocol = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "study_id": "sentinel-two-stage-v5",
        "objective": (
            "Measure the frozen v4 high-recall ranker followed by a Claude precision "
            "filter on the already-published v4 whole-repository test split."
        ),
        "integrity": {
            "v4_stage1_unchanged": True,
            "v4_test_split_reused_exactly": True,
            "test_labels_never_enter_stage2_requests": True,
            "all_stage2_evidence_strictly_pre_candidate": True,
            "no_openai_calls": True,
            "fresh_holdout_claim": False,
            "limitation": (
                "V5 reuses a published test split whose aggregate labels and v4 audit "
                "were visible before the stage-two protocol. It is an end-to-end "
                "architecture measurement, not a new untouched-holdout claim."
            ),
        },
        "v4_frozen_identities": {
            "model_frozen_commit": scorecard["model_frozen_commit"],
            "detector_version": identities["detector_version"],
            "model_sha256": identities["model_sha256"],
            "test_dataset_content_sha256": identities[
                "test_dataset_content_sha256"
            ],
            "v4_scorecard_sha256": scorecard["scorecard_sha256"],
            "v4_label_audit_sha256": identities["label_audit_sha256"],
        },
        "stage1": {
            "candidate_hours": cast(Mapping[str, Any], scorecard["power"])[
                "test_rows"
            ],
            "alerts": metrics["alerts"],
            "true_positive_hours": metrics["true_positive_hours"],
            "false_positive_hours": metrics["false_positive_hours"],
            "missed_positive_hours": metrics["missed_positive_hours"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "incident_recall": test["incident_recall"],
        },
        "stage2": {
            "provider": DEFAULT_PROVIDER,
            "model": DEFAULT_MODEL,
            "model_choice": (
                "Ricardo requested the smarter Opus tier before the protocol and "
                "requests were frozen; the Claude CLI resolved opus to this exact ID."
            ),
            "transport": TRANSPORT_VERSION,
            "claude_cli_version": DEFAULT_CLI_VERSION,
            "effort": DEFAULT_EFFORT,
            "prompt_suggestions": False,
            "authentication": (
                "Claude Code claude.ai Max subscription; ANTHROPIC_API_KEY, "
                "ANTHROPIC_AUTH_TOKEN, and OPENAI_API_KEY removed from every subprocess."
            ),
            "invocation_mode": "one_compact_batch_per_scheduled_detector_run",
            "production_cadence": "one agent invocation every 15 to 60 minutes",
            "evaluation_batch_count": 1,
            "target_batch_wall_seconds": 300,
            "verdict_rule": "Stage-two alert survives exactly when structured real=true.",
            "evidence_grounding_rule": (
                "Markdown-equivalent quotes are restored to exact source substrings; "
                "remaining ungrounded annotations are dropped and audited. A real=true "
                "verdict with textual events must retain at least one exact quote."
            ),
            "system_prompt": STAGE2_SYSTEM_PROMPT,
            "task": STAGE2_TASK,
            "structured_schema_sha256": canonical_sha256(
                build_batch_schema(
                    sorted(
                        str(row["row_id"])
                        for row in cast(
                            Sequence[Mapping[str, Any]], sample["rows"]
                        )
                    )
                )
            ),
            "stage1_threshold": test["threshold"],
            "stage1_detector_version": test["detector_version"],
            "max_budget_usd_per_run": DEFAULT_MAX_BUDGET_USD_PER_RUN,
        },
        "evidence_policy": {
            "version": EVIDENCE_POLICY_VERSION,
            "source_lookback_hours": DEFAULT_SOURCE_LOOKBACK_HOURS,
            "recent_context_hours": DEFAULT_RECENT_CONTEXT_HOURS,
            "event_budget_chars": DEFAULT_EVENT_BUDGET_CHARS,
            "max_event_text_chars": DEFAULT_MAX_EVENT_TEXT_CHARS,
            "max_evidence_events": DEFAULT_MAX_EVIDENCE_EVENTS,
            "selection": (
                "Rank candidate-hour breakage reports first, then prior releases, "
                "other candidate-hour evidence, recent issues, human comments, and "
                "automation-suspect comments. Preserve final chronological order."
            ),
            "full_source_window_sent_to_model": False,
            "full_source_window_hashed_for_audit": True,
        },
        "sampling": sample,
        "evaluation": {
            "primary_unit": "repository UTC hour",
            "stage2_positive": "structured real=true",
            "negative_estimator": "inverse-probability weighted within frozen strata",
            "bootstrap": {
                "method": "whole-repository plus within-negative-stratum bootstrap",
                "replicates": 2000,
                "seed": 20260720,
            },
            "clean_sources": ["completed_bug_closure", "merged_fixing_pr"],
            "full_label_result": "descriptive because v4 comment-source audit noise is 30%",
            "clean_source_result": (
                "confirmatory-grade label cut, but still not a fresh holdout because "
                "the published v4 test is reused"
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "V5_PROTOCOL "
        f"requests={sample['total_stage2_requests']} "
        f"positive_census={sample['positive_alert_census']} "
        f"negative_sample={sample['negative_alert_sample']} "
        f"protocol_sha256={canonical_sha256(protocol)}"
    )


def _verify_v4(dataset: Mapping[str, Any], scorecard: Mapping[str, Any]) -> None:
    identities = cast(Mapping[str, Any], scorecard["frozen_identities"])
    hashes = cast(Mapping[str, Any], dataset["hashes"])
    if hashes["dataset_content_sha256"] != identities["test_dataset_content_sha256"]:
        raise RuntimeError("v5 dataset does not match frozen v4 test identity")
    if dataset.get("split") != "test":
        raise RuntimeError("v5 requires the frozen v4 test split")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


if __name__ == "__main__":
    main()
