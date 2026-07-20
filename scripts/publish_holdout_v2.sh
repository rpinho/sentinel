#!/usr/bin/env bash
set -euo pipefail

# This is the intentionally billable first-publish path. Ordinary
# run_holdout_v2.sh executions are exact-fixture-only and cannot call OpenAI.
uv run python scripts/run_holdout.py run \
  --manifest holdout/v2/manifest.json \
  --allow-live-triage \
  "$@"
