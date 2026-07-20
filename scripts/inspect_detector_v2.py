#!/usr/bin/env python3
"""Emit reproducible detector-v2 evaluations from an already-ingested database."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sentinel.detect import DetectorConfig, evaluate_detection
from sentinel.store import Firing, SentinelStore
from sentinel.types import format_timestamp, parse_timestamp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--entity", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = DetectorConfig.load(args.config)
    if config.algorithm != "robust_rate_v2":
        parser.error("--config must select robust_rate_v2")
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    history_start = start - timedelta(days=config.phase_history_days)
    with SentinelStore(args.db) as store:
        events = store.list_events(
            entity=args.entity,
            start=history_start,
            end=end,
        )
    run = evaluate_detection(events, config, start=start, end=end)
    document: dict[str, Any] = {
        "entity": args.entity,
        "config": str(args.config),
        "detector_version": config.version,
        "history_start": format_timestamp(history_start),
        "detection_start": format_timestamp(start),
        "detection_end": format_timestamp(end),
        "event_count": len(events),
        "verdict_counts": run.verdict_counts,
        "firings": [_firing_to_dict(firing) for firing in run.firings],
        "evaluations": [
            {
                "entity": evaluation.entity,
                "bucket_start": format_timestamp(evaluation.bucket_start),
                "metric_id": evaluation.metric_id,
                "verdict": evaluation.verdict,
                "raw": dict(evaluation.raw),
                "bot_excluded": dict(evaluation.bot_excluded),
                "meta": dict(evaluation.meta),
            }
            for evaluation in run.evaluations
        ],
    }
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


def _firing_to_dict(firing: Firing) -> dict[str, Any]:
    return {
        "firing_id": firing.firing_id,
        "ts": format_timestamp(firing.ts),
        "entity": firing.entity,
        "metric_id": firing.metric_id,
        "window_start": format_timestamp(firing.window_start),
        "window_end": format_timestamp(firing.window_end),
        "observed": firing.observed,
        "baseline_center": firing.baseline_mean,
        "baseline_dispersion": firing.baseline_stddev,
        "robust_z": firing.score,
        "detector_version": firing.detector_version,
        "meta": dict(firing.meta),
    }


if __name__ == "__main__":
    main()
