#!/usr/bin/env python3
"""Execute or replay the frozen v5 requests through Claude subscription auth."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import (
    finalize_verdict_bundle,
    read_gzip_json,
    run_claude_subscription_request,
    write_gzip_json,
)

DEFAULT_PROTOCOL = Path("holdout/v5/protocol.json")
DEFAULT_REQUESTS = Path("holdout/v5/requests.json.gz")
DEFAULT_CACHE = Path(".cache/v5/claude-cli")
DEFAULT_OUTPUT = Path("holdout/v5/verdicts.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.retries < 0:
        parser.error("workers must be positive and retries non-negative")

    protocol = _read_object(args.protocol)
    requests = read_gzip_json(args.requests)
    _verify_bundles(protocol, requests)
    if args.live:
        _verify_subscription_auth(protocol)
    request_rows = list(cast(Sequence[Mapping[str, Any]], requests["requests"]))
    if args.limit is not None:
        request_rows = request_rows[: args.limit]
    records: list[dict[str, Any]] = []
    failures: list[tuple[str, str]] = []
    started = time.monotonic()

    def execute(request: Mapping[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(args.retries + 1):
            try:
                return run_claude_subscription_request(
                    request,
                    cache_dir=args.cache_dir,
                    allow_live=args.live,
                )
            except Exception as exc:  # checkpointed retries are intentionally broad
                last_error = exc
                if attempt < args.retries:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_by_request = {
            pool.submit(execute, request): request for request in request_rows
        }
        for completed, future in enumerate(as_completed(future_by_request), start=1):
            request = future_by_request[future]
            try:
                records.append(future.result())
            except Exception as exc:
                failures.append((str(request["row_id"]), str(exc)))
            if completed == 1 or completed % 10 == 0 or completed == len(request_rows):
                elapsed = time.monotonic() - started
                print(
                    "V5_PROGRESS "
                    f"completed={completed}/{len(request_rows)} "
                    f"valid={len(records)} failures={len(failures)} "
                    f"elapsed_seconds={elapsed:.1f}",
                    flush=True,
                )
    if failures:
        detail = "\n".join(f"{row_id}: {error}" for row_id, error in failures[:20])
        raise RuntimeError(f"v5 stage-two failures ({len(failures)}):\n{detail}")
    if len(request_rows) != int(requests["request_count"]):
        print(
            "V5_PARTIAL "
            f"cached={len(records)} total={requests['request_count']} "
            "verdict_bundle_written=false"
        )
        return
    bundle = finalize_verdict_bundle(
        protocol=protocol,
        requests=requests,
        records=records,
    )
    write_gzip_json(args.output, bundle)
    runtime = [cast(Mapping[str, Any], row["runtime"]) for row in records]
    total_cost = sum(float(row.get("total_cost_usd_equivalent") or 0) for row in runtime)
    print(
        "V5_VERDICTS "
        f"count={len(records)} equivalent_cost_usd={total_cost:.4f} "
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
