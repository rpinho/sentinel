#!/usr/bin/env python3
"""Freeze the signal-scoped v7 protocol before any judge scoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import read_gzip_json
from sentinel.stage2_v7 import (
    DEFAULT_CLI_VERSION,
    DEFAULT_HEAVY_LOOKBACK_HOURS,
    DEFAULT_INVESTIGATION_BATCH_SIZE,
    DEFAULT_INVESTIGATION_EFFORT,
    DEFAULT_MAX_BUDGET_USD_PER_INVESTIGATION_BATCH,
    DEFAULT_MAX_BUDGET_USD_PER_SCREEN_BATCH,
    DEFAULT_MAX_CANDIDATES_PER_HOUR,
    DEFAULT_MAX_HEAVY_EVENTS,
    DEFAULT_MAX_HEAVY_TEXT_CHARS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_RATE_COOLDOWN_HOURS,
    DEFAULT_RELEASE_LOOKBACK_HOURS,
    DEFAULT_SCREEN_BATCH_SIZE,
    DEFAULT_SCREEN_EFFORT,
    DEFAULT_THREAD_COOLDOWN_HOURS,
    EVIDENCE_POLICY_VERSION,
    INVESTIGATION_SYSTEM_PROMPT,
    INVESTIGATION_TASK,
    PROTOCOL_SCHEMA_VERSION,
    SCREEN_SYSTEM_PROMPT,
    SCREEN_TASK,
    TRANSPORT_VERSION,
    investigation_schema,
    screen_schema,
)

DEFAULT_DATASET = Path("holdout/v4/dataset-test.json.gz")
DEFAULT_EVENTS = Path("holdout/v4/events-test.json.gz")
DEFAULT_V4_SCORECARD = Path("holdout/v4/scorecard.json")
DEFAULT_V5_SCORECARD = Path("holdout/v5/scorecard.json")
DEFAULT_V6_SCORECARD = Path("holdout/v6/scorecard.json")
DEFAULT_FEEDBACK = Path("holdout/feedback-snapshot.json")
DEFAULT_OUTPUT = Path("holdout/v7/protocol.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--v4-scorecard", type=Path, default=DEFAULT_V4_SCORECARD)
    parser.add_argument("--v5-scorecard", type=Path, default=DEFAULT_V5_SCORECARD)
    parser.add_argument("--v6-scorecard", type=Path, default=DEFAULT_V6_SCORECARD)
    parser.add_argument("--feedback", type=Path, default=DEFAULT_FEEDBACK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = read_gzip_json(args.dataset)
    events = read_gzip_json(args.events)
    v4 = _read(args.v4_scorecard)
    v5 = _read(args.v5_scorecard)
    v6 = _read(args.v6_scorecard)
    feedback = _read(args.feedback)
    test = cast(Mapping[str, Any], v4["test"])
    metrics = cast(Mapping[str, Any], test["metrics"])
    if dataset.get("split") != "test" or events.get("split") != "test":
        raise RuntimeError("v7 requires the frozen v4 test split")
    if cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"] != cast(Mapping[str, Any], v4["frozen_identities"])["test_dataset_content_sha256"]:
        raise RuntimeError("v7 dataset differs from the published v4 identity")

    protocol = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "study_id": "sentinel-signal-scoped-stage2-v7",
        "version": "v7",
        "objective": "Test signal-scoped claims with light census screening, selective heavy evidence, and story-level dedup on the unchanged v4 test.",
        "integrity": {
            "v4_stage1_unchanged": True,
            "v4_test_split_reused_exactly": True,
            "test_labels_never_enter_agent_requests": True,
            "all_evidence_strictly_pre_candidate": True,
            "prompts_config_and_schemas_frozen_before_scoring": True,
            "zero_openai_calls": True,
            "fresh_holdout_claim": False,
            "limitation": "V7 is a frozen architectural iteration on the already published v4 test, not a new untouched holdout.",
        },
        "frozen_identities": {
            "v4_test_dataset_content_sha256": cast(Mapping[str, Any], dataset["hashes"])["dataset_content_sha256"],
            "v4_test_events_sha256": cast(Mapping[str, Any], events["hashes"])["events_sha256"],
            "v4_scorecard_sha256": v4["scorecard_sha256"],
            "v5_scorecard_sha256": v5["scorecard_sha256"],
            "v6_scorecard_sha256": v6["scorecard_sha256"],
            "feedback_snapshot_sha256": canonical_sha256(feedback),
        },
        "stage1": {
            "candidate_hours": cast(Mapping[str, Any], v4["power"])["test_rows"],
            "alerts": metrics["alerts"],
            "true_positive_hours": metrics["true_positive_hours"],
            "false_positive_hours": metrics["false_positive_hours"],
            "missed_positive_hours": metrics["missed_positive_hours"],
            "precision": metrics["precision"],
            "hour_recall": metrics["recall"],
            "incident_recall": test["incident_recall"],
            "threshold": test["threshold"],
            "detector_version": test["detector_version"],
        },
        "candidate_policy": {
            "unit": "one series x segment x window falsifiable claim",
            "lanes": ["issue_thread_activity", "repository_event_type_rate", "release_adjacency_issue_rate"],
            "event_type_absolute_floors": {"issue": 2, "comment": 4, "pull_request": 4},
            "minimum_rate_robust_z": 3.0,
            "release_lookback_hours": DEFAULT_RELEASE_LOOKBACK_HOURS,
            "max_candidates_per_stage1_hour": DEFAULT_MAX_CANDIDATES_PER_HOUR,
            "thread_cooldown_hours": DEFAULT_THREAD_COOLDOWN_HOURS,
            "rate_cooldown_hours": DEFAULT_RATE_COOLDOWN_HOURS,
            "automation_rule": "Automation and automated mirrors receive deterministic dismissals before the judge.",
            "accounting_rule": "Every generated claim receives a deterministic or agent disposition; no silent drops.",
            "cap_rule": "Class-ranked deterministic tie-break; issue-thread claims precede release adjacency and repository-rate claims.",
        },
        "stage2": {
            "provider": DEFAULT_PROVIDER,
            "model": DEFAULT_MODEL,
            "transport": TRANSPORT_VERSION,
            "claude_cli_version": DEFAULT_CLI_VERSION,
            "authentication": "Claude Code claude.ai subscription with Anthropic and OpenAI API credentials removed from every subprocess.",
            "screen_effort": DEFAULT_SCREEN_EFFORT,
            "investigation_effort": DEFAULT_INVESTIGATION_EFFORT,
            "max_budget_usd_per_screen_batch": DEFAULT_MAX_BUDGET_USD_PER_SCREEN_BATCH,
            "max_budget_usd_per_investigation_batch": DEFAULT_MAX_BUDGET_USD_PER_INVESTIGATION_BATCH,
            "screen_system_prompt": SCREEN_SYSTEM_PROMPT,
            "screen_task": SCREEN_TASK,
            "investigation_system_prompt": INVESTIGATION_SYSTEM_PROMPT,
            "investigation_task": INVESTIGATION_TASK,
            "screen_schema_sha256_full_batch": canonical_sha256(screen_schema(DEFAULT_SCREEN_BATCH_SIZE)),
            "investigation_schema_sha256_full_batch": canonical_sha256(investigation_schema(DEFAULT_INVESTIGATION_BATCH_SIZE)),
            "election_rule": "Fetch heavy evidence exactly when the light screen returns disposition=investigate.",
            "publication_rule": "Publish exactly final disposition=finding after deterministic grounding and attribution checks.",
        },
        "evidence_policy": {
            "version": EVIDENCE_POLICY_VERSION,
            "screen_payload": "Identity, claim, compact metric/baseline/impact fields, one bounded excerpt, and matching feedback lessons.",
            "heavy_lookback_hours": DEFAULT_HEAVY_LOOKBACK_HOURS,
            "max_heavy_events": DEFAULT_MAX_HEAVY_EVENTS,
            "max_heavy_text_chars": DEFAULT_MAX_HEAVY_TEXT_CHARS,
            "raw_event_dumps_sent": False,
            "self_documenting": True,
            "attribution_drill": "Causal attribution requires signature concordance and independent corroboration; timing alone is correlation.",
            "feedback_treatment": "Active matching feedback is untrusted historical hypothesis to verify, never executable instruction.",
        },
        "batching": {
            "mode": "complete scheduled candidate set partitioned only for historical replay scale",
            "screen_candidates_per_batch": DEFAULT_SCREEN_BATCH_SIZE,
            "investigation_candidates_per_batch": DEFAULT_INVESTIGATION_BATCH_SIZE,
            "production_interpretation": "Each 15-60 minute live cycle screens its complete compact candidate set in one invocation when it fits the frozen batch cap.",
        },
        "verdicts": {
            "vocabulary": ["finding", "benign_dismiss", "known_carry", "watch", "insufficient_data"],
            "severity_basis": ["breadth", "absolute_magnitude", "velocity", "confidence"],
            "finding_grounding": "A finding without an exact excerpt quote is downgraded to insufficient_data.",
            "story_rule": "Group sibling findings by repository and exact issue number where available; never group by repository-hour alone.",
        },
        "evaluation": {
            "primary_unit": "published story",
            "matching": "Exact repository plus issue-number match from story to frozen incident; hour mapping is reported separately.",
            "clean_sources": ["completed_bug_closure", "merged_fixing_pr"],
            "full_label_result": "Descriptive because the published v4 comment-source audit found 30% obvious noise.",
            "comparisons": ["v5", "v6", "v7"],
            "bootstrap": {"method": "whole-repository bootstrap", "replicates": 2000, "seed": 20260720},
        },
        "feedback_loop": {
            "snapshot_hash": canonical_sha256(feedback),
            "fast_loop": "Read active per-entity feedback before screening each run.",
            "durable_loop": "Fold validated lessons into versioned prompts/recurrence rules; deterministic lessons move into candidate gates.",
        },
        "cost_gate": {"checkpoint_equivalent_usd": 60.0, "action": "Stop before uncached work beyond the checkpoint and report."},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(protocol, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"V7_PROTOCOL protocol_sha256={canonical_sha256(protocol)} stage1_alerts={metrics['alerts']}")


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
