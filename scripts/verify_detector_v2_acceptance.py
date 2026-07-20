#!/usr/bin/env python3
"""Assert detector-v2's raw webpack reproduction and healthy contrast."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sentinel.corroborate import corroborate_release
from sentinel.detect import DetectorConfig, evaluate_detection
from sentinel.feedback import render_feedback_context
from sentinel.store import SentinelStore
from sentinel.triage import TriageOutput, TriageRequest, build_triage_request
from sentinel.types import format_timestamp

HISTORY_START = datetime(2025, 3, 21, tzinfo=UTC)
HEALTHY_START = datetime(2025, 4, 4, 12, tzinfo=UTC)
HERO_START = datetime(2025, 4, 7, 12, tzinfo=UTC)
END = datetime(2025, 4, 7, 23, tzinfo=UTC)
ACK_TS = datetime(2025, 4, 7, 20, 14, 4, tzinfo=UTC)
FIRING_TS = datetime(2025, 4, 7, 18, 55, 2, tzinfo=UTC)
EXPECTED_TRIAGE_HASHES = {
    "full": "8a8e84a669ab09ef0f5b271a481a33cb9cbdda8bec5e9a7295613b534d6caf25",
    "no_feedback": "96686e532f18a193de07145eae97cc25cb4b78dc485fd64940c9663a3146e4fd",
}


def verify(database: Path, config_path: Path, fixture_dir: Path) -> None:
    config = DetectorConfig.load(config_path)
    assert config.algorithm == "robust_rate_v2"
    with SentinelStore(database) as store:
        all_events = store.list_events(end=datetime(2025, 4, 9, tzinfo=UTC))
        repo_events = store.list_events(
            entity="webpack/webpack", start=HISTORY_START, end=END
        )
        coverage = store.list_ingestion_windows(
            entity="webpack/webpack",
            source="gharchive",
            start=HISTORY_START,
            end=END,
        )
        feedback_context = render_feedback_context(
            store.relevant_feedback(
                "webpack/webpack",
                metric_id="comments_per_active_issue_thread",
            )
        )

    expected_hours = {
        HISTORY_START + timedelta(hours=offset)
        for offset in range(int((END - HISTORY_START).total_seconds() // 3600))
    }
    complete_hours = {
        window.window_start for window in coverage if window.status == "complete"
    }
    assert complete_hours == expected_hours

    healthy = evaluate_detection(
        repo_events,
        config,
        start=HEALTHY_START,
        end=HERO_START,
    )
    assert healthy.firings == ()

    hero = evaluate_detection(repo_events, config, start=HERO_START, end=END)
    assert len(hero.firings) == 1
    firing = hero.firings[0]
    assert firing.ts == FIRING_TS
    assert firing.ts < ACK_TS
    assert firing.metric_id == "comments_per_active_issue_thread"
    assert firing.observed == 5.5
    assert firing.baseline_mean == 1.0
    assert firing.score == 18.0
    assert firing.meta["candidate_metrics"] == [
        "issues_opened_per_active_participant",
        "comments_per_active_issue_thread",
    ]
    assert firing.meta["metric_evaluations"][
        "comments_per_active_issue_thread"
    ]["bot_excluded"]["baseline_samples"] == 5
    release = firing.meta["independent_corroboration"]["recent_release"]
    assert release["version"] == "5.99.0"
    assert release["sources"] == ["gharchive", "npm_registry"]
    assert firing.meta["severity"]["severity"] == "digest"
    assert firing.meta["severity"]["interrupt_policy_enabled"] is False

    automated = {
        actor
        for evaluation in hero.evaluations
        for actor in evaluation.meta["automated_actors"]
    }
    assert automated == {"dependabot[bot]"}

    issue = next(
        event
        for event in repo_events
        if event.kind == "issue"
        and event.meta.get("issue_number") == 19394
        and event.meta.get("action") == "opened"
    )
    assert issue.ts == datetime(2025, 4, 7, 18, 4, 49, tzinfo=UTC)
    assert issue.ts < firing.ts

    culprit = corroborate_release(
        all_events, repo="webpack/webpack", package="webpack", version="5.99.0"
    )
    assert culprit is not None
    assert culprit.github.ts == datetime(2025, 4, 7, 13, 12, 5, tzinfo=UTC)
    assert culprit.npm.ts.replace(microsecond=0) == datetime(
        2025, 4, 7, 13, 13, 8, tzinfo=UTC
    )
    assert culprit.difference_seconds <= 90

    pre_firing_events = [
        event
        for event in repo_events
        if FIRING_TS - timedelta(hours=24) <= event.ts < FIRING_TS
    ]
    triage_requests = {
        "full": build_triage_request(
            firing,
            pre_firing_events,
            feedback_context=feedback_context,
        ),
        "no_feedback": build_triage_request(
            firing,
            pre_firing_events,
            no_feedback=True,
        ),
    }
    triage_outputs = {
        mode: _verify_triage_fixture(
            fixture_dir / f"{request.prompt_hash}.json",
            request,
        )
        for mode, request in triage_requests.items()
    }
    assert {
        mode: request.prompt_hash for mode, request in triage_requests.items()
    } == EXPECTED_TRIAGE_HASHES
    assert all(output.real for output in triage_outputs.values())
    assert all(
        output.culprit_ref == "v5.99.0" for output in triage_outputs.values()
    )
    assert all(output.confidence == 0.99 for output in triage_outputs.values())

    print(f"PASS continuous archive hours: {len(complete_hours)} complete")
    print(f"PASS healthy contrast: firings=0 diagnostics={healthy.verdict_counts}")
    print(
        f"PASS detector v2 firing: {format_timestamp(firing.ts)} "
        f"metric={firing.metric_id} value={firing.observed} "
        f"median={firing.baseline_mean} robust_z={firing.score}"
    )
    print(
        "PASS cross-source release: "
        f"GH={format_timestamp(culprit.github.ts)} "
        f"npm={format_timestamp(culprit.npm.ts)} "
        f"agreement={culprit.difference_seconds:.0f}s"
    )
    print(f"PASS automation exclusions: {sorted(automated)}")
    print(
        "PASS offline GPT-5.6 attribution: "
        + ", ".join(
            f"{mode}={output.culprit_ref}@{output.confidence:.2f}"
            for mode, output in triage_outputs.items()
        )
    )
    print(
        f"PASS pre-ack lead: {(ACK_TS - firing.ts).total_seconds() / 60:.1f} minutes"
    )


def _verify_triage_fixture(path: Path, request: TriageRequest) -> TriageOutput:
    serialized = path.read_text(encoding="utf-8")
    for forbidden in (
        "2025-04-07T20:14:04Z",
        "2784517989",
        "Found buggy places",
        "PR #19398",
    ):
        assert forbidden not in serialized
    record: dict[str, Any] = json.loads(serialized)
    canonical = json.dumps(
        record["request"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == request.prompt_hash
    assert record["request_hash"] == request.prompt_hash
    assert record["request"] == dict(request.api_payload)
    assert record["requested_model"] == "gpt-5.6"
    assert record["returned_model"] == "gpt-5.6-sol"
    assert "temperature" not in record["request"]
    assert "seed" not in record["request"]

    payload = json.loads(record["request"]["input"][1]["content"])
    assert payload["firing"]["firing_id"] == "firing:07cb50298e4a5badf1114403"
    assert payload["input_window"]["max_event_ts"] == "2025-04-07T18:52:11Z"
    assert all(
        event["ts"] < "2025-04-07T18:55:02Z"
        for event in payload["pre_firing_events"]
    )

    output = TriageOutput.from_mapping(record["response"])
    source_texts = [event.get("text") or "" for event in payload["pre_firing_events"]]
    assert output.evidence_quotes
    assert all(
        any(quote in source for source in source_texts)
        for quote in output.evidence_quotes
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("src/sentinel/config/detector-v2.toml"),
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=Path("tests/fixtures/triage"),
    )
    args = parser.parse_args()
    verify(args.db, args.config, args.fixture_dir)


if __name__ == "__main__":
    main()
