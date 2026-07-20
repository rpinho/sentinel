#!/usr/bin/env python3
"""Execute or replay one frozen v5 scheduled-run batch through Claude subscription auth."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import (
    finalize_verdict_bundle,
    read_gzip_json,
    run_claude_subscription_batch,
    write_gzip_json,
)

DEFAULT_PROTOCOL = Path("holdout/v5/protocol.json")
DEFAULT_REQUESTS = Path("holdout/v5/requests.json.gz")
DEFAULT_CACHE = Path(".cache/v5/claude-cli-batch")
DEFAULT_OUTPUT = Path("holdout/v5/verdicts.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("timeout-seconds must be positive")

    protocol = _read_object(args.protocol)
    requests = read_gzip_json(args.requests)
    _verify_bundles(protocol, requests)
    if int(requests.get("invocation_count") or 0) != 1:
        raise RuntimeError("v5 requires exactly one batched agent invocation")
    if args.live:
        _verify_subscription_auth(protocol)
    batch_request = cast(Mapping[str, Any], requests["batch_request"])
    candidate_requests = cast(
        Sequence[Mapping[str, Any]], requests["requests"]
    )
    print(
        "V5_BATCH_START "
        f"candidates={len(candidate_requests)} invocations=1 "
        f"user_chars={requests['batch_user_content_chars']} "
        f"batch_request_hash={batch_request['batch_request_hash']}",
        flush=True,
    )
    record = run_claude_subscription_batch(
        batch_request,
        candidate_requests=candidate_requests,
        cache_dir=args.cache_dir,
        allow_live=args.live,
        timeout_seconds=args.timeout_seconds,
    )
    bundle = finalize_verdict_bundle(
        protocol=protocol,
        requests=requests,
        batch_record=record,
    )
    write_gzip_json(args.output, bundle)
    runtime = cast(Mapping[str, Any], record["runtime"])
    print(
        "V5_VERDICTS "
        f"candidates={len(candidate_requests)} invocations=1 "
        f"wall_seconds={float(runtime['wall_duration_ms']) / 1000:.1f} "
        f"equivalent_cost_usd={float(runtime.get('total_cost_usd_equivalent') or 0):.4f} "
        f"verdict_bundle_sha256={bundle['verdict_bundle_sha256']}"
    )


def _verify_bundles(
    protocol: Mapping[str, Any], requests: Mapping[str, Any]
) -> None:
    if requests.get("protocol_sha256") != canonical_sha256(protocol):
        raise RuntimeError("v5 request bundle protocol mismatch")
    unstamped = {
        key: value
        for key, value in requests.items()
        if key != "request_bundle_sha256"
    }
    if requests.get("request_bundle_sha256") != canonical_sha256(unstamped):
        raise RuntimeError("v5 request bundle hash mismatch")


def _verify_subscription_auth(protocol: Mapping[str, Any]) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
        }
    }
    status = subprocess.run(
        ["claude", "auth", "status"],
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )
    document = json.loads(status.stdout)
    if (
        document.get("loggedIn") is not True
        or document.get("authMethod") != "claude.ai"
        or not document.get("subscriptionType")
    ):
        raise RuntimeError("v5 live run requires Claude subscription authentication")
    expected = str(cast(Mapping[str, Any], protocol["stage2"])["claude_cli_version"])
    version = subprocess.run(
        ["claude", "--version"],
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    ).stdout.split()[0]
    if version != expected:
        raise RuntimeError(f"Claude CLI version {version} differs from frozen {expected}")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object in {path}")
    return raw


if __name__ == "__main__":
    main()
