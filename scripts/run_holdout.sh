#!/usr/bin/env bash
set -euo pipefail

uv run python scripts/run_holdout.py run "$@"
