"""Frozen GH Archive holdout execution and honest scorecard generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from sentinel.adapters.gharchive import GHArchiveAdapter
from sentinel.detect import DetectorConfig, evaluate_detection
from sentinel.pipeline import execute_triage
from sentinel.store import (
    Feedback,
    FeedbackOutcome,
    FeedbackVerdict,
    Firing,
    IngestionWindow,
    SentinelStore,
    TriageRecord,
)
from sentinel.types import Event, format_timestamp, parse_timestamp

HOLDOUT_SCHEMA_VERSION = 1
AUTO_LABEL_RULE_VERSION = "maintainer-substantive-v3"
MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

NEGATIVE_ACK = re.compile(
    r"\b(not a bug|expected behavio[u]?r|works as intended|cannot reproduce|"
    r"can't reproduce|unable to reproduce|invalid|support request|"
    r"not (?:the )?official(?:ly supported)? (?:build|binary|binaries)|"
    r"issue against (?:the )?(?:package )?distributor)\b",
    re.IGNORECASE,
)
POSITIVE_ACK = re.compile(
    r"\b(fix(?:ed|ing)?|bug(?:gy)?|regression|confirm(?:ed)?|reproduc(?:e|ed)|"
    r"root cause|caus(?:e|ed)|broken|revert(?:ed|ing)?|patch(?:ed)?|"
    r"workaround|resolved)\b",
    re.IGNORECASE,
)
ACTION_ACK = re.compile(
    r"(?:#\d+|/pull/\d+|\bpr\b|\bmerge(?:d)?\b|\brelease(?:d)?\b|"
    r"\bcommit(?:ted)?\b|\bship(?:ped)?\b)",
    re.IGNORECASE,
)
STRONG_ACK = re.compile(
    r"\b(root cause|confirmed|reproduced|fixed|resolved|reverted)\b",
    re.IGNORECASE,
)
BREAKAGE_REPORT = re.compile(
    r"\b(regression|crash(?:es|ed|ing)?|error|fail(?:s|ed|ing|ure)?|broken|"
    r"not working|doesn't work|does not work|bug)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AutoVerdict:
    verdict: str
    reason: str
    issue_number: int | None = None
    ack_ts: str | None = None
    ack_url: str | None = None
    ack_excerpt: str | None = None
    lead_minutes: float | None = None


def canonical_sha256(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze_holdout(
    *,
    repo_root: Path,
    feedback_db: Path,
    output_dir: Path,
    start: datetime,
    end: datetime,
    repositories: Sequence[str],
    ack_lookahead_hours: int,
    selection_policy: str,
    detector_config_path: str = "src/sentinel/config/default.toml",
    holdout_id: str = "gharchive-2025-05-12-three-repos-v1",
    database_path: str = ".cache/holdout.duckdb",
    scorecard_path: str = "holdout/scorecard.json",
    markdown_path: str = "HOLDOUT.md",
) -> dict[str, Any]:
    """Freeze code/config/feedback before any holdout archive is inspected."""

    snapshot_path = output_dir / "feedback-snapshot.json"
    manifest_path = output_dir / "manifest.json"
    if snapshot_path.exists() or manifest_path.exists():
        raise FileExistsError(
            "holdout freeze already exists; refusing to replace committed evidence"
        )
    if end <= start:
        raise ValueError("holdout end must follow start")
    if ack_lookahead_hours < 0:
        raise ValueError("ack lookahead cannot be negative")
    if len(set(repositories)) != len(repositories) or not repositories:
        raise ValueError("holdout repositories must be unique and non-empty")

    config_path = repo_root / detector_config_path
    config_bytes = config_path.read_bytes()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    config = DetectorConfig.load(config_path)
    frozen_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
    ).strip()

    with SentinelStore(feedback_db) as store:
        feedback = store.list_feedback()
    try:
        source_database = str(feedback_db.relative_to(repo_root))
    except ValueError:
        source_database = feedback_db.name
    snapshot = {
        "schema_version": HOLDOUT_SCHEMA_VERSION,
        "frozen_at": format_timestamp(datetime.now(UTC)),
        "source_database": source_database,
        "rows": [
            feedback_to_dict(item)
            for item in sorted(
                feedback, key=lambda row: (row.created_at, row.feedback_id)
            )
        ],
    }
    snapshot_hash = canonical_sha256(snapshot)
    manifest = {
        "schema_version": HOLDOUT_SCHEMA_VERSION,
        "holdout_id": holdout_id,
        "frozen_at": snapshot["frozen_at"],
        "frozen_git_commit": frozen_commit,
        "detector_config_path": detector_config_path,
        "detector_config_sha256": config_sha256,
        "detector_version": config.version,
        "feedback_snapshot_path": "holdout/feedback-snapshot.json",
        "feedback_snapshot_sha256": snapshot_hash,
        "selection_policy": selection_policy,
        "repositories": list(repositories),
        "detection_start": format_timestamp(start),
        "detection_end": format_timestamp(end),
        "baseline_start": format_timestamp(
            start - timedelta(days=config.phase_history_days)
            if config.algorithm == "robust_rate_v2"
            else start
        ),
        "ack_lookahead_hours": ack_lookahead_hours,
        "raw_cache_dir": ".cache/gharchive",
        "database_path": database_path,
        "fixture_dir": "tests/fixtures/triage",
        "scorecard_path": scorecard_path,
        "markdown_path": markdown_path,
        "auto_label_rule_version": AUTO_LABEL_RULE_VERSION,
    }
    _write_json(snapshot_path, snapshot)
    _write_json(manifest_path, manifest)
    return manifest


def run_holdout(*, repo_root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest, snapshot = load_frozen_holdout(
        repo_root=repo_root, manifest_path=manifest_path
    )
    start = parse_timestamp(cast(str, manifest["detection_start"]))
    end = parse_timestamp(cast(str, manifest["detection_end"]))
    baseline_start = parse_timestamp(
        cast(str, manifest.get("baseline_start") or manifest["detection_start"])
    )
    ack_end = end + timedelta(hours=int(manifest["ack_lookahead_hours"]))
    repositories = tuple(cast(list[str], manifest["repositories"]))
    database_path = repo_root / cast(str, manifest["database_path"])
    raw_cache_dir = repo_root / cast(str, manifest["raw_cache_dir"])
    fixture_dir = repo_root / cast(str, manifest["fixture_dir"])
    config = DetectorConfig.load(
        repo_root / cast(str, manifest["detector_config_path"])
    )

    with SentinelStore(database_path) as store:
        restored = store.import_feedback(
            feedback_from_dict(row)
            for row in cast(list[dict[str, Any]], snapshot["rows"])
        )
        print(f"SNAPSHOT feedback_rows={len(snapshot['rows'])} restored={restored}")
        ingest_archives(
            store=store,
            repositories=repositories,
            start=baseline_start,
            end=ack_end,
            cache_dir=raw_cache_dir,
        )
        events = store.list_events(start=baseline_start, end=end)
        detection = evaluate_detection(events, config, start=start, end=end)
        firings = list(detection.firings)
        inserted = store.insert_firings(firings)
        print(
            f"DETECT events={len(events)} firings={len(firings)} inserted={inserted}"
        )
        run_all_triage_modes(
            store=store,
            firings=firings,
            cache_dir=repo_root / ".cache/triage",
            fixture_dir=fixture_dir,
            model="gpt-5.6",
        )
        verify_fixture_replay(
            store=store,
            firings=firings,
            fixture_dir=fixture_dir,
            model="gpt-5.6",
        )
        all_events = store.list_events(start=start, end=ack_end)
        scorecard = build_scorecard(
            manifest=manifest,
            store=store,
            firings=firings,
            events=all_events,
            start=start,
            end=end,
            ack_end=ack_end,
        )
        scorecard["detector_diagnostics"] = detection.verdict_counts
        scorecard["execution_git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()

    scorecard_path = repo_root / cast(str, manifest["scorecard_path"])
    markdown_path = repo_root / cast(str, manifest["markdown_path"])
    _write_json(scorecard_path, scorecard)
    _write_text(markdown_path, render_holdout_markdown(scorecard))
    print_raw_metrics(scorecard)
    return scorecard


def load_frozen_holdout(
    *, repo_root: Path, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != HOLDOUT_SCHEMA_VERSION:
        raise ValueError("unsupported holdout manifest schema")
    config_path = repo_root / cast(str, manifest["detector_config_path"])
    actual_config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if actual_config_hash != manifest.get("detector_config_sha256"):
        raise ValueError("detector config changed after holdout freeze")
    config = DetectorConfig.load(config_path)
    if config.version != manifest.get("detector_version"):
        raise ValueError("detector version does not match frozen manifest")
    snapshot_path = repo_root / cast(str, manifest["feedback_snapshot_path"])
    snapshot = _read_json(snapshot_path)
    if canonical_sha256(snapshot) != manifest.get("feedback_snapshot_sha256"):
        raise ValueError("feedback snapshot hash does not match frozen manifest")
    return manifest, snapshot


def ingest_archives(
    *,
    store: SentinelStore,
    repositories: Sequence[str],
    start: datetime,
    end: datetime,
    cache_dir: Path,
) -> None:
    adapter = GHArchiveAdapter(cache_dir=cache_dir)
    hours = tuple(iter_hours(start, end))
    for index, hour in enumerate(hours, start=1):
        existing = {
            window.entity
            for window in store.list_ingestion_windows(
                source="gharchive", start=hour, end=hour + timedelta(hours=1)
            )
            if window.status == "complete"
        }
        if set(repositories) <= existing:
            print(
                f"ARCHIVE {index}/{len(hours)} {format_timestamp(hour)} reused",
                flush=True,
            )
            continue
        try:
            events = list(adapter.iter_events(hour, entities=repositories))
            archive_path = adapter.cache_path(hour)
            source_bytes = archive_path.stat().st_size
            by_repo = {
                repo: [event for event in events if event.entity == repo]
                for repo in repositories
            }
            for repo, repo_events in by_repo.items():
                store.insert_events(repo_events)
                store.record_ingestion_window(
                    IngestionWindow(
                        source="gharchive",
                        entity=repo,
                        window_start=hour,
                        window_end=hour + timedelta(hours=1),
                        status="complete",
                        normalized_events=len(repo_events),
                        source_bytes=source_bytes,
                        meta={
                            "archive": archive_path.name,
                            "url": adapter.archive_url(hour),
                            "holdout": True,
                        },
                    )
                )
        except Exception as exc:
            for repo in repositories:
                store.record_ingestion_window(
                    IngestionWindow(
                        source="gharchive",
                        entity=repo,
                        window_start=hour,
                        window_end=hour + timedelta(hours=1),
                        status="failed",
                        normalized_events=0,
                        meta={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )
            raise
        counts = ",".join(f"{repo}={len(rows)}" for repo, rows in by_repo.items())
        print(
            f"ARCHIVE {index}/{len(hours)} {format_timestamp(hour)} "
            f"bytes={source_bytes} {counts}",
            flush=True,
        )


def run_all_triage_modes(
    *,
    store: SentinelStore,
    firings: Sequence[Firing],
    cache_dir: Path,
    fixture_dir: Path,
    model: str,
) -> None:
    for mode, no_agent, no_feedback in (
        ("full", False, False),
        ("no_feedback", False, True),
        ("no_agent", True, False),
    ):
        for index, firing in enumerate(firings, start=1):
            result, inserted = execute_triage(
                store,
                firing,
                no_agent=no_agent,
                no_feedback=no_feedback,
                model=model,
                cache_dir=cache_dir,
                fixture_dir=fixture_dir,
            )
            print(
                f"TRIAGE mode={mode} {index}/{len(firings)} "
                f"firing={firing.firing_id} real={result.real} "
                f"culprit={result.culprit_ref} cache_hit={result.cache_hit} "
                f"inserted={inserted}",
                flush=True,
            )


def verify_fixture_replay(
    *,
    store: SentinelStore,
    firings: Sequence[Firing],
    fixture_dir: Path,
    model: str,
) -> None:
    previous_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = ""
    try:
        with tempfile.TemporaryDirectory(prefix="sentinel-holdout-replay-") as cache:
            for mode, no_feedback in (("full", False), ("no_feedback", True)):
                for firing in firings:
                    result, _ = execute_triage(
                        store,
                        firing,
                        no_agent=False,
                        no_feedback=no_feedback,
                        model=model,
                        cache_dir=Path(cache),
                        fixture_dir=fixture_dir,
                    )
                    if not result.cache_hit or not result.replayed:
                        raise RuntimeError(
                            f"{mode} fixture did not replay for {firing.firing_id}"
                        )
    finally:
        if previous_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous_key
    print(f"REPLAY verified={len(firings) * 2} offline=true")


def build_scorecard(
    *,
    manifest: Mapping[str, Any],
    store: SentinelStore,
    firings: Sequence[Firing],
    events: Sequence[Event],
    start: datetime,
    end: datetime,
    ack_end: datetime,
) -> dict[str, Any]:
    firing_rows = [
        firing_scorecard_row(store=store, firing=firing, events=events)
        for firing in firings
    ]
    misses = find_detector_misses(events=events, firings=firings, start=start, end=end)
    weeks = (end - start).total_seconds() / (7 * 24 * 3600)
    metrics = {
        mode: mode_metrics(firing_rows, mode=mode, weeks=weeks)
        for mode in ("no_agent", "no_feedback", "full")
    }
    expected_hours = len(tuple(iter_hours(start, end)))
    windows = store.list_ingestion_windows(
        source="gharchive", start=start, end=end
    )
    complete = sum(window.status == "complete" for window in windows)
    expected = expected_hours * len(cast(list[str], manifest["repositories"]))
    feedback_effect = _metric_delta(
        metrics["full"]["precision"], metrics["no_feedback"]["precision"]
    )
    return {
        "schema_version": HOLDOUT_SCHEMA_VERSION,
        "generated_at": format_timestamp(datetime.now(UTC)),
        "holdout": dict(manifest),
        "detection_window": {
            "start": format_timestamp(start),
            "end": format_timestamp(end),
            "ack_lookahead_end": format_timestamp(ack_end),
            "duration_weeks": weeks,
        },
        "coverage": {
            "complete_repo_hours": complete,
            "expected_repo_hours": expected,
            "ratio": complete / expected if expected else None,
            "archive_hours": expected_hours,
        },
        "auto_label": {
            "rule_version": AUTO_LABEL_RULE_VERSION,
            "maintainer_associations": sorted(MAINTAINER_ASSOCIATIONS),
            "description": (
                "First later same-issue human maintainer comment on an issue thread "
                "matching disclosed substantive positive or negative phrases. Candidate "
                "issues must be visible no later than the firing; otherwise UNRESOLVED."
            ),
        },
        "methodology_amendments": [
            {
                "from_rule": "maintainer-substantive-v1",
                "to_rule": AUTO_LABEL_RULE_VERSION,
                "reason": (
                    "The first raw runs exposed label leakage: generated PR statistics "
                    "matched acknowledgment phrases (including CI posted through a human "
                    "maintainer account), issue activity later in the firing hour could "
                    "label an earlier firing, and an explicit downstream-distributor "
                    "closure was read as a fix. V3 grades only issue acknowledgments, "
                    "excludes bots, restricts candidate issue activity to timestamps at "
                    "or before the firing, and recognizes the disclosed downstream-build "
                    "negative phrase. Detector outputs and agent fixtures were unchanged."
                ),
                "observed_v1_metrics": {
                    "no_agent": {"true_positives": 9, "unresolved": 11},
                    "no_feedback": {"misses": 9, "unresolved": 11},
                    "full": {"misses": 9, "unresolved": 11},
                    "detector_misses": 3,
                },
                "observed_v2_metrics": {
                    "no_agent": {"true_positives": 7, "unresolved": 13},
                    "no_feedback": {"misses": 7, "unresolved": 13},
                    "full": {"misses": 7, "unresolved": 13},
                    "detector_misses": 3,
                },
            }
        ],
        "metrics": metrics,
        "feedback_precision_delta": feedback_effect,
        "firings": firing_rows,
        "detector_misses": misses,
    }


def firing_scorecard_row(
    *, store: SentinelStore, firing: Firing, events: Sequence[Event]
) -> dict[str, Any]:
    bucket_events = [
        event
        for event in events
        if event.entity == firing.entity
        and firing.window_start <= event.ts < firing.window_end
        and event.kind in {"issue", "comment"}
    ]
    full = store.latest_triage_result(firing.firing_id, mode="full")
    no_feedback = store.latest_triage_result(firing.firing_id, mode="no_feedback")
    no_agent = store.latest_triage_result(firing.firing_id, mode="no_agent")
    if full is None or no_feedback is None or no_agent is None:
        raise RuntimeError(f"missing triage ablation for {firing.firing_id}")
    verdict = auto_label_firing(
        firing=firing,
        events=events,
        issue_numbers=visible_issue_numbers(firing, bucket_events),
    )
    source_event = min(
        (
            event
            for event in bucket_events
            if event.ts >= firing.ts and event.meta.get("url")
        ),
        key=lambda event: event.ts,
        default=None,
    )
    return {
        "firing_id": firing.firing_id,
        "ts": format_timestamp(firing.ts),
        "entity": firing.entity,
        "metric_id": firing.metric_id,
        "observed_at_fire": firing.observed,
        "bucket_total": firing.meta.get("bucket_total"),
        "baseline_mean": firing.baseline_mean,
        "baseline_stddev": firing.baseline_stddev,
        "z_score": firing.score,
        "detector_version": firing.detector_version,
        "source_url": source_event.meta.get("url") if source_event else None,
        "issue_numbers": sorted(issue_numbers(bucket_events)),
        "routing": routing_for_firing(firing),
        "verdict": asdict(verdict),
        "modes": {
            "full": triage_to_dict(full),
            "no_feedback": triage_to_dict(no_feedback),
            "no_agent": triage_to_dict(no_agent),
        },
    }


def auto_label_firing(
    *, firing: Firing, events: Sequence[Event], issue_numbers: set[int]
) -> AutoVerdict:
    candidates = [
        event
        for event in events
        if event.entity == firing.entity
        and event.kind == "comment"
        and event.ts > firing.ts
        and _int_or_none(event.meta.get("issue_number")) in issue_numbers
        and str(event.meta.get("author_association") or "").upper()
        in MAINTAINER_ASSOCIATIONS
        and not _is_bot_event(event)
        and "/pull/" not in str(event.meta.get("url") or "")
    ]
    for event in sorted(candidates, key=lambda item: item.ts):
        verdict = classify_acknowledgment(event.text)
        if verdict is None:
            continue
        return AutoVerdict(
            verdict=verdict,
            reason="deterministic substantive maintainer acknowledgment",
            issue_number=_int_or_none(event.meta.get("issue_number")),
            ack_ts=format_timestamp(event.ts),
            ack_url=_str_or_none(event.meta.get("url")),
            ack_excerpt=_excerpt(event.text),
            lead_minutes=round((event.ts - firing.ts).total_seconds() / 60, 2),
        )
    return AutoVerdict(
        verdict="UNRESOLVED",
        reason="no later same-issue substantive maintainer acknowledgment in coverage",
    )


def visible_issue_numbers(firing: Firing, events: Iterable[Event]) -> set[int]:
    return issue_numbers(event for event in events if event.ts <= firing.ts)


def _is_bot_event(event: Event) -> bool:
    identity = str(event.meta.get("author") or event.meta.get("actor") or "").lower()
    return identity.endswith("[bot]") or identity.endswith("-bot")


def classify_acknowledgment(text: str | None) -> str | None:
    normalized = " ".join((text or "").split())
    if not normalized:
        return None
    if NEGATIVE_ACK.search(normalized):
        return "FALSE"
    if POSITIVE_ACK.search(normalized) and (
        ACTION_ACK.search(normalized) or STRONG_ACK.search(normalized)
    ):
        return "TRUE"
    return None


def find_detector_misses(
    *,
    events: Sequence[Event],
    firings: Sequence[Firing],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    covered = {
        (firing.entity, issue_number)
        for firing in firings
        for issue_number in issue_numbers(
            event
            for event in events
            if event.entity == firing.entity
            and firing.window_start <= event.ts < firing.window_end
        )
    }
    comments: dict[tuple[str, int], list[Event]] = {}
    for event in events:
        number = _int_or_none(event.meta.get("issue_number"))
        if event.kind == "comment" and number is not None:
            comments.setdefault((event.entity, number), []).append(event)

    misses: list[dict[str, Any]] = []
    for event in events:
        number = _int_or_none(event.meta.get("issue_number"))
        if (
            event.kind != "issue"
            or event.meta.get("action") != "opened"
            or number is None
            or not (start <= event.ts < end)
            or not BREAKAGE_REPORT.search(event.text or "")
            or (event.entity, number) in covered
        ):
            continue
        candidates = [
            comment
            for comment in comments.get((event.entity, number), [])
            if event.ts < comment.ts <= event.ts + timedelta(hours=72)
            and str(comment.meta.get("author_association") or "").upper()
            in MAINTAINER_ASSOCIATIONS
            and classify_acknowledgment(comment.text) == "TRUE"
        ]
        if not candidates:
            continue
        ack = min(candidates, key=lambda item: item.ts)
        misses.append(
            {
                "entity": event.entity,
                "issue_number": number,
                "reported_at": format_timestamp(event.ts),
                "report_url": event.meta.get("url"),
                "report_excerpt": _excerpt(event.text),
                "ack_ts": format_timestamp(ack.ts),
                "ack_url": ack.meta.get("url"),
                "ack_excerpt": _excerpt(ack.text),
                "reason": "acknowledged breakage report had no covering detector firing",
            }
        )
    return sorted(misses, key=lambda row: (row["reported_at"], row["entity"]))


def mode_metrics(
    firing_rows: Sequence[Mapping[str, Any]], *, mode: str, weeks: float
) -> dict[str, Any]:
    counts = {
        "true_positives": 0,
        "false_positives": 0,
        "true_negatives": 0,
        "misses": 0,
        "unresolved": 0,
    }
    lead_minutes: list[float] = []
    for row in firing_rows:
        verdict = cast(Mapping[str, Any], row["verdict"])
        truth = verdict["verdict"]
        mode_result = cast(Mapping[str, Any], cast(Mapping[str, Any], row["modes"])[mode])
        predicted_positive = True if mode == "no_agent" else bool(mode_result["real"])
        if truth == "UNRESOLVED":
            counts["unresolved"] += 1
        elif truth == "TRUE" and predicted_positive:
            counts["true_positives"] += 1
            if verdict.get("lead_minutes") is not None:
                lead_minutes.append(float(verdict["lead_minutes"]))
        elif truth == "TRUE":
            counts["misses"] += 1
        elif truth == "FALSE" and predicted_positive:
            counts["false_positives"] += 1
        else:
            counts["true_negatives"] += 1
    resolved_alerts = counts["true_positives"] + counts["false_positives"]
    precision = (
        counts["true_positives"] / resolved_alerts if resolved_alerts else None
    )
    return {
        **counts,
        "firings": len(firing_rows),
        "resolved_alerts": resolved_alerts,
        "precision": precision,
        "median_lead_minutes": (
            statistics.median(lead_minutes) if lead_minutes else None
        ),
        "false_positives_per_week": (
            counts["false_positives"] / weeks if weeks else None
        ),
    }


def routing_for_firing(firing: Firing) -> dict[str, str]:
    detector_severity = firing.meta.get("severity")
    if isinstance(detector_severity, dict):
        level = str(detector_severity.get("severity") or "watch")
        audiences = {
            "interrupt": "repository maintainers + ecosystem owners",
            "digest": "periodic anomaly digest",
            "watch": "queryable detector record",
        }
        attention = float(detector_severity.get("attention_score") or 0.0)
        return {
            "severity": level,
            "audience": audiences.get(level, "queryable detector record"),
            "rationale": (
                "v2 breadth × depth × velocity × confidence attention score "
                f"{attention:.3f}"
            ),
        }
    total = int(firing.meta.get("bucket_total") or firing.observed)
    if total >= 20:
        return {
            "severity": "critical",
            "audience": "maintainers + ecosystem owners",
            "rationale": "20+ issue-activity events in the firing hour",
        }
    if total >= 10:
        return {
            "severity": "high",
            "audience": "repository maintainers",
            "rationale": "10+ issue-activity events in the firing hour",
        }
    return {
        "severity": "watch",
        "audience": "triage queue",
        "rationale": "threshold crossed below the high-volume routing tier",
    }


def triage_to_dict(result: TriageRecord) -> dict[str, Any]:
    return {
        "mode": result.mode,
        "real": result.real,
        "cause": result.cause,
        "culprit_ref": result.culprit_ref,
        "confidence": result.confidence,
        "evidence_quotes": list(result.evidence_quotes),
        "model": result.model,
        "requested_model": result.requested_model,
        "prompt_hash": result.prompt_hash,
        "cache_hit": result.cache_hit,
        "replayed": result.replayed,
        "input_max_ts": (
            format_timestamp(result.input_max_ts) if result.input_max_ts else None
        ),
    }


def render_holdout_markdown(scorecard: Mapping[str, Any]) -> str:
    holdout = cast(Mapping[str, Any], scorecard["holdout"])
    coverage = cast(Mapping[str, Any], scorecard["coverage"])
    metrics = cast(Mapping[str, Mapping[str, Any]], scorecard["metrics"])
    lines = [
        "# Sentinel frozen holdout",
        "",
        "> Generated directly by `scripts/run_holdout.py`; results are unedited.",
        "",
        "## Frozen inputs",
        "",
        f"- Window: `{holdout['detection_start']}` to `{holdout['detection_end']}`",
        f"- Repositories: {', '.join(f'`{repo}`' for repo in holdout['repositories'])}",
        f"- Frozen commit: `{holdout['frozen_git_commit']}`",
        f"- Execution commit: `{scorecard['execution_git_commit']}`",
        f"- Detector config: `{holdout['detector_config_sha256']}`",
        f"- Detector version: `{holdout['detector_version']}`",
        f"- Feedback snapshot: `{holdout['feedback_snapshot_sha256']}`",
        f"- Selection policy: {holdout['selection_policy']}",
        "",
        "## Raw metrics",
        "",
        "| Mode | Firings | TP | FP | Misses | Unresolved | Precision | Median lead | FP/week |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in ("no_agent", "no_feedback", "full"):
        item = metrics[mode]
        lines.append(
            f"| `{mode}` | {item['firings']} | {item['true_positives']} | "
            f"{item['false_positives']} | {item['misses']} | {item['unresolved']} | "
            f"{_percent(item['precision'])} | {_minutes(item['median_lead_minutes'])} | "
            f"{_number(item['false_positives_per_week'])} |"
        )
    lines.extend(
        [
            "",
            f"Feedback precision delta (full − no-feedback): "
            f"`{_percentage_points(scorecard['feedback_precision_delta'])}`.",
            "",
            f"Archive coverage: `{coverage['complete_repo_hours']}/"
            f"{coverage['expected_repo_hours']}` repo-hours "
            f"({_percent(coverage['ratio'])}).",
            "",
            "Verdicts use the committed deterministic maintainer-acknowledgment rule. "
            "No later acknowledgment means `UNRESOLVED`, not a guessed false positive.",
            "",
            "### Methodology amendment",
            "",
            cast(list[Mapping[str, Any]], scorecard["methodology_amendments"])[0][
                "reason"
            ],
            "",
            "## Every firing",
            "",
            "| Fired at | Entity | Full hypothesis | Culprit | Confidence | Verdict | Ack |",
            "| --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in cast(list[Mapping[str, Any]], scorecard["firings"]):
        full = cast(Mapping[str, Any], cast(Mapping[str, Any], row["modes"])["full"])
        verdict = cast(Mapping[str, Any], row["verdict"])
        lines.append(
            f"| `{row['ts']}` | `{row['entity']}` | "
            f"{_markdown_cell(full.get('cause'))} | `{full.get('culprit_ref') or 'none'}` | "
            f"{_number(full.get('confidence'))} | **{verdict['verdict']}** | "
            f"{_markdown_cell(verdict.get('ack_url') or 'none')} |"
        )
    misses = cast(list[Mapping[str, Any]], scorecard["detector_misses"])
    lines.extend(["", "## Published detector misses", ""])
    if not misses:
        lines.append("No misses met the disclosed automatic-acknowledgment rule.")
    else:
        lines.extend(
            [
                "| Reported at | Entity | Issue | Maintainer acknowledgment |",
                "| --- | --- | --- | --- |",
            ]
        )
        for miss in misses:
            lines.append(
                f"| `{miss['reported_at']}` | `{miss['entity']}` | "
                f"{_markdown_cell(miss.get('report_url'))} | "
                f"{_markdown_cell(miss.get('ack_url'))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a frozen holdout, not a tuned benchmark. `UNRESOLVED` rows remain "
            "visible. Feedback is judged only by the measured full versus no-feedback "
            "difference above; no improvement is claimed when the delta is zero or unknown.",
            "",
        ]
    )
    return "\n".join(lines)


def print_raw_metrics(scorecard: Mapping[str, Any]) -> None:
    print("RAW HOLDOUT METRICS")
    for mode, metrics in cast(
        Mapping[str, Mapping[str, Any]], scorecard["metrics"]
    ).items():
        print(
            f"  {mode}: firings={metrics['firings']} "
            f"tp={metrics['true_positives']} fp={metrics['false_positives']} "
            f"misses={metrics['misses']} unresolved={metrics['unresolved']} "
            f"precision={metrics['precision']} "
            f"median_lead_minutes={metrics['median_lead_minutes']} "
            f"false_positives_per_week={metrics['false_positives_per_week']}"
        )
    print(f"  detector_misses={len(scorecard['detector_misses'])}")
    print(f"  feedback_precision_delta={scorecard['feedback_precision_delta']}")


def feedback_to_dict(item: Feedback) -> dict[str, Any]:
    return {
        "feedback_id": item.feedback_id,
        "firing_id": item.firing_id,
        "ts": format_timestamp(item.ts),
        "entity": item.entity,
        "metric_id": item.metric_id,
        "outcome": item.outcome,
        "verdict": item.verdict,
        "hypothesis": item.hypothesis,
        "confidence": item.confidence,
        "corrected_cause": item.corrected_cause,
        "reason_code": item.reason_code,
        "note": item.note,
        "source": item.source,
        "submitted_by": item.submitted_by,
        "detector_version": item.detector_version,
        "sentinel_run_id": item.sentinel_run_id,
        "created_at": format_timestamp(item.created_at),
        "supersedes_feedback_id": item.supersedes_feedback_id,
    }


def feedback_from_dict(raw: Mapping[str, Any]) -> Feedback:
    return Feedback(
        feedback_id=str(raw["feedback_id"]),
        firing_id=_str_or_none(raw.get("firing_id")),
        ts=parse_timestamp(cast(str, raw["ts"])),
        entity=str(raw["entity"]),
        metric_id=str(raw["metric_id"]),
        outcome=cast(FeedbackOutcome, raw["outcome"]),
        verdict=cast(FeedbackVerdict | None, raw.get("verdict")),
        hypothesis=_str_or_none(raw.get("hypothesis")),
        confidence=(
            float(raw["confidence"]) if raw.get("confidence") is not None else None
        ),
        corrected_cause=_str_or_none(raw.get("corrected_cause")),
        reason_code=_str_or_none(raw.get("reason_code")),
        note=_str_or_none(raw.get("note")),
        source=str(raw["source"]),
        submitted_by=_str_or_none(raw.get("submitted_by")),
        detector_version=_str_or_none(raw.get("detector_version")),
        sentinel_run_id=_str_or_none(raw.get("sentinel_run_id")),
        created_at=parse_timestamp(cast(str, raw["created_at"])),
        supersedes_feedback_id=_str_or_none(raw.get("supersedes_feedback_id")),
    )


def issue_numbers(events: Iterable[Event]) -> set[int]:
    return {
        number
        for event in events
        if (number := _int_or_none(event.meta.get("issue_number"))) is not None
    }


def iter_hours(start: datetime, end: datetime) -> Iterable[datetime]:
    current = parse_timestamp(start).replace(minute=0, second=0, microsecond=0)
    while current < end:
        yield current
        current += timedelta(hours=1)


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return raw


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    _write_text(
        path,
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _int_or_none(value: object) -> int | None:
    try:
        return int(cast(Any, value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def _excerpt(value: str | None, limit: int = 240) -> str | None:
    normalized = " ".join((value or "").split())
    if not normalized:
        return None
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _metric_delta(left: object, right: object) -> float | None:
    if left is None or right is None:
        return None
    return float(cast(float, left)) - float(cast(float, right))


def _percent(value: object) -> str:
    return "N/A" if value is None else f"{float(cast(float, value)) * 100:.1f}%"


def _percentage_points(value: object) -> str:
    return "N/A" if value is None else f"{float(cast(float, value)) * 100:+.1f} pp"


def _minutes(value: object) -> str:
    return "N/A" if value is None else f"{float(cast(float, value)):.1f} min"


def _number(value: object) -> str:
    return "N/A" if value is None else f"{float(cast(float, value)):.2f}"


def _markdown_cell(value: object) -> str:
    return str(value or "none").replace("|", "\\|").replace("\n", " ")
