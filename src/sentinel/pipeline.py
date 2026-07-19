"""Shared mode-aware triage orchestration for CLI, holdout, and web replay."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

from sentinel.feedback import render_feedback_context
from sentinel.store import Firing, SentinelStore, TriageRecord
from sentinel.triage import triage_firing

TRIAGE_LOOKBACK = timedelta(hours=24)


def execute_triage(
    store: SentinelStore,
    firing: Firing,
    *,
    no_agent: bool,
    no_feedback: bool,
    model: str,
    cache_dir: Path,
    fixture_dir: Path,
) -> tuple[TriageRecord, bool]:
    """Run one ablation mode and persist its linked scorecard result."""

    if no_agent:
        record = TriageRecord(
            triage_id=triage_result_id(firing.firing_id, "no_agent", None),
            firing_id=firing.firing_id,
            mode="no_agent",
            real=None,
            cause=None,
            culprit_ref=None,
            confidence=None,
            evidence_quotes=(),
            model=None,
            requested_model=None,
            prompt_hash=None,
            cache_hit=False,
            replayed=False,
            input_max_ts=None,
            response_id=None,
        )
        return record, store.insert_triage_result(record)

    events = store.list_events(
        entity=firing.entity,
        start=firing.ts - TRIAGE_LOOKBACK,
        end=firing.ts,
    )
    feedback_context = None
    if not no_feedback:
        feedback_context = render_feedback_context(
            store.relevant_feedback(firing.entity, metric_id=firing.metric_id)
        )
    run = triage_firing(
        firing,
        events,
        feedback_context=feedback_context,
        no_feedback=no_feedback,
        model=model,
        cache_dir=cache_dir,
        fixture_dir=fixture_dir,
    )
    record = TriageRecord(
        triage_id=triage_result_id(firing.firing_id, run.mode, run.prompt_hash),
        firing_id=firing.firing_id,
        mode=run.mode,
        real=run.output.real,
        cause=run.output.cause,
        culprit_ref=run.output.culprit_ref,
        confidence=run.output.confidence,
        evidence_quotes=run.output.evidence_quotes,
        model=run.model,
        requested_model=run.requested_model,
        prompt_hash=run.prompt_hash,
        cache_hit=run.cache_hit,
        replayed=run.replayed,
        input_max_ts=run.input_max_ts,
        response_id=run.response_id,
    )
    return record, store.insert_triage_result(record)


def triage_result_id(firing_id: str, mode: str, prompt_hash: str | None) -> str:
    identity = json.dumps(
        {"firing_id": firing_id, "mode": mode, "prompt_hash": prompt_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "triage:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
