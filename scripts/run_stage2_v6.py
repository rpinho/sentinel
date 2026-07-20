#!/usr/bin/env python3
"""Execute or replay frozen v6 scheduled-size batches via Claude subscription."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from sentinel.detector_v3 import canonical_sha256
from sentinel.stage2_v5 import (
    read_gzip_json,
    run_claude_subscription_batch,
    write_gzip_json,
)
from sentinel.stage2_v6 import finalize_verdict_bundle

DEFAULT_PROTOCOL = Path("holdout/v6/protocol.json")
DEFAULT_REQUESTS = Path("holdout/v6/requests.json.gz")
DEFAULT_CACHE = Path(".cache/v6/claude-cli-batches")
DEFAULT_OUTPUT = Path("holdout/v6/verdicts.json.gz")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--limit-batches", type=int)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or args.retries < 0 or args.timeout_seconds < 1:
        parser.error("workers/timeout must be positive and retries non-negative")

    protocol = _read_object(args.protocol)
    requests = read_gzip_json(args.requests)
    _verify_bundles(protocol, requests)
    if args.live:
        _verify_subscription_auth(protocol)
    all_requests = {
        str(request["row_id"]): request
        for request in cast(Sequence[Mapping[str, Any]], requests["requests"])
    }
    batches = list(cast(Sequence[Mapping[str, Any]], requests["batches"]))
    if args.limit_batches is not None:
        batches = batches[: args.limit_batches]
    cost_gate = float(cast(Mapping[str, Any], protocol["cost_gate"])["checkpoint_equivalent_usd"])
    records: list[dict[str, Any]] = []
    failures: list[tuple[int, str]] = []
    cumulative_cost = 0.0
    started = time.monotonic()

    def execute(batch: Mapping[str, Any]) -> dict[str, Any]:
        candidates = [all_requests[str(row_id)] for row_id in cast(Sequence[str], batch["row_ids"])]
        last_error: Exception | None = None
        for attempt in range(args.retries + 1):
            try:
                return run_claude_subscription_batch(
                    batch,
                    candidate_requests=candidates,
                    cache_dir=args.cache_dir,
                    allow_live=args.live,
                    timeout_seconds=args.timeout_seconds,
                )
            except Exception as exc:  # resumable exact-hash retry boundary
                last_error = exc
                if attempt < args.retries:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending: dict[Future[dict[str, Any]], Mapping[str, Any]] = {}
        next_index = 0
        while next_index < len(batches) and len(pending) < args.workers:
            batch = batches[next_index]
            pending[pool.submit(execute, batch)] = batch
            next_index += 1
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                batch = pending.pop(future)
                batch_index = int(batch["batch_index"])
                try:
                    record = future.result()
                    records.append(record)
                    cumulative_cost += float(cast(Mapping[str, Any], record["runtime"]).get("total_cost_usd_equivalent") or 0)
                except Exception as exc:
                    failures.append((batch_index, str(exc)))
                completed = len(records) + len(failures)
                print(
                    "V6_PROGRESS "
                    f"batches={completed}/{len(batches)} valid={len(records)} "
                    f"failures={len(failures)} candidates={sum(int(record['candidate_count']) for record in records)} "
                    f"equivalent_usd={cumulative_cost:.3f} "
                    f"elapsed_seconds={time.monotonic() - started:.1f}",
                    flush=True,
                )
                if cumulative_cost >= cost_gate and next_index < len(batches):
                    failures.append((-1, f"equivalent-cost checkpoint reached at ${cumulative_cost:.2f}"))
                    next_index = len(batches)
                elif next_index < len(batches):
                    next_batch = batches[next_index]
                    pending[pool.submit(execute, next_batch)] = next_batch
                    next_index += 1
    if failures:
        detail = "\n".join(f"batch {index}: {error}" for index, error in failures[:20])
        raise RuntimeError(f"v6 batch failures ({len(failures)}):\n{detail}")
    if len(batches) != int(requests["invocation_count"]):
        print(
            f"V6_PARTIAL cached_batches={len(records)} total={requests['invocation_count']} verdict_bundle_written=false"
        )
        return
    records.sort(key=lambda record: int(next(batch["batch_index"] for batch in batches if batch["batch_request_hash"] == record["batch_request_hash"])))
    bundle = finalize_verdict_bundle(
        protocol=protocol,
        requests=requests,
        batch_records=records,
    )
    write_gzip_json(args.output, bundle)
    print(
        "V6_VERDICTS "
        f"candidates={len(bundle['records'])} invocations={len(records)} "
        f"equivalent_cost_usd={cumulative_cost:.4f} "
        f"verdict_bundle_sha256={bundle['verdict_bundle_sha256']}"
    )


def _verify_bundles(protocol: Mapping[str, Any], requests: Mapping[str, Any]) -> None:
    if requests.get("protocol_sha256") != canonical_sha256(protocol):
        raise RuntimeError("v6 requests do not match protocol")
    unstamped = {key: value for key, value in requests.items() if key != "request_bundle_sha256"}
    if requests.get("request_bundle_sha256") != canonical_sha256(unstamped):
        raise RuntimeError("v6 request bundle hash mismatch")


def _verify_subscription_auth(protocol: Mapping[str, Any]) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"}
    }
    status = subprocess.run(
        ["claude", "auth", "status"],
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )
    document = json.loads(status.stdout)
    if document.get("loggedIn") is not True or document.get("authMethod") != "claude.ai" or not document.get("subscriptionType"):
        raise RuntimeError("v6 requires Claude subscription authentication")
    expected = str(cast(Mapping[str, Any], protocol["stage2"])["claude_cli_version"])
    actual = subprocess.run(
        ["claude", "--version"],
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    ).stdout.split()[0]
    if actual != expected:
        raise RuntimeError(f"Claude CLI version {actual} differs from frozen {expected}")


def _read_object(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected object in {path}")
    return raw


if __name__ == "__main__":
    main()
