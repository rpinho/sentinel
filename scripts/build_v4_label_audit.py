#!/usr/bin/env python3
"""Finalize a manually reviewed, source-stratified v4 label audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256

DEFAULT_DECISIONS = Path("holdout/v4/label-audit-decisions.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train", "test"), required=True)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidate_path = args.candidates or Path(
        f".cache/v4-audit-candidates-{args.split}.json"
    )
    output_path = args.output or Path(
        f"holdout/v4/label-audit-{args.split}.json"
    )
    candidates = _read_object(candidate_path)
    decisions = _read_object(args.decisions)
    audit = finalize_audit(candidates, decisions, split=args.split)
    _write_json(output_path, audit)
    print(
        "V4_LABEL_AUDIT "
        f"split={args.split} reviewed={audit['counts']['reviewed']} "
        f"rejected={audit['counts']['rejected']} "
        f"audit_sha256={audit['audit_sha256']}"
    )


def finalize_audit(
    candidates: Mapping[str, Any],
    decisions: Mapping[str, Any],
    *,
    split: str,
) -> dict[str, Any]:
    if candidates.get("split") != split:
        raise ValueError("v4 label-audit candidate split mismatch")
    if decisions.get("study_id") != candidates.get("study_id"):
        raise ValueError("v4 label-audit study mismatch")
    split_decisions = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], decisions.get("splits") or {}).get(split) or {},
    )
    if split_decisions.get("candidate_sample_sha256") != candidates.get(
        "sample_sha256"
    ):
        raise ValueError("v4 label-audit decisions do not match frozen sample")
    samples = cast(Sequence[Mapping[str, Any]], candidates.get("samples") or ())
    sample_ids = {str(row["incident_id"]) for row in samples}
    overrides = cast(Mapping[str, Mapping[str, Any]], split_decisions.get("overrides") or {})
    if extra := set(overrides) - sample_ids:
        raise ValueError(f"v4 label-audit overrides are outside sample: {sorted(extra)}")
    default_status = str(decisions["default_status"])
    default_reasons = cast(Mapping[str, str], decisions["default_reasons"])
    reviews: list[dict[str, Any]] = []
    for sample in samples:
        incident_id = str(sample["incident_id"])
        override = overrides.get(incident_id)
        status = str(override["status"] if override else default_status)
        if status not in {"accepted", "ambiguous", "rejected"}:
            raise ValueError(f"invalid v4 label-audit status for {incident_id}")
        source = str(sample["label_source"])
        reviews.append(
            {
                **dict(sample),
                "status": status,
                "reason": str(
                    override["reason"] if override else default_reasons[source]
                ),
            }
        )
    by_source: dict[str, Any] = {}
    for source in sorted({str(row["label_source"]) for row in reviews}):
        source_reviews = [row for row in reviews if row["label_source"] == source]
        counts = {
            status: sum(row["status"] == status for row in source_reviews)
            for status in ("accepted", "ambiguous", "rejected")
        }
        by_source[source] = {
            "reviewed": len(source_reviews),
            **counts,
            "obvious_label_noise_rate": counts["rejected"] / len(source_reviews),
        }
    counts = {
        "reviewed": len(reviews),
        "accepted": sum(row["status"] == "accepted" for row in reviews),
        "ambiguous": sum(row["status"] == "ambiguous" for row in reviews),
        "rejected": sum(row["status"] == "rejected" for row in reviews),
    }
    base = {
        "schema_version": 1,
        "study_id": candidates["study_id"],
        "split": split,
        "reviewer": decisions["reviewer"],
        "policy": decisions["policy"],
        "sampling": candidates["sampling"],
        "requested_per_source": candidates["requested_per_source"],
        "candidate_sample_sha256": candidates["sample_sha256"],
        "counts": counts,
        "by_source": by_source,
        "reviews": reviews,
    }
    return {**base, "audit_sha256": canonical_sha256(base)}


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
